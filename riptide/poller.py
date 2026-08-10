#!/usr/bin/env python3
"""
poller.py — Riptide Bot: poll for @riptide-bot fix comments on external repos.

The GitHub App webhook only fires for repos where the app is installed.
For external repos (different org, not installed), we poll via `gh` CLI
(authenticated as ChonSong via PAT) using the GitHub search API.

Workflow:
1. Search open PRs for "@riptide-bot fix" phrase via GitHub search API.
2. For each matching PR, fetch comments to find the exact comment ID.
3. Call handle_fix_command with a GhCliClient (PAT-based, no app install needed).
4. Track processed comment IDs in metadata.db to avoid re-processing.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("riptide.poller")

DATA_DIR = Path(os.environ.get("RIPTIDE_DATA_DIR", "/home/sc/.local/share/riptide"))
DB_PATH = DATA_DIR / "metadata.db"
PROCESSED_TABLE = "poller_processed_comments"
FIX_RE = re.compile(r"@riptide-bot\s+fix\b(.*)", re.IGNORECASE | re.DOTALL)
LOOKBACK_DAYS = int(os.environ.get("RIPTIDE_POLLER_LOOKBACK", "3"))
# How many open PRs to scan per poll. GitHub's search API caps a single
# response at 100 items; `gh` auto-paginates internally to honor --limit,
# so a limit above 20 keeps older fix requests from being pushed off the
# page when many PRs were updated recently.
SEARCH_LIMIT = int(os.environ.get("RIPTIDE_POLLER_SEARCH_LIMIT", "100"))





def _init_db(conn: sqlite3.Connection):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {PROCESSED_TABLE} (
            comment_id INTEGER PRIMARY KEY,
            processed_at TEXT NOT NULL,
            result TEXT,
            pending_response TEXT
        )
    """)
    # Migration for pre-existing databases: CREATE TABLE IF NOT EXISTS is a
    # no-op when the table already exists, so databases created before the
    # pending_response column was added lack it. Without the column,
    # _mark_processed/_get_pending_response throw "no such column" and break
    # the poller on every fix comment.
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({PROCESSED_TABLE})").fetchall()}
    if "pending_response" not in columns:
        conn.execute(f"ALTER TABLE {PROCESSED_TABLE} ADD COLUMN pending_response TEXT")
    conn.commit()


def _is_processed(conn: sqlite3.Connection, comment_id: int) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {PROCESSED_TABLE} WHERE comment_id = ?",
        (comment_id,)
    ).fetchone()
    return row is not None


def _mark_processed(conn: sqlite3.Connection, comment_id: int, result: str = "", pending_response: str = ""):
    conn.execute(
        f"INSERT OR REPLACE INTO {PROCESSED_TABLE} (comment_id, processed_at, result, pending_response) "
        f"VALUES (?, ?, ?, ?)",
        (comment_id, datetime.now(timezone.utc).isoformat(), result[:200], pending_response)
    )
    conn.commit()


def _get_pending_response(conn: sqlite3.Connection, comment_id: int) -> Optional[str]:
    """Retrieve a pending response body that failed to post."""
    row = conn.execute(
        f"SELECT pending_response FROM {PROCESSED_TABLE} WHERE comment_id = ?",
        (comment_id,)
    ).fetchone()
    return row[0] if row and row[0] else None


def _has_pending_fix(conn: sqlite3.Connection, pr_key: str) -> bool:
    """Check if any comment on this PR already spawned a fix."""
    like_pattern = f'%"spawned"%'
    rows = conn.execute(
        f"SELECT result FROM {PROCESSED_TABLE} WHERE result LIKE ?",
        (like_pattern,)
    ).fetchall()
    for (result_str,) in rows:
        try:
            data = json.loads(result_str)
            if data.get("pr_key") == pr_key:
                return True
        except (json.JSONDecodeError, TypeError):
            # Legacy rows without JSON
            if pr_key in result_str:
                return True
    return False


def _search_fix_comments(lookback_days: int = LOOKBACK_DAYS) -> list[dict]:
    """Search open PRs mentioning @riptide-bot fix via gh CLI.

    --match comments restricts the GitHub search to comments only, so PRs
    whose body/title merely contains the phrase are not matched (we would
    otherwise waste API calls fetching their comments and find no FIX_RE
    match). gh auto-paginates up to --limit, so SEARCH_LIMIT > 20 keeps
    older fix requests visible even when many PRs were updated recently.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days))
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    cmd = [
        "gh", "search", "prs",
        "--state", "open",
        "--updated", f">={cutoff_str}",
        "--sort", "updated",
        "--order", "desc",
        "--limit", str(SEARCH_LIMIT),
        "--match", "comments",
        "--json", "number,title,repository,createdAt,body,author,commentsCount",
        f'"@riptide-bot fix"',
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            log.warning("gh search prs failed: %s", result.stderr[:200])
            return []
        items = json.loads(result.stdout) if result.stdout.strip() else []
    except Exception as e:
        log.error("search error: %s", e)
        return []

    matches = []
    seen_prs = set()

    for item in items:
        repo = item.get("repository", {})
        owner = repo.get("owner", {}).get("login", "")
        repo_name = repo.get("name", "")
        pr_number = item.get("number", 0)
        if not owner or not repo_name or not pr_number:
            continue

        pr_key = f"{owner}/{repo_name}#{pr_number}"
        if pr_key in seen_prs:
            continue
        seen_prs.add(pr_key)

        comments = _get_pr_comments(owner, repo_name, pr_number)
        for comment in comments:
            body = comment.get("body", "")
            if FIX_RE.search(body):
                matches.append({
                    "comment_id": comment["id"],
                    "commenter": comment.get("user", {}).get("login", "unknown"),
                    "body": body,
                    "owner": owner,
                    "repo": repo_name,
                    "pr_number": pr_number,
                    "pr_title": item.get("title", ""),
                    "created_at": comment.get("created_at", ""),
                    "pr_key": pr_key,
                })

    return matches


def _get_comments_page(endpoint: str, page: int) -> list[dict]:
    """Fetch a single page of comments from a GitHub API endpoint."""
    cmd = ["gh", "api", f"{endpoint}?sort=created&direction=desc&per_page=100&page={page}"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        return []
    return json.loads(result.stdout) or []


def _get_pr_comments(owner: str, repo: str, pr_number: int) -> list[dict]:
    """Fetch all comments (issue + review) for a PR, paginated."""
    comments = []
    for endpoint in [
        f"repos/{owner}/{repo}/issues/{pr_number}/comments",
        f"repos/{owner}/{repo}/pulls/{pr_number}/comments",
    ]:
        for page in range(1, 4):  # max 3 pages = 300 comments
            try:
                batch = _get_comments_page(endpoint, page)
            except Exception as e:
                log.debug("Error fetching comments: %s", e)
                break
            comments.extend(batch)
            if len(batch) < 100:
                break
    return comments


def _handle_fix(client, match: dict, conn: sqlite3.Connection):
    comment_id = match["comment_id"]
    pr_key = match["pr_key"]
    owner = match["owner"]
    repo = match["repo"]
    pr_number = match["pr_number"]

    # Retry path: if a previous post attempt failed, retry without re-calling handle_fix_command
    pending_response = _get_pending_response(conn, comment_id)
    if pending_response:
        log.info(f"Retrying pending response for comment {comment_id}")
        try:
            client.post_pr_comment(
                installation_id=None,
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                body=pending_response,
            )
            spawned = "Riptide Fix triggered" in pending_response
            status = "spawned" if spawned else "not-spawned"
            _mark_processed(conn, comment_id,
                            f'{{"result":"{status}","pr_key":"{pr_key}"}}', pending_response="")
            log.info(f"Successfully posted pending response for {pr_key}")
            return
        except Exception as e:
            log.error(f"Retry post failed for {pr_key}: {e}")
            return  # Keep pending response for future retry

    if _is_processed(conn, comment_id):
        log.info(f"Skipping already-processed comment {comment_id}")
        return

    # PR-level dedup: skip if any comment on this PR already spawned a fix
    if _has_pending_fix(conn, pr_key):
        log.info(f"Skipping {pr_key} — fix already spawned for this PR")
        _mark_processed(conn, comment_id, f'{{"result":"already-pending","pr_key":"{pr_key}"}}')
        return

    # Cross-channel dedup: on installed repos the GitHub App webhook already
    # claims fix jobs (reserving them in StateStore) and posts the confirmation.
    # If a pending fix job exists for this PR, stay silent — calling
    # handle_fix_command here would fail reserve_job and emit a redundant
    # "Could not schedule" comment on top of the webhook's confirmation.
    # (Non-installed/external repos have no webhook path, so this is a no-op
    # for the poller's primary use case.)
    try:
        from riptide.orchestrator import StateStore
        state = StateStore()
        name_prefix = f"riptide-fix-{owner}-{repo}-{pr_number}"
        if state.has_pending_job(name_prefix):
            log.info(f"Skipping {pr_key} — fix already pending via webhook")
            _mark_processed(conn, comment_id, f'{{"result":"already-pending-webhook","pr_key":"{pr_key}"}}')
            return
    except Exception as e:
        log.debug(f"Pending-job check failed for {pr_key}: {e}", exc_info=True)

    commenter = match["commenter"]
    body = match["body"]
    description = FIX_RE.search(body).group(1).strip() if FIX_RE.search(body) else ""

    log.info(f"Processing fix command: {pr_key} by {commenter}")

    from riptide.fixer import handle_fix_command

    result = handle_fix_command(
        client=client,
        installation_id=None,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        commenter=commenter,
        description=description,
    )

    if result:
        spawned = "Riptide Fix triggered" in result
        try:
            client.post_pr_comment(
                installation_id=None,
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                body=result,
            )
            status = "spawned" if spawned else "not-spawned"
            _mark_processed(conn, comment_id,
                            f'{{"result":"{status}","pr_key":"{pr_key}"}}')
            log.info(f"Posted fix trigger comment on {pr_key} (spawned={spawned})")
        except Exception as e:
            log.error(f"Failed to post comment on {pr_key}: {e}")
            # Store the result as pending so the next poll can retry
            _mark_processed(conn, comment_id, f"post-pending: {str(e)[:150]}",
                            pending_response=result)
    else:
        log.info(f"Fix handler returned no comment for {pr_key}")
        _mark_processed(conn, comment_id, "no-result")


def poll():
    """Main poll entry point."""
    conn = sqlite3.connect(str(DB_PATH))
    _init_db(conn)

    from riptide.gh_cli_client import make_gh_cli_client
    client = make_gh_cli_client()
    if client is None:
        log.error("gh CLI not available — poller cannot run")
        conn.close()
        return

    matches = _search_fix_comments()
    log.info(f"Found {len(matches)} @riptide-bot fix comments in last {LOOKBACK_DAYS} days")

    for match in matches:
        try:
            _handle_fix(client, match, conn)
        except Exception as e:
            log.error(f"Error handling fix comment {match.get('comment_id')}: {e}")
            import traceback
            traceback.print_exc()

    conn.close()


if __name__ == "__main__":
    poll()
