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
WAIT_TIMEOUT=300  # 5 minutes max wait
POLL_INTERVAL=10
DEPLOY_BRANCH="${RIPTIDE_DEPLOY_BRANCH:-main}"
LOCK_FILE="${RIPTIDE_DEPLOY_LOCK:-/tmp/riptide-deploy.lock}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

log "=== Deploy started ==="

cd "$REPO_DIR"

# ── 0. Interprocess lock — serialize concurrent deploys ──────────────────────
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    log "SKIP: another deployment is already in progress (lock held)"
    exit 0
fi
# Lock released automatically when fd 200 closes on exit (any path)
trap 'exec 200>&-' EXIT


# ── 1. Wait for active Hermes sessions ──────────────────────────────────────
log "Checking for active Hermes cron jobs..."

waited=0
while [ $waited -lt $WAIT_TIMEOUT ]; do
    # Check for running hermes agent processes (cron-spawned sessions)
    # Use extended regex (-E) for proper group matching
    # Exclude the cron scheduler itself, grep, and this script
    running=$(pgrep -Ef "hermes.*(cron|agent)" 2>/dev/null | grep -v -E "(pgrep|deploy\.sh|hermes cron)" | wc -l || true)
    if [ "$running" -eq 0 ]; then
        log "No active Hermes sessions — proceeding"
        break
    fi
    log "Waiting for $running active Hermes session(s) to finish (${waited}s elapsed)..."
    sleep $POLL_INTERVAL
    waited=$((waited + POLL_INTERVAL))
done

if [ $waited -ge $WAIT_TIMEOUT ]; then
    log "TIMEOUT: sessions still running after ${WAIT_TIMEOUT}s — deferring deploy to next merge"
    exit 0
fi

# ── 2. Pull latest main ─────────────────────────────────────────────────────
log "Pulling latest origin/${DEPLOY_BRANCH}..."
if ! git pull origin "$DEPLOY_BRANCH" --ff-only >> "$LOG_FILE" 2>&1; then
    log "ERROR: git pull failed"
    exit 1
fi

# ── 3. Clean stale bytecode ─────────────────────────────────────────────────
log "Cleaning __pycache__..."
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# ── 4. Restart service ──────────────────────────────────────────────────────
log "Restarting riptide.service..."
if ! systemctl --user restart riptide.service >> "$LOG_FILE" 2>&1; then
    log "ERROR: systemctl restart failed"
    exit 1
fi

# ── 5. Verify ───────────────────────────────────────────────────────────────
sleep 3
if systemctl --user is-active --quiet riptide.service; then
    log "=== Deploy complete — service active ==="
else
    log "=== Deploy FAILED — service not active ==="
    exit 1
fi
