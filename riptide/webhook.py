#!/usr/bin/env python3
"""
webhook.py — FastAPI webhook receiver for Riptide.

Handles GitHub App webhook events from the octopus-selfhost app (ID 4262983):
  - pull_request (opened, reopened, synchronize)  → enqueue review
  - issue_comment (@mention)                     → enqueue review
  - pull_request (closed, merged)               → enqueue incremental index
  - installation / installation_repositories    → sync repo list

No `gh` CLI. Pure JWT auth via github_app.py.
"""

import os, json, logging, traceback, subprocess, shlex, tempfile
from pathlib import Path
from typing import Optional
from queue import Queue
from threading import Thread
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request, Response, HTTPException
from pydantic import BaseModel

from .github_app import verify_webhook_signature, GitHubAppClient
from .review_worker import enqueue_review, enqueue_index

# Companion is optional — silently unavailable if RIPTIDE_COMPANION_REPOS is unset
_companion = None


def get_companion():
    global _companion
    if _companion is None:
        try:
            from .companion import Companion

            _companion = Companion(github_client() if GITHUB_PRIVATE_KEY_PATH else None)
        except Exception as e:
            log.warning("Companion not available: %s", e)
            _companion = False  # sentinel — don't retry
    return _companion if _companion else None


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("riptide.webhook")

app = FastAPI(title="Riptide Webhook Server")

# ── Config ─────────────────────────────────────────────────────────────────────

GITHUB_APP_ID = int(os.environ.get("GITHUB_APP_ID", "4262983"))
GITHUB_PRIVATE_KEY_PATH = os.environ.get("GITHUB_PRIVATE_KEY_PATH", "")
WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")

# Path to Riptide's own SQLite metadata DB
DATA_DIR = Path(os.environ.get("RIPTIDE_DATA_DIR", "/tmp/riptide"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
METADATA_DB = DATA_DIR / "metadata.db"

# ── GitHub client factory ───────────────────────────────────────────────────────

_github_client: Optional[GitHubAppClient] = None


def github_client() -> GitHubAppClient:
    global _github_client
    if _github_client is None:
        if not GITHUB_PRIVATE_KEY_PATH:
            raise RuntimeError("GITHUB_PRIVATE_KEY_PATH not set")
        _github_client = GitHubAppClient(GITHUB_APP_ID, GITHUB_PRIVATE_KEY_PATH)
    return _github_client


# ── State tracking for Riptide-acted PRs ─────────────────────────────────────
# Tracks which PRs Riptide has modified, so we can correlate check_run events
# with our actions and trigger retries on failure.
_RIPTIDE_ACTED_PRCS: set[str] = set()


def _track_acted_pr(owner: str, repo: str, pr_number: int):
    """Record that Riptide acted on this PR (for check_run correlation)."""
    key = f"{owner}/{repo}#{pr_number}"
    _RIPTIDE_ACTED_PRCS.add(key)
    # Persist to disk so restarts don't lose state
    try:
        state_file = DATA_DIR / "riptide_acted_prs.json"
        state_file.write_text(json.dumps(list(_RIPTIDE_ACTED_PRCS)))
    except Exception:
        pass


def _has_acted_on_pr(owner: str, repo: str, pr_number: int) -> bool:
    """Check if Riptide has acted on this PR."""
    key = f"{owner}/{repo}#{pr_number}"
    return key in _RIPTIDE_ACTED_PRCS


def _load_acted_prs():
    """Load persisted state on startup."""
    global _RIPTIDE_ACTED_PRCS
    try:
        state_file = DATA_DIR / "riptide_acted_prs.json"
        if state_file.exists():
            _RIPTIDE_ACTED_PRCS = set(json.loads(state_file.read_text()))
    except Exception:
        pass

job_queue: Queue = Queue(maxsize=1)  # 1 = serialise reviews to avoid rate limits


def start_worker():
    """Start the background review worker thread."""
    from .review_worker import process_jobs

    t = Thread(
        target=process_jobs, args=(job_queue,), daemon=True, name="review-worker"
    )
    t.start()
    log.info("Review worker started")


# ── Webhook handler ────────────────────────────────────────────────────────────


@app.post("/webhook/github")
async def github_webhook(request: Request) -> Response:
    """
    Main GitHub webhook endpoint.
    Matches the behavior of Octopus's /api/github/webhook route.ts.
    """
    # 1. Verify signature
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")
    event = request.headers.get("x-github-event", "")
    delivery_id = request.headers.get("x-github-delivery", "unknown")

    if not verify_webhook_signature(body, signature, WEBHOOK_SECRET):
        log.warning(f"[{delivery_id}] Invalid webhook signature from {request.client}")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body)
    log.info(f"[{delivery_id}] {event} event received")

    try:
        if event == "pull_request":
            return await handle_pull_request(payload, delivery_id)
        elif event == "issue_comment":
            return await handle_issue_comment(payload, delivery_id)
        elif event == "pull_request_review":
            return await handle_pull_request_review(payload, delivery_id)
        elif event == "check_run":
            return await handle_check_run(payload, delivery_id)
        elif event == "workflow_run":
            return await handle_workflow_run(payload, delivery_id)
        elif event in ("installation", "installation_repositories"):
            return await handle_installation(payload, event, delivery_id)
        else:
            log.info(f"[{delivery_id}] Unhandled event: {event}")
            return Response(status_code=200)
    except Exception as e:
        log.error(
            f"[{delivery_id}] Error handling {event}: {e}\n{traceback.format_exc()}"
        )
        # Return 200 to prevent GitHub retry storms on our errors
        return Response(status_code=200)


async def handle_pull_request(payload: dict, delivery_id: str) -> Response:
    """Handle pull_request events — enqueue review or incremental index."""
    action = payload.get("action", "")
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})

    owner = repo.get("full_name", "").split("/")[0] if repo.get("full_name") else ""
    repo_name = repo.get("name", "")
    repo_full = repo.get("full_name", "")
    pr_number = pr.get("number")
    head_sha = pr.get("head", {}).get("sha", "")
    is_merged = pr.get("merged", False)

    installation_id = installation.get("id")
    if not installation_id:
        log.info(f"[{delivery_id}] No installation ID, skipping")
        return Response(status_code=200)

    # Clear retry count on new commits (user or agent pushed changes)
    if action == "synchronize" and pr_number:
        _clear_retry_count(f"{owner}/{repo_name}#{pr_number}")

    log.info(
        f"[{delivery_id}] PR {action}: {repo_full}#{pr_number} head_sha={head_sha[:7]}"
    )

    # PR opened/reopened/synchronize → start review
    if action in ("opened", "reopened", "synchronize"):
        job = {
            "type": "review",
            "installation_id": installation_id,
            "owner": owner,
            "repo": repo_name,
            "repo_full": repo_full,
            "pr_number": pr_number,
            "pr_title": pr.get("title", f"PR #{pr_number}"),
            "pr_author": pr.get("user", {}).get("login", "unknown"),
            "head_sha": head_sha,
            "delivery_id": delivery_id,
        }
        try:
            enqueue_review(job_queue, job)
            log.info(f"[{delivery_id}] Review enqueued for {repo_full}#{pr_number}")
        except Exception as e:
            log.error(f"[{delivery_id}] Failed to enqueue review: {e}")

        # ── Companion TLDR (opt-in, non-blocking, background thread) ──
        companion = get_companion()
        if companion and companion.is_active_for(owner, repo_name):
            changed = []
            try:
                # Fetch changed files for companion context (lightweight)
                from threading import Thread

                t = Thread(
                    target=companion.run_for_pr,
                    args=(
                        installation_id,
                        owner,
                        repo_name,
                        pr_number,
                        pr.get("title", f"PR #{pr_number}"),
                        pr.get("user", {}).get("login", "unknown"),
                        [],  # changed_files will be fetched inside companion thread
                    ),
                    daemon=True,
                    name=f"companion-{repo_name}-{pr_number}",
                )
                t.start()
                log.info(
                    f"[{delivery_id}] Companion thread spawned for {repo_full}#{pr_number}"
                )
            except Exception as e:
                log.warning(f"[{delivery_id}] Companion launch failed: {e}")

    # PR merged → incremental index
    elif action == "closed" and is_merged:
        # Fetch changed files list via GitHub API
        try:
            files = github_client().get_pr_files(
                installation_id, owner, repo_name, pr_number
            )
            file_list = [
                {"filename": f["filename"], "status": f["status"]} for f in files
            ]
            job = {
                "type": "incremental_index",
                "installation_id": installation_id,
                "owner": owner,
                "repo": repo_name,
                "repo_full": repo_full,
                "pr_number": pr_number,
                "changed_files": file_list,
                "delivery_id": delivery_id,
            }
            enqueue_index(job_queue, job)
            log.info(
                f"[{delivery_id}] Incremental index enqueued for {repo_full}#{pr_number}"
            )
        except Exception as e:
            log.error(f"[{delivery_id}] Failed to enqueue incremental index: {e}")

    return Response(status_code=200)


async def handle_issue_comment(payload: dict, delivery_id: str) -> Response:
    """Handle issue_comment events — @mention triggers review."""
    action = payload.get("action", "")
    if action != "created":
        return Response(status_code=200)

    comment = payload.get("comment", {})
    body = comment.get("body", "")
    issue = payload.get("issue", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})

    # Check if this is a PR (not a plain issue)
    is_pr = bool(issue.get("pull_request"))
    mentions_riptide = bool(
        is_pr and ("@riptide" in body.lower() or "@octopus" in body.lower())
    )

    # Skip own comments (posted by the bot)
    via_app = comment.get("performed_via_github_app", {})
    app_slug = os.environ.get("GITHUB_APP_SLUG", "octopus-selfhost")
    own_app_id = str(GITHUB_APP_ID)
    if via_app.get("id") and str(via_app.get("id")) == own_app_id:
        log.info(f"[{delivery_id}] Skipping own comment")
        return Response(status_code=200)
    if comment.get("user", {}).get("type") == "Bot":
        bot_login = comment.get("user", {}).get("login", "").lower()
        if bot_login == f"{app_slug}[bot]":
            log.info(f"[{delivery_id}] Skipping own bot comment: {bot_login}")
            return Response(status_code=200)

    if not mentions_riptide:
        return Response(status_code=200)

    owner = repo.get("full_name", "").split("/")[0] if repo.get("full_name") else ""
    repo_name = repo.get("name", "")
    repo_full = repo.get("full_name", "")
    pr_number = issue.get("number")
    comment_id = comment.get("id")
    installation_id = installation.get("id")

    if not installation_id:
        return Response(status_code=200)

    log.info(
        f"[{delivery_id}] @mention in {repo_full}#{pr_number}, comment_id={comment_id}"
    )

    # Add 👀 reaction
    try:
        github_client().add_comment_reaction(
            installation_id, owner, repo_name, comment_id, "eyes"
        )
    except Exception as e:
        log.warning(f"[{delivery_id}] Could not add reaction: {e}")

    # Enqueue review
    job = {
        "type": "review",
        "installation_id": installation_id,
        "owner": owner,
        "repo": repo_name,
        "repo_full": repo_full,
        "pr_number": pr_number,
        "pr_title": issue.get("title", f"PR #{pr_number}"),
        "pr_author": issue.get("user", {}).get("login", "unknown"),
        "head_sha": "",
        "trigger_comment_id": comment_id,
        "trigger_comment_body": body,
        "delivery_id": delivery_id,
    }
    try:
        enqueue_review(job_queue, job)
    except Exception as e:
        log.error(f"[{delivery_id}] Failed to enqueue review: {e}")

    return Response(status_code=200)


async def handle_pull_request_review(payload: dict, delivery_id: str) -> Response:
    """Handle pull_request_review events — check for 'Need Action' label → spawn Hermes session."""
    action = payload.get("action", "")
    if action != "submitted":
        return Response(status_code=200)

    review = payload.get("review", {})
    state = review.get("state", "")  # "approved", "changes_requested", "commented"
    if state not in ("changes_requested", "commented"):
        return Response(status_code=200)

    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})

    # Check for "Need Action" label (case-insensitive)
    labels = pr.get("labels", [])
    action_phrases = (
        "need action",
        "needs action",
        "need-action",
        "needs-action",
        "action needed",
        "action-required",
    )
    has_need_action = any(
        label.get("name", "").lower() in action_phrases for label in labels
    )
    if not has_need_action:
        return Response(status_code=200)

    owner = repo.get("owner", {}).get("login", "")
    repo_name = repo.get("name", "")
    pr_number = pr.get("number")
    review_body = review.get("body", "")
    reviewer = review.get("user", {}).get("login", "unknown")

    log.info(
        f"[{delivery_id}] Need Action on {owner}/{repo_name}#{pr_number} "
        f"by {reviewer}: '{review_body[:200]}'"
    )

    _spawn_hermes_session(owner, repo_name, pr_number, review_body, reviewer)
    return Response(status_code=200)


async def handle_installation(payload: dict, event: str, delivery_id: str) -> Response:
    """Handle installation events — sync repo list."""
    action = payload.get("action", "")
    installation = payload.get("installation", {})
    installation_id = installation.get("id")

    if event == "installation" and action == "deleted":
        log.info(f"[{delivery_id}] App uninstalled, installation_id={installation_id}")
        # Remove from metadata DB
        import sqlite3

        with sqlite3.connect(METADATA_DB) as conn:
            conn.execute("DELETE FROM installations WHERE id = ?", (installation_id,))
        return Response(status_code=200)

    if not installation_id:
        return Response(status_code=200)

    # Sync repos
    try:
        client = github_client()
        repos = client.get_installation_repos(installation_id)
        import sqlite3

        with sqlite3.connect(METADATA_DB) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO installations (id, account_login, created_at)
                VALUES (?, ?, ?)
            """,
                (
                    installation_id,
                    installation.get("account", {}).get("login", ""),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            for r in repos:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO repositories
                    (id, full_name, name, default_branch, installation_id, is_active)
                    VALUES (?, ?, ?, ?, ?, 1)
                """,
                    (
                        r["id"],
                        r["full_name"],
                        r["name"],
                        r.get("default_branch", "main"),
                        installation_id,
                    ),
                )
        log.info(
            f"[{delivery_id}] Synced {len(repos)} repos for installation {installation_id}"
        )
    except Exception as e:
        log.error(f"[{delivery_id}] Installation sync failed: {e}")

    return Response(status_code=200)


async def handle_check_run(payload: dict, delivery_id: str) -> Response:
    """Handle check_run events — detect CI failures on PRs we've acted on."""
    if payload.get("action") != "completed":
        return Response(status_code=200)

    check_run = payload.get("check_run", {})
    conclusion = check_run.get("conclusion", "")
    if conclusion not in ("failure", "timed_out", "cancelled"):
        return Response(status_code=200)

    prs = check_run.get("pull_requests", [])
    if not prs:
        return Response(status_code=200)

    repo = payload.get("repository", {})
    owner = repo.get("owner", {}).get("login", "")
    repo_name = repo.get("name", "")

    for pr_data in prs:
        pr_number = pr_data.get("number")
        if not pr_number:
            continue
        if not _has_acted_on_pr(owner, repo_name, pr_number):
            continue

        retry_key = f"{owner}/{repo_name}#{pr_number}"
        retry_count = _get_retry_count(retry_key)
        if retry_count >= 3:
            log.warning(f"[{delivery_id}] Max retries for {retry_key}")
            continue

        log.info(f"[{delivery_id}] CI failed {retry_key} attempt {retry_count + 1}/3")
        _spawn_retry_session(
            owner, repo_name, pr_number,
            check_run.get("name", "CI"), conclusion,
            check_run.get("output", {}).get("summary", "No output"),
        )

    return Response(status_code=200)


async def handle_workflow_run(payload: dict, delivery_id: str) -> Response:
    """
    Handle workflow_run events — detect GitHub Actions CI failures on PRs
    we've acted on. This is the primary mechanism for GitHub Actions.
    """
    if payload.get("action") != "completed":
        return Response(status_code=200)

    workflow_run = payload.get("workflow_run", {})
    conclusion = workflow_run.get("conclusion", "")
    if conclusion not in ("failure", "timed_out", "cancelled"):
        return Response(status_code=200)

    prs = workflow_run.get("pull_requests", [])
    if not prs:
        return Response(status_code=200)

    repo = payload.get("repository", {})
    owner = repo.get("owner", {}).get("login", "")
    repo_name = repo.get("name", "")

    for pr_data in prs:
        pr_number = pr_data.get("number")
        if not pr_number:
            continue
        if not _has_acted_on_pr(owner, repo_name, pr_number):
            continue

        retry_key = f"{owner}/{repo_name}#{pr_number}"
        retry_count = _get_retry_count(retry_key)
        if retry_count >= 3:
            log.warning(f"[{delivery_id}] Max retries for {retry_key}")
            continue

        log.info(f"[{delivery_id}] Workflow failed {retry_key} attempt {retry_count + 1}/3")
        _spawn_retry_session(
            owner, repo_name, pr_number,
            workflow_run.get("name", "CI"), conclusion,
            workflow_run.get("head_commit", {}).get("message", "No output"),
        )

    return Response(status_code=200)


def _get_retry_count(key: str) -> int:
    try:
        f = DATA_DIR / "riptide_retries.json"
        if f.exists():
            return json.loads(f.read_text()).get(key, 0)
    except Exception:
        pass
    return 0

def _increment_retry_count(key: str):
    """Increment the retry count for a PR."""
    try:
        f = DATA_DIR / "riptide_retries.json"
        data = json.loads(f.read_text()) if f.exists() else {}
        data[key] = data.get(key, 0) + 1
        f.write_text(json.dumps(data))
    except Exception:
        pass


def _clear_retry_count(key: str):
    """Clear retry count for a PR (called on new commits / push events)."""
    try:
        f = DATA_DIR / "riptide_retries.json"
        if f.exists():
            data = json.loads(f.read_text())
            if key in data:
                del data[key]
                f.write_text(json.dumps(data))
    except Exception:
        pass


def _spawn_retry_session(owner, repo, pr_number, check_name, conclusion, output_summary):
    """Spawn a deep-think retry session when CI fails after our changes."""
    hermes_bin = _find_hermes_bin()
    if not hermes_bin:
        return

    retry_key = f"{owner}/{repo}#{pr_number}"
    retry_count = _get_retry_count(retry_key)
    delay_minutes = [2, 5, 10][min(retry_count, 2)]
    run_at = (datetime.now() + timedelta(minutes=delay_minutes)).strftime("%Y-%m-%dT%H:%M:%S")

    prompt = (
        f"PR #{pr_number} in {owner}/{repo} has FAILING CI after Riptide made changes.\n\n"
        f"FAILING CHECK: {check_name}\nCONCLUSION: {conclusion}\nOUTPUT: {output_summary}\n"
        f"RETRY ATTEMPT: {retry_count + 1}/3\n\n"
        f"ROLE: Senior engineer diagnosing test failures. Use deep-think skill.\n\n"
        f"DIAGNOSIS LOOP:\n"
        f"1. IDENTIFY — Which tests failed? Exact error messages?\n"
        f"   gh pr checks {pr_number} --repo {owner}/{repo}\n"
        f"2. LOCALIZE — Which files/functions implicated? Use graphify.\n"
        f"3. ROOT CAUSE — Why did tests fail? Bug in our change? Broken assumption?\n"
        f"4. FIX — Minimal correct fix. Don't just make tests pass.\n"
        f"5. VERIFY — Run failing tests locally before pushing.\n"
        f"6. ESCALATE — If can't fix, comment explaining what you tried.\n\n"
        f"REPO: ~/workspace/{repo}/\n"
    )

    cmd = [
        hermes_bin, "cron", "create", run_at,
        "--name", f"riptide-retry-{owner}-{repo}-{pr_number}-{retry_count + 1}",
        "--skill", "github-pr-lifecycle",
        "--skill", "deep-think",
    ]

    try:
        result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            _increment_retry_count(retry_key)
            log.info(f"Retry spawned: {result.stdout[:200]}")
        else:
            log.error(f"Retry failed: {result.stderr[:200]}")
    except Exception as e:
        log.error(f"Retry error: {e}")
async def health() -> dict:
    return {"status": "ok", "app": "riptide"}


# ── Hermes session spawner ───────────────────────────────────────────────────


def _spawn_hermes_session(
    owner: str, repo: str, pr_number: int, review_body: str, reviewer: str
):
    """
    Spawn a one-shot Hermes cron session to autonomously address PR review feedback.

    The session runs as a scheduled job ~1 minute in the future with the
    github-pr-lifecycle skill loaded. The output is delivered to the default
    delivery target (usually Discord).
    """
    hermes_bin = _find_hermes_bin()
    if not hermes_bin:
        log.error("hermes binary not found — cannot spawn session")
        return

    # Schedule 2 minutes from now (use local time — cron parser expects local tz)
    run_at = (datetime.now() + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")

    prompt = (
        f"PR #{pr_number} in {owner}/{repo} received a review from {reviewer} "
        f"requesting changes. The review says: {review_body}.\n\n"
        f"ROLE: You are a senior engineer addressing code review feedback. "
        f"You must use the deep-think skill (load with skill_view('deep-think')) "
        f"to reason about this feedback before making any code changes.\n\n"
        f"MANDATORY DEEPTHINK LOOP:\n"
        f"1. SURFACE — Fetch the PR diff, review comments, and inline comments.\n"
        f"   Read everything the reviewer said. Restate the feedback in your own words.\n"
        f"2. EXPLORE — What code paths are affected? What assumptions does the\n"
        f"   reviewer's feedback reveal? Are there edge cases?\n"
        f"3. CHALLENGE — Could the proposed fix have unintended side effects?\n"
        f"   Is there a simpler approach? What tests could break?\n"
        f"4. SYNTHESIZE — Design the fix. Plan which files to change and why.\n"
        f"5. EMPIRICAL VALIDATION — After applying changes, run the test suite.\n"
        f"   Verify the fix actually works before pushing.\n"
        f"6. VERIFY — Check CI after pushing. If tests fail, diagnose and fix.\n"
        f"   Then request re-gate by commenting on the PR mentioning the reviewer.\n\n"
        f"UI CHANGES: If the PR modifies UI files (*.css, *.scss, *.less, *.html, *.jsx,\n"
        f"*.tsx, *.vue, *.svelte, *.astro, *.svg, *.png, *.jpg, *.gif, *.webp), you MUST run\n"
        f"ProofShot visual verification BEFORE and AFTER applying fixes:\n"
        f"  proofshot start → test the UI → proofshot stop → proofshot pr {pr_number}\n\n"
        f"REPO CONTEXT: The repository is at ~/workspace/{repo}/.\n"
        f"Use the codebase and git history to understand the full context.\n\n"
        f"PREFERRED MODEL: If possible, use custom:LongCat with LongCat-2.0 "
        f"for reasoning. If quota is exceeded, fall back to default model."
    )

    # Track that we acted on this PR (for check_run retry correlation)
    _track_acted_pr(owner, repo, pr_number)

    cmd = [
        hermes_bin,
        "cron",
        "create",
        run_at,
        "--name",
        f"riptide-pr-{owner}-{repo}-{pr_number}",
        "--skill",
        "github-pr-lifecycle",
        "--skill",
        "deep-think",
    ]

    log.info(
        f"Spawning Hermes session for {owner}/{repo}#{pr_number}: {' '.join(shlex.quote(c) for c in cmd)}"
    )

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            log.error(
                f"Failed to spawn Hermes session for {owner}/{repo}#{pr_number}: "
                f"stdout={result.stdout[:300]} stderr={result.stderr[:300]}"
            )
        else:
            log.info(
                f"Spawned Hermes session for {owner}/{repo}#{pr_number}: "
                f"{result.stdout[:300]}"
            )
    except subprocess.TimeoutExpired:
        log.warning(f"Timeout spawning Hermes session for {owner}/{repo}#{pr_number}")
    except Exception as e:
        log.error(f"Error spawning Hermes session: {e}")


def _find_hermes_bin() -> str | None:
    """Locate the hermes binary. Checks PATH then known install locations."""
    import shutil

    path = shutil.which("hermes")
    if path:
        return path
    candidates = [
        "/home/sc/.hermes/hermes-agent/venv/bin/hermes",
        "/home/sc/.local/bin/hermes",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


# ── Init DB on startup ─────────────────────────────────────────────────────────


@app.on_event("startup")
def init_db():
    _load_acted_prs()
    import sqlite3

    with sqlite3.connect(METADATA_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS installations (
                id INTEGER PRIMARY KEY,
                account_login TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS repositories (
                id INTEGER PRIMARY KEY,
                full_name TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                default_branch TEXT DEFAULT 'main',
                installation_id INTEGER,
                is_active INTEGER DEFAULT 1,
                last_indexed_at TEXT,
                FOREIGN KEY (installation_id) REFERENCES installations(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_full TEXT,
                pr_number INTEGER,
                status TEXT,
                enqueued_at TEXT,
                completed_at TEXT,
                findings_count INTEGER,
                error TEXT
            )
        """)
    start_worker()
    log.info(f"Metadata DB ready at {METADATA_DB}")
