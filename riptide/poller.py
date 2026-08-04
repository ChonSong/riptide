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

import requests

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


def _get_gh_token() -> str:
    cmd = ["gh", "auth", "token"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get gh token: {result.stderr[:200]}")
    return result.stdout.strip()


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
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days))
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    try:
        token = _get_gh_token()
        params = {
            "q": f'is:pr is:open "@riptide-bot fix" updated:>={cutoff_str}',
            "sort": "updated", "order": "desc", "per_page": "20",
        }
        url = "https://api.github.com/search/issues"
        headers = {"Authorization": f"token {token}",
                   "Accept": "application/vnd.github+json"}
        result = requests.get(url, params=params, headers=headers, timeout=15)
        if result.status_code != 200:
            log.warning("search failed: HTTP %s", result.status_code)
            return []
        data = result.json()
    except Exception as e:
        log.error("search error: %s", e)
        return []

    matches = []
    seen_prs = set()

    for item in data.get("items", []):
        repo_url = item.get("repository_url", "")
        parts = repo_url.rsplit("/", 2)
        if len(parts) < 3:
            continue
        owner = parts[-2]
        repo = parts[-1]
        pr_number = item["number"]

        pr_key = f"{owner}/{repo}#{pr_number}"
        if pr_key in seen_prs:
            continue
        seen_prs.add(pr_key)

        comments = _get_pr_comments(owner, repo, pr_number)
        for comment in comments:
            body = comment.get("body", "")
            if FIX_RE.search(body):
                # Check if comment is recent enough (compare updated_at, fallback to created_at)
                comment_ts_str = comment.get("updated_at") or comment.get("created_at", "")
                if comment_ts_str:
                    try:
                        comment_ts = datetime.fromisoformat(comment_ts_str.replace("Z", "+00:00"))
                        if comment_ts < cutoff:
                            continue  # Skip stale command comments
                    except (ValueError, TypeError):
                        pass  # If parsing fails, include the comment
                matches.append({
                    "comment_id": comment["id"],
                    "commenter": comment.get("user", {}).get("login", "unknown"),
                    "body": body,
                    "owner": owner,
                    "repo": repo,
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

    # Check for pending response from a previous failed post attempt
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
            # Successfully posted — clear pending response and mark as spawned
            spawned = "Riptide Fix triggered" in pending_response
            status = "spawned" if spawned else "not-spawned"
            _mark_processed(conn, comment_id, f'{{"result":"{status}","pr_key":"{pr_key}"}}', pending_response="")
            log.info(f"Successfully posted pending response for {pr_key}")
            return
        except Exception as e:
            log.error(f"Retry post failed for {pr_key}: {e}")
            # Keep pending response for future retry
            return

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
                            f'{{"result":"{status}","pr_key":"{pr_key}"}}', pending_response="")
            log.info(f"Posted fix trigger comment on {pr_key} (spawned={spawned})")
        except Exception as e:
            log.error(f"Failed to post comment on {pr_key}: {e}")
            # Store the response body as pending for retry, but don't mark as permanently processed
            _mark_processed(conn, comment_id, f"post-pending: {str(e)[:150]}", pending_response=result)
    else:
        log.info(f"Fix handler returned no comment for {pr_key}")
        _mark_processed(conn, comment_id, "no-result", pending_response="")


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
