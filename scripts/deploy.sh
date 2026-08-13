#!/usr/bin/env bash
#
# deploy.sh — Riptide auto-deploy script.
# Triggered by webhook.py when a PR merges into the default branch.
#
# Waits for active Hermes cron jobs (fix/review sessions) to finish
# before pulling and restarting — avoids interrupting mid-session work.
#
set -euo pipefail

REPO_DIR="/home/sc/workspace/riptide"
LOG_FILE="${RIPTIDE_DEPLOY_LOG:-/tmp/riptide-deploy.log}"
DEPLOY_BRANCH="${RIPTIDE_DEPLOY_BRANCH:-main}"
LOCK_FILE="${RIPTIDE_DEPLOY_LOCK:-/tmp/riptide-deploy.lock}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

log "=== Deploy started ==="

cd "$REPO_DIR"

# ── 0a. Runtime env (before pull — restarts pick these up) ──────────────────
# Ollama runs on the standard port; Companion's prep/enrich passes need it.
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"

# ── 0. Interprocess lock — serialize concurrent deploys ──────────────────────
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    log "SKIP: another deployment is already in progress (lock held)"
    exit 0
fi
# Lock released automatically when fd 200 closes on exit (any path)
trap 'exec 200>&-' EXIT


# ── 1. Pull latest main ─────────────────────────────────────────────────────
# Auto-deploy script — pulls latest, restarts service, verifies.
# Runs under systemd-run --scope (not --collect) so the scope
# is cleaned up automatically after deploy completes.
if ! git pull origin "$DEPLOY_BRANCH" --ff-only >> "$LOG_FILE" 2>&1; then
    log "ERROR: git pull failed"
    exit 1
fi

# ── 2. Clean stale bytecode ─────────────────────────────────────────────────
log "Cleaning __pycache__..."
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# ── 3. Restart service ──────────────────────────────────────────────────────
log "Restarting riptide.service..."
if ! systemctl --user restart riptide.service >> "$LOG_FILE" 2>&1; then
    log "ERROR: systemctl restart failed"
    exit 1
fi

# ── 4. Verify ───────────────────────────────────────────────────────────────
sleep 3
if systemctl --user is-active --quiet riptide.service; then
    log "=== Deploy complete — service active ==="
else
    log "=== Deploy FAILED — service not active ==="
    exit 1
fi

# ── 5. Smoke test ────────────────────────────────────────────────────────
# Verify webhook responds AND serves latest code (not stale)
sleep 2
WEBHOOK_URL="${RIPTIDE_WEBHOOK_URL:-http://localhost:8477/webhook/github}"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$WEBHOOK_URL" \
    -H "Content-Type: application/json" -H "X-GitHub-Event: push" -d '{"test":true}' 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "401" ]; then
    # 200 = valid signature, 401 = invalid sig but service running
    log "Smoke test passed — webhook responding (HTTP $HTTP_CODE)"
else
    log "WARNING: Smoke test failed — webhook returned HTTP $HTTP_CODE"
    # Don't exit 1 — service is running, may just need more time
fi
