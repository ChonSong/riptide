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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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


# ── In-process job queue ────────────────────────────────────────────────────────

job_queue: Queue = Queue(maxsize=1)  # 1 = serialise reviews to avoid rate limits

def start_worker():
    """Start the background review worker thread."""
    from .review_worker import process_jobs
    t = Thread(target=process_jobs, args=(job_queue,), daemon=True, name="review-worker")
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
        elif event in ("installation", "installation_repositories"):
            return await handle_installation(payload, event, delivery_id)
        else:
            log.info(f"[{delivery_id}] Unhandled event: {event}")
            return Response(status_code=200)
    except Exception as e:
        log.error(f"[{delivery_id}] Error handling {event}: {e}\n{traceback.format_exc()}")
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

    log.info(f"[{delivery_id}] PR {action}: {repo_full}#{pr_number} head_sha={head_sha[:7]}")

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

    # PR merged → incremental index
    elif action == "closed" and is_merged:
        # Fetch changed files list via GitHub API
        try:
            files = github_client().get_pr_files(installation_id, owner, repo_name, pr_number)
            file_list = [{"filename": f["filename"], "status": f["status"]} for f in files]
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
            log.info(f"[{delivery_id}] Incremental index enqueued for {repo_full}#{pr_number}")
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
        is_pr and
        ("@riptide" in body.lower() or "@octopus" in body.lower())
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

    log.info(f"[{delivery_id}] @mention in {repo_full}#{pr_number}, comment_id={comment_id}")

    # Add 👀 reaction
    try:
        github_client().add_comment_reaction(installation_id, owner, repo_name, comment_id, "eyes")
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
    action_phrases = ("need action", "needs action", "need-action", "needs-action", "action needed", "action-required")
    has_need_action = any(
        label.get("name", "").lower() in action_phrases
        for label in labels
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
            conn.execute("""
                INSERT OR REPLACE INTO installations (id, account_login, created_at)
                VALUES (?, ?, ?)
            """, (installation_id, installation.get("account", {}).get("login", ""), datetime.now(timezone.utc).isoformat()))
            for r in repos:
                conn.execute("""
                    INSERT OR REPLACE INTO repositories
                    (id, full_name, name, default_branch, installation_id, is_active)
                    VALUES (?, ?, ?, ?, ?, 1)
                """, (r["id"], r["full_name"], r["name"], r.get("default_branch", "main"), installation_id))
        log.info(f"[{delivery_id}] Synced {len(repos)} repos for installation {installation_id}")
    except Exception as e:
        log.error(f"[{delivery_id}] Installation sync failed: {e}")

    return Response(status_code=200)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": "riptide"}


# ── Hermes session spawner ───────────────────────────────────────────────────

def _spawn_hermes_session(owner: str, repo: str, pr_number: int, review_body: str, reviewer: str):
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
        f"requesting changes. The review says: {review_body}. "
        f"Address this feedback: fetch the PR diff and comments using gh, "
        f"understand what the reviewer requested, make the necessary code changes, "
        f"commit with a descriptive message referencing the PR, and push. "
        f"Reply to the review thread to confirm the changes."
    )

    cmd = [
        hermes_bin, "cron", "create", run_at,
        "--name", f"riptide-pr-{owner}-{repo}-{pr_number}",
        "--skill", "github-pr-lifecycle",
    ]

    log.info(f"Spawning Hermes session for {owner}/{repo}#{pr_number}: {' '.join(shlex.quote(c) for c in cmd)}")

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
