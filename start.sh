#!/bin/bash
# Riptide webhook server start script
# Loads .env, pulls latest code (main branch only), and starts the server
cd "$(dirname "$0")" || exit 1
set -a
. ./.env
set +a

# Auto-update: if running on main branch, fast-forward to latest origin/main
# This ensures merges to main take effect on next Riptide restart
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ "$CURRENT_BRANCH" = "main" ]; then
    git fetch --quiet origin main 2>/dev/null && git merge --ff-only origin/main --quiet 2>/dev/null
fi

# Pin the active Hermes profile to `riptide` so cron jobs spawned via
# `hermes cron create` inherit the lean riptide SOUL (~667B vs ~2.6KB
# for the default profile), cutting input tokens per review run.
# Honors an operator-supplied HERMES_PROFILE in .env or the systemd
# environment; only sets the default if unset. See
# ~/.hermes/profiles/riptide/SOUL.md and hermes_cli/profiles.py.
export HERMES_PROFILE="${HERMES_PROFILE:-riptide}"

exec /home/sc/.hermes/hermes-agent/venv/bin/python3 server.py --prod