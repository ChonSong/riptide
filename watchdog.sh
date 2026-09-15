#!/bin/bash
# Riptide auto-update watchdog
# Checks if origin/main is ahead of local and restarts Riptide if so
# Run via cron every 5 minutes

set -e
# Overridable so the watchdog can be tested against a temp repo, and so it can
# never depend on whichever branch a dev checkout happens to have checked out.
WATCHDOG_REPO="${RIPTIDE_WATCHDOG_REPO:-/home/sc/workspace/riptide-prod}"
cd "$WATCHDOG_REPO"

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ "$CURRENT_BRANCH" != "main" ]; then
    exit 0
fi

git fetch --quiet origin main 2>/dev/null || exit 0
LOCAL=$(git rev-parse main 2>/dev/null)
REMOTE=$(git rev-parse origin/main 2>/dev/null)

# No change: nothing to do.
if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0
fi

# Restart only when origin/main is genuinely ahead (local main is an ancestor of
# it). Raw SHA inequality is also true when local main is ahead or has diverged —
# restarting then achieves nothing and hides a checkout an operator must fix.
if git merge-base --is-ancestor "$LOCAL" "$REMOTE" 2>/dev/null; then
    systemctl --user restart riptide.service 2>/dev/null || true
else
    echo "watchdog: local main ($LOCAL) is not an ancestor of origin/main ($REMOTE)" >&2
    echo "watchdog: checkout is ahead of or diverged from origin — operator action required" >&2
    exit 1
fi
