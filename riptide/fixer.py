#!/usr/bin/env python3
"""
fixer.py — @riptide-bot fix: autonomous fix command (Bot 2 family).

Handles on-demand `@riptide-bot fix [description]` commands via
handle_fix_command(), called directly from webhook.py when a user
comments the command on a PR.

Unlike @riptide-bot review (analysis only), the spawned fix session can
edit files, commit, and push fixes to the PR's branch — gated by
_is_push_eligible() so we never push to foreign repos or fork PRs.

Safety gates (locked design, 2026-08-02):
  - Push allowed only when we own the repo (ChonSong org) or authored the PR.
  - Fork PRs: comment-only patch, never a push attempt.
  - No force-push. No rewriting pushed history.
  - Fix session must run tests before pushing; no push on red.

Uses `gh` CLI (already authenticated as ChonSong) inside the spawned
session for git push — same identity used for posting reviews.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

import structlog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = structlog.get_logger("riptide.fixer")

# ── Config ───────────────────────────────────────────────────────────────────

# Matches `@riptide-bot fix` plus an optional free-text description.
# The capture group (.*) with DOTALL grabs multi-line descriptions.
FIX_RE = re.compile(r"@riptide-bot\s+fix\b(.*)", re.IGNORECASE | re.DOTALL)

OUR_USERNAME = os.environ.get("RIPTIDE_OUR_USERNAME", "ChonSong")
OUR_ORG = os.environ.get("RIPTIDE_OUR_ORG", "ChonSong")
# Pin the inference config on spawned cron jobs — the global config drifts,
# and unpinned jobs are skipped to prevent unintended spend (Hermes #44585).
# Mirrors deepthink.py's DEEPTHINK_MODEL/DEEPTHINK_PROVIDER.
FIX_MODEL = os.environ.get("RIPTIDE_FIX_MODEL", "LongCat-2.0")
FIX_PROVIDER = os.environ.get("RIPTIDE_FIX_PROVIDER", "longcat")

# Workspace root for spawned sessions (PYTHONPATH pitfall — spawned
# sessions have no PYTHONPATH; the prompt must insert this path).
WORKSPACE_ROOT = os.environ.get("RIPTIDE_WORKSPACE_ROOT", "/home/sc/workspace")


def _with_db_retry(fn, max_retries=3, base_delay=0.5):
    """Execute fn() with retry on SQLite 'database is locked' errors.

    Uses exponential backoff. Returns fn()'s result or raises after max_retries.
    """
    for attempt in range(max_retries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "database is locked" not in str(e).lower() or attempt == max_retries - 1:
                raise
            wait_time = base_delay * (2 ** attempt)
            log.info(f"DB locked, retrying in {wait_time}s (attempt {attempt+1}/{max_retries})")
            time.sleep(wait_time)


def handle_fix_command(
    client,
    installation_id: int | None,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
    description: str = "",
    delivery_id: str = "",
) -> str | None:
    """Handle @riptide-bot fix command — spawn an on-demand fix session.

    Called from webhook.py when a user comments @riptide-bot fix on a PR.
    Fetches PR details via the GitHub API client, checks push eligibility,
    spawns the fix session (or queues if busy), and returns a user-facing
    confirmation message (or error message).

    description: optional free-text after `fix` (already stripped).
    delivery_id: trace ID from the originating webhook event (for log correlation).
    """
    # ── Structlog trace binding ───────────────────────────────────────────────
    # If delivery_id is provided (from webhook), bind it so all downstream
    # log lines automatically include it. This enables end-to-end tracing
    # from webhook → fixer → Hermes cron → subagent.
    if delivery_id:
        structlog.contextvars.bind_contextvars(
            delivery_id=delivery_id,
            worker="fixer",
            pr=f"{owner}/{repo}#{pr_number}",
        )
    # Module-level log (line 38) already bound with delivery_id via structlog.contextvars

    try:
        pr_details = client.get_pr_details(installation_id, owner, repo, pr_number)
    except Exception as e:
        log.warning("Failed to fetch PR details for fix: %s", e)
        return (
            f"⚠️ Could not fetch PR #{pr_number} details ({e}). "
            f"Make sure the PR exists and the app is installed."
        )

    title = pr_details.get("title", f"PR #{pr_number}")
    author = pr_details.get("user", {}).get("login", "unknown")
    additions = pr_details.get("additions", 0)
    deletions = pr_details.get("deletions", 0)
    total_loc = additions + deletions
    head_sha = pr_details.get("head", {}).get("sha", "")
    head_ref = pr_details.get("head", {}).get("ref", "")
    # Fork detection: fail-closed — if head.repo is missing (deleted fork,
    # API race) or differs from base, treat as fork.
    head_repo = (pr_details.get("head", {}).get("repo") or {}).get("full_name", "")
    is_fork = head_repo.lower() != f"{owner}/{repo}".lower() if head_repo else True

    # Authorization gate: the COMMENTER must be the PR author, the repo owner,
    # or OUR_USERNAME.
    authorized = (
        commenter == OUR_USERNAME
        or commenter == author
        or commenter == owner
    )
    if not authorized:
        log.warning(
            "Unauthorized fix attempt by %s on %s/%s#%d (author=%s, owner=%s)",
            commenter, owner, repo, pr_number, author, owner,
        )
        return (
            f"🚫 **Not authorized.** Only the PR author (@{author}), the repo "
            f"owner (@{owner}), or @{OUR_USERNAME} can trigger `@riptide-bot fix` "
            f"on this PR. Your comment was logged."
        )

    # Push eligibility: allow if we own the repo OR authored the PR.
    # Fork PRs from external users (head != base, not our org) stay comment-only.
    # Fork PRs authored by us are push-eligible (we own the head branch).
    push_eligible = _is_push_eligible(owner, repo, author) and _is_fork_push_eligible(is_fork, author)

    pr_key = f"{owner}/{repo}#{pr_number}"

    # Check if there's already a fix running for this PR — with retry on lock contention
    from riptide.state import StateStore

    def check_and_queue():
        state = StateStore()
        # Use global gate OR PR-specific queue (AND logic: must check both to avoid races)
        if state.has_running_fix() or state.get_queue_length(pr_number, owner=owner, repo=repo) > 0:
            queue_id = state.enqueue_fix(pr_number, pr_key, commenter, description.strip(), installation_id=installation_id, owner=owner, repo=repo)
            position = state.get_queue_position(queue_id)
            running_pr = state.get_running_fix_pr()
            return queue_id, position, running_pr
        return None, None, None

    try:
        result = _with_db_retry(check_and_queue)
    except sqlite3.OperationalError:
        log.error(f"DB locked after retries for #{pr_number}")
        return f"⚠️ Database temporarily locked for #{pr_number}. Please retry in a few seconds."
    
    queue_id, position, running_pr = result

    if queue_id is not None:
        if running_pr and running_pr != pr_number:
            # Another PR's fix is running — queue globally
            return (
                f"⏳ **Fix queued for #{pr_number}.**\n\n"
                f"Another fix is currently running for PR #{running_pr}. "
                f"Your request has been added to the queue (position: {position}).\n\n"
                f"**Scope:** {description.strip() or 'all outstanding review findings'}\n"
                f"**Estimated start:** after the current fix completes (~5-15 min)."
            )
        else:
            # Same PR fix running or queued
            return (
                f"⏳ **Fix queued for #{pr_number}.**\n\n"
                f"A fix is already in progress for this PR. "
                f"Your request has been added to the queue (position: {position}). "
                f"It will run automatically when the current fix completes.\n\n"
                f"**Scope:** {description.strip() or 'all outstanding review findings'}\n"
                f"**Estimated start:** ~5 min after current fix finishes."
            )

    # No existing fix — try to spawn immediately (with lock retry)
    def do_spawn():
        return _spawn_fix(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            pr_title=title,
            pr_author=author,
            total_loc=total_loc,
            head_sha=head_sha,
            head_ref=head_ref,
            description=description.strip(),
            push_eligible=push_eligible,
        )

    try:
        spawned = _with_db_retry(do_spawn)
    except sqlite3.OperationalError:
        log.error(f"DB locked after retries during spawn for #{pr_number}")
        return f"⚠️ Database temporarily locked for #{pr_number}. Please retry in a few seconds."
    except Exception as e:
        log.error("Failed to spawn fix: %s", e)
        return f"⚠️ Failed to spawn fix session for #{pr_number}: {e}"

    if not spawned:
        # Reserve failed — something else grabbed the slot. Queue this request.
        def queue_on_race():
            state = StateStore()
            qid = state.enqueue_fix(pr_number, pr_key, commenter, description.strip(), installation_id=installation_id, owner=owner, repo=repo)
            return state.get_queue_position(qid)
        try:
            position = _with_db_retry(queue_on_race)
        except sqlite3.OperationalError:
            position = "?"
        return (
            f"⏳ **Fix queued for #{pr_number}.**\n\n"
            f"A fix just started for this PR (dedup race). "
            f"Your request has been added to the queue (position: {position}). "
            f"It will run automatically when the current fix completes.\n\n"
            f"**Scope:** {description.strip() or 'all outstanding review findings'}"
        )

    log.info(
        "On-demand fix spawned for %s/%s#%d by %s (push_eligible=%s)",
        owner, repo, pr_number, commenter, push_eligible,
    )

    mode = (
        "edit, commit, and push fixes directly to the PR branch"
        if push_eligible
        else "generate a comment-only patch (fork or foreign repo)"
    )
    scope = (
        f"the problem you described: _{description.strip()}_"
        if description.strip()
        else "all outstanding review findings"
    )
    return (
        f"🛠 **Riptide Fix triggered for #{pr_number}!**\n\n"
        f"A Hermes fix session has been scheduled and will begin within 2 minutes. "
        f"It will {mode}. Scope: {scope}.\n\n"
        f"**PR:** {title}\n"
        f"**Author:** @{author}\n"
        f"**Changes:** +{additions}/-{deletions} ({total_loc} LOC)\n"
        f"**Commit:** `{head_sha[:12]}`"
    )


def _is_push_eligible(owner: str, repo: str, pr_author: str) -> bool:
    """Safety gate: allow pushing only when we own the repo or authored the PR.

    Mirrors the Bot 2 ownership filter (deepthink.py). Never push to
    foreign repos. (Fork PRs are additionally excluded by the caller.)
    """
    return owner == OUR_ORG or pr_author == OUR_USERNAME


def _is_fork_push_eligible(is_fork: bool, author: str) -> bool:
    """Return True if a fork PR is push-eligible."""
    return not is_fork or author == OUR_USERNAME


def _is_cron_available() -> bool:
    """Check that `hermes cron create` works."""
    import shutil
    return shutil.which("hermes") is not None


def _spawn_fix(
    owner: str,
    repo: str,
    pr_number: int,
    pr_title: str,
    pr_author: str,
    total_loc: int,
    head_sha: str,
    head_ref: str,
    description: str,
    push_eligible: bool,
) -> bool:
    """Spawn a Hermes cron session that edits, commits, and pushes the fix.

    Retries up to 3 times with exponential backoff (5s/10s/20s).
    Reserves a pending job before spawning; marks failed if all attempts
    fail. Returns True if spawned successfully, False otherwise.
    """
    max_retries = 3
    base_delay = 5  # seconds
    name = f"riptide-fix-{owner}-{repo}-{pr_number}"
    run_at = (datetime.now() + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")

    # Cross-session awareness: clean up stale jobs, then atomically reserve
    from riptide.state import StateStore
    state = StateStore()
    # Graceful handling of concurrent cron jobs (SQLite lock contention)
    for _retry in range(3):
        try:
            state.cleanup_stale_pending()
            break
        except Exception as e:
            if "locked" in str(e).lower() and _retry < 2:
                import time as _time
                _time.sleep(2 * (_retry + 1))
            else:
                raise
    job_id = f"{name}-{head_sha[:12]}-{uuid.uuid4().hex[:12]}"
    if not state.reserve_job(job_id, pr_number, "t1", name):
        log.info(f"Skipping {owner}/{repo}#{pr_number} — fix already pending")
        return False

    try:
        prompt = _build_fix_prompt(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            pr_title=pr_title,
            pr_author=pr_author,
            total_loc=total_loc,
            head_sha=head_sha,
            head_ref=head_ref,
            description=description,
            push_eligible=push_eligible,
            job_id=job_id,
        )
    except Exception as e:
        state.mark_failed(job_id)
        log.error(f"Failed to build fix prompt for {owner}/{repo}#{pr_number}: {e}")
        return False

    cmd = [
        "hermes", "cron", "create", run_at,
        prompt,
        "--name", name,
        "--skill", "github-pr-lifecycle",
        "--skill", "deep-think",
        "--skill", "riptide-fix",
        "--model", FIX_MODEL,
        "--provider", FIX_PROVIDER,
        "--repeat", "1",
        "--deliver", "origin",
    ]

    for attempt in range(max_retries):
        if attempt > 0:
            delay = base_delay * (2 ** attempt)  # 5s, 10s, 20s
            log.info(f"Retry {attempt+1}/{max_retries} for {owner}/{repo}#{pr_number} in {delay}s...")
            time.sleep(delay)

        if not _is_cron_available():
            log.warning(f"hermes not available on attempt {attempt+1} for {owner}/{repo}#{pr_number}")
            continue

        log.info(f"Spawning: hermes cron create {run_at} --name {name} (attempt {attempt+1})")
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                log.info(f"✓ Spawned fix for {owner}/{repo}#{pr_number}: {result.stdout[:200]}")
                return True
            else:
                log.error(f"✗ Spawn failed (attempt {attempt+1}): {result.stderr[:300]}")
        except subprocess.TimeoutExpired:
            log.warning(f"Timeout spawning fix (attempt {attempt+1})")
        except Exception as e:
            log.error(f"Error spawning fix (attempt {attempt+1}): {e}")

    state.mark_failed(job_id)
    log.error(f"All {max_retries} attempts failed for {owner}/{repo}#{pr_number}")
    return False


def process_fix_queue(client, owner: str = "ChonSong", repo: str = "riptide") -> Optional[str]:
    """Process the next queued fix if any.

    Pops the oldest 'queued' item, marks it 'running', fetches PR details,
    and spawns a fix session directly (bypassing handle_fix_command's busy
    check, since we already hold the queue slot).

    Returns a status message if a fix was started, None if queue is empty.
    """
    from riptide.state import StateStore
    state = StateStore()
    state.cleanup_stale_pending()
    state.cleanup_stale_queue_items()

    next_item = state.start_next_queued_fix()
    if not next_item:
        return None

    pr_number = next_item["pr_number"]
    description = next_item["description"]
    installation_id = next_item.get("installation_id")

    # Fetch PR details using the stored installation_id (not None)
    try:
        pr_details = client.get_pr_details(installation_id, owner, repo, pr_number)
    except Exception as e:
        log.error(f"Failed to fetch PR #{pr_number} from queue: {e}")
        state.complete_fix_queue_item(next_item["id"], success=False)
        return None

    # Spawn directly — we already hold the queue slot, so skip handle_fix_command's
    # busy-state check. This avoids recursive re-entry into the queue.
    title = pr_details.get("title", f"PR #{pr_number}")
    author = pr_details.get("user", {}).get("login", "unknown")
    additions = pr_details.get("additions", 0)
    deletions = pr_details.get("deletions", 0)
    total_loc = additions + deletions
    head_sha = pr_details.get("head", {}).get("sha", "")
    head_ref = pr_details.get("head", {}).get("ref", "")
    head_repo = (pr_details.get("head", {}).get("repo") or {}).get("full_name", "")
    is_fork = head_repo.lower() != f"{owner}/{repo}".lower() if head_repo else True
    push_eligible = _is_push_eligible(owner, repo, author) and _is_fork_push_eligible(is_fork, author)

    try:
        spawned = _spawn_fix(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            pr_title=title,
            pr_author=author,
            total_loc=total_loc,
            head_sha=head_sha,
            head_ref=head_ref,
            description=description or "",
            push_eligible=push_eligible,
        )
    except Exception as e:
        log.error(f"Failed to spawn queued fix: {e}")
        state.complete_fix_queue_item(next_item["id"], success=False)
        return f"⚠️ Queued fix for #{pr_number} failed: {e}"

    if spawned:
        state.complete_fix_queue_item(next_item["id"], success=True)
        return f"🚀 Queued fix started for PR #{pr_number}"
    else:
        state.complete_fix_queue_item(next_item["id"], success=False)
        return f"⚠️ Queued fix for #{pr_number} failed to spawn"


def _build_fix_prompt(
    owner: str,
    repo: str,
    pr_number: int,
    pr_title: str,
    pr_author: str,
    total_loc: int,
    head_sha: str,
    head_ref: str,
    description: str,
    push_eligible: bool,
    job_id: str,
) -> str:
    """Build the orchestrator prompt for the spawned fix session.

    Self-aware and grounded: the session verifies each finding against the
    current code before editing, edits only files in the PR's diff, runs
    tests before pushing, and reports per-finding verdicts.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    scope_text = (
        f"Fix ONLY the problem described here: {description}"
        if description
        else "Fix ALL outstanding findings from the latest @riptide-bot review on this PR."
    )
    push_instructions = (
        f"""## Push (authorized — same-repo, push eligible)
1. git add ONLY the files you edited (never `git add -A`).
2. git commit with a Conventional Commit message (fix(scope): ...).
3. Push to the PR branch: `git push origin HEAD:{head_ref}`
   (gh's credential helper authenticates this as ChonSong — no extra setup).
4. NEVER force-push. NEVER rewrite pushed history."""
        if push_eligible
        else """## Push (NOT authorized — fork or foreign repo)
Do NOT push. Instead post a PR comment containing the full patch
(`git diff` output) plus a note: "Cannot push to a fork/foreign repo —
apply this patch manually or open a PR in the base repository."
Never stay silent."""
    )

    return f"""## Mission
{scope_text}

PR: #{pr_number} in {owner}/{repo} — "{pr_title}" by @{pr_author}
HEAD: {head_sha[:12]} (branch: {head_ref}) · Changes: {total_loc} LOC

## Pre-flight (mandatory, in order)
1. import sys; sys.path.insert(0, '{WORKSPACE_ROOT}')   # PYTHONPATH pitfall
2. Clone/update the repo at the PR HEAD (fork-safe — `git fetch origin {head_ref}`
   fails for fork PRs whose head lives on the fork, not the base repo):
   `gh repo clone {owner}/{repo} /tmp/riptide-fix-{pr_number} -- --depth 50`
   then `cd /tmp/riptide-fix-{pr_number} && git fetch origin pull/{pr_number}/head:pr-{pr_number} && git checkout {head_sha}`
3. `gh pr view {pr_number} --repo {owner}/{repo} --json files,additions,deletions,headRefOid`
4. Graphify first-pass (blast radius before editing anything):
   `graphify query "<what does the file I'm changing touch>" --graph graphify-out/{today}/graph.json`
   `graphify path <fileA> <fileB>` for callers of anything you will change.

## Verification gate (run BEFORE any edit — one finding at a time, sequential)
Parse the latest @riptide-bot review comment's `## 🔍 Findings` section
plus inline review threads (`gh api repos/{owner}/{repo}/pulls/{pr_number}/comments`).
For EACH finding, verify it against the CURRENT code at {head_sha[:12]}:
  - Fetch the file at the PR HEAD (never trust stale line numbers — match by code context).
  - Verdict: `valid` (still present) | `skip-already-addressed` | `skip-stale-false-positive`.
Only `valid` findings proceed to implementation. Skip the rest with a one-line reason.

## Deep-think loop
SURFACE → EXPLORE (graphify) → CHALLENGE → SYNTHESIZE → VALIDATE

## Constraints (hard)
- ONLY touch files in this PR's diff. Scope isolation.
- NEVER edit github-private-key.pem, .env, or any credential/secret file.
- NO force-push. NO rewriting pushed history.
- Run the repo's test suite before pushing. No push on red tests.
- Run `python -m py_compile` on every changed .py file.
- Conventional Commits (fix(scope): ...).
- Model attribution footer REQUIRED on the summary comment:
  <sub>🤖 Riptide Fix via Hermes · model: <model_name></sub>

## Execution (sequential subagents — one at a time, never parallel)
1. Verification subagent → per-finding verdicts (above).
2. Implementation subagent → minimal, targeted edits for `valid` findings only.
3. Test + validate → run repo tests; iterate until green.
{push_instructions}

## Summary comment (always posted when done)
Post a PR comment listing per-finding verdict + one-line reason, files
touched, test results, and the commit SHA (or the patch if push was not
authorized). Include the model attribution footer.

## Cleanup (mandatory — run AFTER posting the summary comment)
import sys; sys.path.insert(0, '/home/sc/workspace/riptide')
from riptide.orchestrator import StateStore
state = StateStore()
# Call ONE of these based on outcome:
state.mark_complete('{job_id}')  # success: fixes applied and tested
# state.mark_failed('{job_id}')   # failure: red tests, could not complete
"""
