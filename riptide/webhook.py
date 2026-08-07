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
import time
import logging
import threading
import subprocess
import requests
import traceback
from pathlib import Path
from typing import Optional, TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .companion import Companion

from fastapi import FastAPI, Request, Response, HTTPException
from pydantic import BaseModel

from .github_app import verify_webhook_signature, GitHubAppClient
from .orchestrator import T0Orchestrator, TaskClassifier
from .state import StateStore
from .labeler import Labeler

# Companion is optional — silently unavailable if RIPTIDE_COMPANION_REPOS is unset
_companion: "Companion | Literal[False] | None" = None

# Labeler is optional — silently unavailable if RIPTIDE_LABELER_ENABLED != "1"
_labeler = None


def _reconcile_labels(github, installation_id, owner, repo, pr_number, new_labels, labeler):
    """Remove stale bot-managed labels, preserve human-applied ones."""
    try:
        # Get current labels on the issue
        issue_url = f"{github.base_url}/repos/{owner}/{repo}/issues/{pr_number}"
        resp = requests.get(issue_url, headers=github._headers(installation_id), timeout=15)
        resp.raise_for_status()
        current_labels = [l["name"] for l in resp.json().get("labels", [])]

        # Determine which labels are bot-managed (in our taxonomy)
        all_bot_labels = labeler._get_all_labels()

        # Remove bot-managed labels that are NOT in the new classification
        for label in current_labels:
            if label in all_bot_labels and label not in new_labels:
                try:
                    github.remove_label_from_issue(installation_id, owner, repo, pr_number, label)
                except Exception:
                    pass  # Label may have been removed already
    except Exception as e:
        log.warning(f"Label reconciliation failed: {e}")


def get_labeler():
    global _labeler
    if _labeler is None:
        if os.environ.get("RIPTIDE_LABELER_ENABLED", "1") != "1":
            _labeler = False  # sentinel — don't retry
            return None
        try:
            _labeler = Labeler()
        except Exception as e:
            log.warning("Labeler not available: %s", e)
            _labeler = False
    return _labeler if _labeler else None


def get_companion():
    global _companion
    if _companion is None:
        try:
            from .companion import Companion

            _companion = Companion(github_client() if GITHUB_PRIVATE_KEY_PATH else None)
        except Exception as e:
            log.warning("Companion not available: %s", e)
            _companion = False  # sentinel — don't retry
    result = _companion
    if result is False or result is None:
        return None
    return result


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("riptide.webhook")

app = FastAPI(title="Riptide Webhook Server")

# ── Synchronize rate limiting ───────────────────────────────────────────────
# Track last synchronize time per PR to avoid flooding on frequent pushes
_SYNCHRONIZE_TIMESTAMPS: dict[str, float] = {}
_SYNCHRONIZE_LOCK = threading.Lock()
_SYNCHRONIZE_MIN_INTERVAL = 60.0  # seconds between synchronize processing


def _sync_test_helper(items: list[dict]) -> list[str]:
    """Live-test helper (WS-3 sync path): deterministic finding fixture."""
    keys = []
    for item in items:
        try:
            keys.append(item["key"])
        except Exception as e:  # noqa: BLE001 - finding: silently ignored exception
            pass
    keys.sort()
    return keys


def _should_process_synchronize(owner: str, repo: str, pr_number: int) -> bool:
    """Check if enough time has passed since the last synchronize for this PR."""
    global _SYNCHRONIZE_TIMESTAMPS
    key = f"{owner}/{repo}#{pr_number}"
    now = time.time()
    with _SYNCHRONIZE_LOCK:
        last_time = _SYNCHRONIZE_TIMESTAMPS.get(key, 0)
        if now - last_time < _SYNCHRONIZE_MIN_INTERVAL:
            return False
        _SYNCHRONIZE_TIMESTAMPS[key] = now
        # Clean old entries (simple GC)
        if len(_SYNCHRONIZE_TIMESTAMPS) > 1000:
            cutoff = now - 3600  # 1 hour
            _SYNCHRONIZE_TIMESTAMPS = {
                k: v for k, v in _SYNCHRONIZE_TIMESTAMPS.items() if v > cutoff
            }
        return True


_state_store = None


def _get_state_store():
    global _state_store
    if _state_store is None:
        _state_store = StateStore(
            db_path=os.environ.get("RIPTIDE_STATE_DB", "/tmp/riptide_state.db")
        )
    return _state_store


# ── Health check ────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    """Return server health status for monitoring / tunnel-watchdog."""
    return {"status": "ok"}

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

    # Idempotency: drop duplicate deliveries before expensive processing
    if not _get_state_store().reserve_delivery(delivery_id):
        log.info(f"[{delivery_id}] Duplicate delivery dropped")
        return Response(status_code=200)

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

    # Rate limit: skip 'synchronize' if one happened recently for this PR
    if action == "synchronize":
        if not _should_process_synchronize(owner, repo_name, pr_number):
            log.info(f"[{delivery_id}] synchronize rate-limited for {repo_full}#{pr_number}")
            return Response(status_code=200)

    # PR opened/reopened/synchronize → companion deterministic flow (one pipeline)
    if action in ("opened", "reopened", "synchronize"):
        companion = get_companion()
        github = github_client() if GITHUB_PRIVATE_KEY_PATH else None
        if companion and companion.is_active_for(owner, repo_name):
            from threading import Thread

            # Fetch PR files + head SHA (shared by companion flow and T0 fallback)
            files = []
            head_sha = ""
            if github:
                try:
                    files = github.get_pr_files(installation_id, owner, repo_name, pr_number)
                    pr_detail = github.get_pr_details(installation_id, owner, repo_name, pr_number)
                    head_sha = pr_detail.get("head", {}).get("sha", "")
                except Exception as e:
                    log.warning(f"[{delivery_id}] Could not fetch PR files: {e}")

            title = pr.get("title", f"PR #{pr_number}")
            author = pr.get("user", {}).get("login", "unknown")

            if os.environ.get("RIPTIDE_T0_FALLBACK", "").lower() in ("1", "true", "yes"):
                # Legacy T0 dispatcher (opt-in fallback; default is companion flow)
                total_loc = sum(f.get("additions", 0) + f.get("deletions", 0) for f in files)
                profile = TaskClassifier().classify(
                    pr_number, owner, repo_name,
                    title, author,
                    files, total_loc,
                    installation_id=installation_id,
                    head_sha=head_sha,
                )

                def _safe_orchestrate(*args, **kwargs):
                    """Run T0 orchestrator with fail-safe."""
                    try:
                        orch = T0Orchestrator(companion=companion, github_client=github)
                        orch.review_pr(*args, **kwargs)
                    except Exception as e:
                        log.error(
                            f"[{delivery_id}] Orchestrator crashed for "
                            f"{repo_full}#{pr_number}: {e}\n{traceback.format_exc()}"
                        )

                t = Thread(
                    target=_safe_orchestrate,
                    args=(profile,),
                    kwargs={"mode": "parallel"},
                    daemon=True,
                    name=f"t0-{repo_name}-{pr_number}",
                )
                t.start()
                log.info(
                    f"[{delivery_id}] T0 orchestrator spawned (fallback) for {repo_full}#{pr_number}"
                )
            else:
                # Deterministic companion flow — the single pipeline entry.
                # Runs depth decision → context bundle → Tier-1 canonical thread
                # (Stage 0/1/2) inside Companion.run_for_pr (semaphore-guarded).
                def _safe_run():
                    try:
                        companion.run_for_pr(
                            installation_id, owner, repo_name, pr_number,
                            title, author, files,
                        )
                    except Exception as e:
                        log.error(
                            f"[{delivery_id}] Companion flow crashed for "
                            f"{repo_full}#{pr_number}: {e}\n{traceback.format_exc()}"
                        )

                t = Thread(
                    target=_safe_run,
                    daemon=True,
                    name=f"companion-{repo_name}-{pr_number}",
                )
                t.start()
                log.info(
                    f"[{delivery_id}] Companion deterministic flow spawned for {repo_full}#{pr_number}"
                )

            # Also spawn labeler thread (non-blocking)
            labeler = get_labeler()
            if labeler and github:
                def _safe_label():
                    try:
                        labels = labeler.classify_pr(pr_detail, files, repo_full)
                        # Setup labels on repo first (creates missing ones)
                        labeler.setup_labels_on_repo(installation_id, owner, repo_name, github)
                        # Reconcile: remove stale bot-managed labels, preserve human labels
                        _reconcile_labels(github, installation_id, owner, repo_name, pr_number, labels, labeler)
                        # Add labels to PR
                        github.add_labels_to_issue(installation_id, owner, repo_name, pr_number, labels)
                        log.info(f"[{delivery_id}] Labels applied to {repo_full}#{pr_number}: {labels}")
                    except Exception as e:
                        log.error(f"[{delivery_id}] Labeler failed: {e}")

                label_thread = threading.Thread(target=_safe_label, daemon=True, name=f"label-{repo_name}-{pr_number}")
                label_thread.start()
                log.info(f"[{delivery_id}] Labeler spawned for {repo_full}#{pr_number}")

    # PR merged into default branch → auto-deploy
    elif action == "closed" and pr.get("merged"):
        default_branch = os.environ.get("RIPTIDE_DEPLOY_BRANCH", "main")
        base_ref = pr.get("base", {}).get("ref", "")
        if base_ref == default_branch:
            log.info(f"[{delivery_id}] PR #{pr_number} merged into {default_branch} — triggering auto-deploy")
            deploy_script = os.environ.get("RIPTIDE_DEPLOY_SCRIPT", "/home/sc/workspace/riptide/scripts/deploy.sh")
            if not Path(deploy_script).exists():
                log.error(
                    f"[{delivery_id}] Auto-deploy skipped — script not found: {deploy_script}"
                )
            elif not os.access(deploy_script, os.X_OK):
                log.error(
                    f"[{delivery_id}] Auto-deploy skipped — script not executable: {deploy_script}"
                )
            else:
                try:
                    proc = subprocess.Popen(
                        ["systemd-run", "--user", "--scope", "--property=KillMode=process", deploy_script],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.STDOUT,
                    )
                    log.info(f"[{delivery_id}] Auto-deploy triggered (pid={proc.pid})")
                except FileNotFoundError:
                    log.error(
                        f"[{delivery_id}] Auto-deploy skipped — systemd-run not found. Install systemd or trigger deploy manually."
                    )
                except Exception as e:
                    log.error(f"[{delivery_id}] Failed to trigger auto-deploy: {e}")

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

        # Route 2b: On-demand fix command (@riptide-bot fix [description])
        from riptide.fixer import FIX_RE, handle_fix_command

        if FIX_RE.search(body):
            log.info(
                f"[{delivery_id}] Fix command on {owner}/{repo_name}#{pr_number} by {commenter}"
            )
            try:
                client = github_client()
                description = FIX_RE.search(body).group(1).strip()
                result = handle_fix_command(
                    client, installation_id, owner, repo_name, pr_number, commenter, description
                )
                if result:
                    client.post_pr_comment(
                        installation_id, owner, repo_name, pr_number, result
                    )
            except Exception as e:
                log.error(f"[{delivery_id}] Fix command failed: {e}")

        # Route 2c: Relabel command (@riptide-bot relabel)
        if "@riptide-bot relabel" in body.lower():
            log.info(
                f"[{delivery_id}] Relabel command on {owner}/{repo_name}#{pr_number} by {commenter}"
            )
            try:
                client = github_client()
                labeler = get_labeler()
                if labeler:
                    pr_detail = client.get_pr_details(installation_id, owner, repo_name, pr_number)
                    files = client.get_pr_files(installation_id, owner, repo_name, pr_number)
                    labels = labeler.classify_pr(pr_detail, files, f"{owner}/{repo_name}")
                    labeler.setup_labels_on_repo(installation_id, owner, repo_name, client)
                    # Reconcile: remove stale bot-managed labels before applying new ones
                    _reconcile_labels(client, installation_id, owner, repo_name, pr_number, labels, labeler)
                    client.add_labels_to_issue(installation_id, owner, repo_name, pr_number, labels)
                    client.post_pr_comment(
                        installation_id, owner, repo_name, pr_number,
                        f"🏷️ Labels re-applied: {', '.join(labels)}"
                    )
            except Exception as e:
                log.error(f"[{delivery_id}] Relabel command failed: {e}")

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
