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

exec /home/sc/.hermes/hermes-agent/venv/bin/python3 server.py --prod
