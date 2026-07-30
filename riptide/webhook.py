#!/usr/bin/env python3
"""
webhook.py — FastAPI webhook receiver for Riptide.

Handles GitHub App webhook events:
  - pull_request (opened, reopened, synchronize)  → enqueue companion TLDR
  - pull_request (closed, merged)                → no-op (was incremental index)
  - issue_comment (@mention)                     → companion skip/resume
  - installation / installation_repositories    → sync repo list

The companion posts a TL;DR comment with graphify-informed blast radius.
Riptide Review (Bot 2) runs via cron polling in deepthink.py — not here.
"""
import os
import json
import logging
import traceback
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException
from pydantic import BaseModel

from .github_app import verify_webhook_signature, GitHubAppClient

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


# ── Webhook handler ────────────────────────────────────────────────────────────


@app.post("/webhook/github")
async def github_webhook(request: Request) -> Response:
    """Main GitHub webhook endpoint."""
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
    """Handle pull_request events — spawn companion TLDR thread."""
    action = payload.get("action", "")
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})

    owner = repo.get("full_name", "").split("/")[0] if repo.get("full_name") else ""
    repo_name = repo.get("name", "")
    repo_full = repo.get("full_name", "")
    pr_number = pr.get("number")
    installation_id = installation.get("id")

    if not installation_id:
        log.info(f"[{delivery_id}] No installation ID, skipping")
        return Response(status_code=200)

    log.info(
        f"[{delivery_id}] PR {action}: {repo_full}#{pr_number}"
    )

    # PR opened/reopened/synchronize → companion TLDR
    if action in ("opened", "reopened", "synchronize"):
        companion = get_companion()
        if companion and companion.is_active_for(owner, repo_name):
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

    return Response(status_code=200)


async def handle_issue_comment(payload: dict, delivery_id: str) -> Response:
    """Handle issue_comment events — companion skip/resume and on-demand review commands."""
    action = payload.get("action", "")
    if action != "created":
        return Response(status_code=200)

    comment = payload.get("comment", {})
    body = comment.get("body", "")
    issue = payload.get("issue", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})
    installation_id = installation.get("id")

    # Skip own comments (posted by the bot)
    via_app = comment.get("performed_via_github_app") or {}
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

    is_pr = bool(issue.get("pull_request"))
    if not is_pr:
        return Response(status_code=200)

    owner = repo.get("full_name", "").split("/")[0] if repo.get("full_name") else ""
    repo_name = repo.get("name", "")
    pr_number = issue.get("number")
    commenter = comment.get("user", {}).get("login", "unknown")

    # Route 1: Companion skip/resume commands
    companion = get_companion()
    if companion:
        result = companion.handle_comment(
            installation_id, owner, repo_name, pr_number, body, commenter
        )
        if result:
            if installation_id:
                try:
                    github_client().post_pr_comment(
                        installation_id, owner, repo_name, pr_number, result
                    )
                except Exception as e:
                    log.warning(f"[{delivery_id}] Could not post companion reply: {e}")
            return Response(status_code=200)

    # Route 2: On-demand review command (@riptide-bot review / deepthink / full review)
    if installation_id and body and "@riptide-bot" in body.lower():
        from riptide.deepthink import REVIEW_RE, handle_review_command

        if REVIEW_RE.search(body):
            log.info(
                f"[{delivery_id}] Review command on {owner}/{repo_name}#{pr_number} by {commenter}"
            )
            try:
                client = github_client()
                result = handle_review_command(
                    client, installation_id, owner, repo_name, pr_number, commenter
                )
                if result:
                    client.post_pr_comment(
                        installation_id, owner, repo_name, pr_number, result
                    )
            except Exception as e:
                log.error(f"[{delivery_id}] Review command failed: {e}")

    return Response(status_code=200)


async def handle_installation(payload: dict, event: str, delivery_id: str) -> Response:
    """Handle installation events — sync repo list."""
    action = payload.get("action", "")
    installation = payload.get("installation", {})
    installation_id = installation.get("id")

    if event == "installation" and action == "deleted":
        log.info(f"[{delivery_id}] App uninstalled, installation_id={installation_id}")
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

        from datetime import datetime, timezone

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


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": "riptide"}


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
    log.info(f"Metadata DB ready at {METADATA_DB}")
