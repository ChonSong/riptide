#!/bin/bash
# Riptide auto-update watchdog
# Checks if origin/main is ahead of local and restarts Riptide if so
# Run via cron every 5 minutes

set -e
cd /home/sc/workspace/riptide

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ "$CURRENT_BRANCH" != "main" ]; then
    exit 0
fi

git fetch --quiet origin main 2>/dev/null || exit 0
LOCAL=$(git rev-parse main 2>/dev/null)
REMOTE=$(git rev-parse origin/main 2>/dev/null)

if [ "$LOCAL" != "$REMOTE" ]; then
    systemctl --user restart riptide.service 2>/dev/null || true
fi
