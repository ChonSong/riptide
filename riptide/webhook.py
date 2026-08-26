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
import shutil
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
from .review_memory import store_review_outcome

# Companion is optional — silently unavailable if RIPTIDE_COMPANION_REPOS is unset
_companion: "Companion | Literal[False] | None" = None

# gh CLI client for repos without App installation (PAT-based)
_GH_CLI_CLIENT_UNSET = object()
_gh_cli_client: object | None = _GH_CLI_CLIENT_UNSET

# Labeler is optional — silently unavailable if RIPTIDE_LABELER_ENABLED != "1"
_labeler = None

# Repos the companion should run on, even without App installation
WATCHED_REPOS = [
    r.strip()
    for r in os.environ.get(
        "RIPTIDE_WATCHED_REPOS",
        "ChonSong/riptide,ChonSong/hermes-webui,ChonSong/hermes-webui-extensions,ChonSong/seans-reporepo,ChonSong/pr-review,ChonSong/everything-claude-code,codeovertcp/gto-wizard-clone-v2,nesquena/hermes-webui",
    ).split(",")
    if r.strip()
]


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


def get_gh_cli_client():
    """Get or create a gh CLI client for PAT-based API access.

    Used as a fallback when the GitHub App is not installed on a repo.
    Retries on each call if gh CLI was previously unavailable.
    """
    global _gh_cli_client
    if _gh_cli_client is not _GH_CLI_CLIENT_UNSET:
        return _gh_cli_client
    try:
        from .gh_cli_client import make_gh_cli_client
        _gh_cli_client = make_gh_cli_client()
    except Exception as e:
        log.warning("gh CLI client not available: %s", e)
        _gh_cli_client = None
    return _gh_cli_client


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
            db_path=os.environ.get("RIPTIDE_STATE_DB", str(Path.home() / ".local/share/riptide/state.db"))
        )
    return _state_store


# ── Health check ────────────────────────────────────────────────────────────────


@app.get("/health")
async def health_check():
    """Return server health status for monitoring / tunnel-watchdog."""
    return {"status": "ok", "app": "riptide"}


@app.get("/metrics")
async def metrics_endpoint():
    """Prometheus-compatible /metrics endpoint for scraping."""
    from riptide.metrics import get_metrics_payload, get_metrics_content_type
    return Response(content=get_metrics_payload(), media_type=get_metrics_content_type())


# ── Trace context (contextvars + structlog) ───────────────────────────────────

import structlog
import contextvars

# Context variable for delivery_id — propagates through threads and async tasks
_delivery_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("delivery_id", default=None)

logger = structlog.get_logger("riptide")


def bind_trace_context(delivery_id: str, **extra):
    """Bind delivery_id and any extra context to the current execution context.

    This propagates to all child threads and async tasks within the same
    contextvars.Context. For cross-process jumps (Hermes cron), the
    delivery_id is embedded in the prompt.
    """
    _delivery_id_var.set(delivery_id)
    structlog.contextvars.bind_contextvars(delivery_id=delivery_id, **extra)


def get_delivery_id() -> Optional[str]:
    """Return the delivery_id bound to the current context, if any."""
    return _delivery_id_var.get()


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
    state = _get_state_store()
    if not state.reserve_delivery(delivery_id):
        log.info(f"[{delivery_id}] Duplicate delivery dropped")
        return Response(status_code=200)

    if not verify_webhook_signature(body, signature, WEBHOOK_SECRET):
        log.warning(f"[{delivery_id}] Invalid webhook signature from {request.client}")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body)
    log.info(f"[{delivery_id}] {event} event received")

    # ── Bind trace context for all downstream processing ──────────────────────
    # This propagates delivery_id through structlog automatically.
    # Worker threads spawned via contextvars.copy_context().run() inherit this.
    bind_trace_context(delivery_id, event=event, github_event=event)

    try:
        if event == "pull_request":
            result = await handle_pull_request(payload, delivery_id)
        elif event == "issue_comment":
            result = await handle_issue_comment(payload, delivery_id)
        elif event in ("installation", "installation_repositories"):
            result = await handle_installation(payload, event, delivery_id)
        else:
            log.info(f"[{delivery_id}] Unhandled event: {event}")
            result = Response(status_code=200)
        state.mark_delivery_done(delivery_id)
        return result
    except Exception as e:
        state.mark_delivery_failed(delivery_id)
        log.error(
            f"[{delivery_id}] Error handling {event}: {type(e).__name__}: {e}\n{traceback.format_exc()}"
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

    # ── Fallback: use gh CLI client for repos without App installation ───────
    # When the GitHub App isn't installed, installation_id is None but we can
    # still act on watched repos via PAT-authenticated gh CLI.
    gh_cli = None
    using_gh_cli_fallback = False
    if not installation_id:
        if repo_full in WATCHED_REPOS:
            gh_cli = get_gh_cli_client()
            if gh_cli:
                installation_id = None  # gh CLI ignores this param
                using_gh_cli_fallback = True
                log.info(f"[{delivery_id}] No installation ID for {repo_full} — using gh CLI fallback")
            else:
                log.info(f"[{delivery_id}] No installation ID for {repo_full} and gh CLI unavailable, skipping")
                return Response(status_code=200)
        else:
            log.info(f"[{delivery_id}] No installation ID for {repo_full} (not in WATCHED_REPOS), skipping")
            return Response(status_code=200)

    log.info(
        f"[{delivery_id}] PR {action}: {repo_full}#{pr_number} (gh_cli_fallback={using_gh_cli_fallback})"
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
        # When using gh CLI fallback, bypass is_active_for() check since
        # WATCHED_REPOS was already verified at the webhook level.
        if companion and (using_gh_cli_fallback or companion.is_active_for(owner, repo_name)):
            # Use gh CLI client when App is not installed (fallback mode)
            # gh_cli is already assigned from the fallback check above
            active_github = gh_cli if using_gh_cli_fallback else github
            from threading import Thread

            # Fetch PR files + head SHA (shared by companion flow and T0 fallback)
            files = []
            head_sha = ""
            if active_github:
                try:
                    files = active_github.get_pr_files(installation_id, owner, repo_name, pr_number)
                    pr_detail = active_github.get_pr_details(installation_id, owner, repo_name, pr_number)
                    head_sha = pr_detail.get("head", {}).get("sha", "")
                except Exception as e:
                    log.warning(f"[{delivery_id}] Could not fetch PR files: {e}")

            title = pr.get("title", f"PR #{pr_number}")
            author = pr.get("user", {}).get("login", "unknown")

            # Pass the gh_cli client to companion.run_for_pr() via client override
            # so all API calls use the PAT-based client when App isn't installed.
            companion_client = active_github if using_gh_cli_fallback else None

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
                            client=gh_cli if using_gh_cli_fallback else None,
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
                    f"[{delivery_id}] Companion deterministic flow spawned for {repo_full}#{pr_number} (gh_cli_fallback={using_gh_cli_fallback})"
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
        # Authoritative source: repo's actual default_branch from payload.
        # Fall back to env var RIPTIDE_DEPLOY_BRANCH, then "main".
        default_branch = repo.get("default_branch", os.environ.get("RIPTIDE_DEPLOY_BRANCH", "main"))
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
                if not shutil.which("systemd-run"):
                    log.error(
                        f"[{delivery_id}] Auto-deploy skipped — systemd-run not found in PATH. Install systemd or trigger deploy manually."
                    )
                else:
                    cmd = ["systemd-run", "--user", "--scope", "--property=KillMode=process", "--collect", deploy_script]
                    log.info(f"[{delivery_id}] Auto-deploy: invoking systemd-run with script={deploy_script}")
                    log.debug(f"[{delivery_id}] Auto-deploy: full command: {cmd}")
                    try:
                        proc = subprocess.Popen(
                            cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        log.info(f"[{delivery_id}] Auto-deploy triggered (pid={proc.pid})")
                    except Exception as e:
                        log.error(f"[{delivery_id}] Failed to trigger auto-deploy: {e}")

            # Store review outcome on merge (best-effort)
            try:
                head_sha = pr.get("head", {}).get("sha", "")
                store_review_outcome(
                    owner=owner,
                    repo=repo_name,
                    pr_number=pr_number,
                    head_sha=head_sha,
                    findings_count=0,
                    critical_count=0,
                    warning_count=0,
                    verdict="merged",
                    metadata={"source": "webhook_merge", "delivery_id": delivery_id},
                )
                log.info(f"[{delivery_id}] Review outcome stored for merged PR #{pr_number}")
            except Exception as e:
                log.warning(f"[{delivery_id}] Failed to store review outcome (non-fatal): {e}")

    return Response(status_code=200)


async def handle_issue_comment(payload: dict, delivery_id: str) -> Response:
    """Handle issue_comment events — companion skip/resume, on-demand commands, and checkbox toggles."""
    action = payload.get("action", "")

    # Route 4: Checkbox toggle (edited comments)
    if action == "edited":
        return await _handle_checkbox_toggle(payload, delivery_id)

    if action != "created":
        return Response(status_code=200)

    comment = payload.get("comment", {})
    body = comment.get("body", "")
    issue = payload.get("issue", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})
    installation_id = installation.get("id")

    # Must be a PR comment
    is_pr = bool(issue.get("pull_request"))
    if not is_pr:
        return Response(status_code=200)

    owner = repo.get("full_name", "").split("/")[0] if repo.get("full_name") else ""
    repo_name = repo.get("name", "")
    pr_number = issue.get("number")
    commenter = comment.get("user", {}).get("login", "unknown")

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

    # ── Fallback: use gh CLI client for repos without App installation ───────
    # When the GitHub App isn't installed, installation_id is None but we can
    # still act on watched repos via PAT-authenticated gh CLI.
    using_gh_cli_fallback = False
    if not installation_id:
        repo_full = repo.get("full_name", "")
        from riptide.webhook import WATCHED_REPOS
        if repo_full in WATCHED_REPOS:
            gh_cli = get_gh_cli_client()
            if gh_cli:
                installation_id = None  # gh CLI ignores this param
                using_gh_cli_fallback = True
                log.info(f"[{delivery_id}] No installation ID for {repo_full} — using gh CLI fallback")
            else:
                log.info(f"[{delivery_id}] No installation ID for {repo_full} and gh CLI unavailable, skipping")
                return Response(status_code=200)
        else:
            log.info(f"[{delivery_id}] No installation ID for {repo_full} (not in WATCHED_REPOS), skipping")
            return Response(status_code=200)

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

    # Route 2: Unified @riptide-bot command router
    if body and "@riptide-bot" in body.lower():
        from riptide.interaction_handler import handle_command

        try:
            response = handle_command(
                payload=payload,
                delivery_id=delivery_id,
                comment_id=comment.get("id", 0),
                installation_id=installation_id,
                owner=owner,
                repo=repo_name,
                pr_number=pr_number,
                body=body,
                commenter=commenter,
            )
            if response:
                client = github_client() if not using_gh_cli_fallback else get_gh_cli_client()
                client.post_pr_comment(
                    installation_id, owner, repo_name, pr_number, response
                )
        except Exception as e:
            log.error(f"[{delivery_id}] Command handler failed: {e}")

    return Response(status_code=200)



async def _handle_checkbox_toggle(payload: dict, delivery_id: str) -> Response:
    """
    Handle checkbox toggle events from issue_comment edited webhooks.

    Parses which checkboxes were toggled, checks authorization, applies dedup,
    dispatches actions, and resets checkboxes.
    """
    comment = payload.get("comment", {})
    issue = payload.get("issue", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})

    # Must be a PR comment
    if "pull_request" not in issue:
        return Response(status_code=200)

    # Only process our own bot's comments — identified by checkbox pattern
    # in the comment body (not performed_via_github_app, which indicates
    # which app *created* the comment, not whether it contains our checkboxes)
    body = comment.get("body", "")
    if "- [ ] 🔍 Trigger review" not in body and "- [x] 🔍 Trigger review" not in body:
        # Not our comment (no checkbox pattern) — skip
        return Response(status_code=200)

    installation_id = installation.get("id")
    if not installation_id:
        return Response(status_code=200)

    owner = repo.get("full_name", "").split("/")[0] if repo.get("full_name") else ""
    repo_name = repo.get("name", "")
    pr_number = issue.get("number")
    commenter = comment.get("user", {}).get("login", "unknown")

    # Skip bot users (our own edits shouldn't trigger actions)
    if comment.get("user", {}).get("type") == "Bot":
        return Response(status_code=200)

    # Get PR author for authorization checks
    try:
        github = github_client()
        pr_details = github.get_pr_details(installation_id, owner, repo_name, pr_number)
        pr_author = pr_details.get("user", {}).get("login", "unknown")
    except Exception as e:
        log.warning(f"[{delivery_id}] Failed to fetch PR details for checkbox: {e}")
        return Response(status_code=200)

    # Handle the checkbox toggle
    try:
        from riptide.checkbox_handler import handle_checkbox_toggle

        triggered = handle_checkbox_toggle(
            payload=payload,
            github_client=github,
            state_store=_get_state_store(),
            installation_id=installation_id,
            owner=owner,
            repo=repo_name,
            pr_number=pr_number,
            commenter=commenter,
            pr_author=pr_author,
        )
        if triggered:
            log.info(
                f"[{delivery_id}] Checkbox triggered {triggered} on {owner}/{repo_name}#{pr_number}"
            )
    except Exception as e:
        log.error(f"[{delivery_id}] Checkbox handler failed: {e}")

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





# ── Init DB on startup ─────────────────────────────────────────────────────────


@app.on_event("startup")
def init_db():
    import sqlite3

    # Clean up stale checkbox trigger records on startup (non-fatal)
    try:
        _get_state_store().cleanup_stale_checkbox_triggers(max_age_seconds=3600)
    except Exception as e:
        log.warning(f"Startup checkbox cleanup failed (non-critical): {e}")

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

    # Recover any pending work from a previous process
    try:
        from riptide.state import StateStore
        state = StateStore()
        pending = state.recover_pending_work()
        if pending:
            log.info(f"Recovered {len(pending)} pending work items from previous process")
            # Spawn workers in background to avoid blocking startup
            import threading
            threading.Thread(
                target=_spawn_recovery_workers,
                args=(pending,),
                daemon=True,
                name="startup-recovery",
            ).start()
    except Exception as e:
        log.warning(f"Work recovery failed (non-fatal): {e}")


def _spawn_recovery_workers(pending_items: list[dict]):
    """Spawn workers for recovered pending items (runs in background thread).

    For now, transition items back to 'pending' so the cron poller re-processes
    them. Direct worker spawning requires installation_id + client context that
    isn't persisted in the work_queue payload. A future enhancement can persist
    that context and spawn workers directly.
    """
    try:
        from riptide.state import StateStore
        state = StateStore()

        for item in pending_items:
            work_id = item.get("id", "unknown")
            kind = item.get("kind")
            log.info(f"Recovery: transitioning {work_id} (kind={kind}) back to pending for cron pickup")
            # Transition back to pending so cron poller picks it up
            conn = state._get_conn()
            conn.execute(
                "UPDATE work_queue SET status='pending', pid=NULL WHERE id=? AND status='recovering'",
                (work_id,),
            )
            conn.commit()
    except Exception as e:
        log.error(f"Recovery worker spawner failed: {e}")
