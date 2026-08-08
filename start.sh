#!/bin/bash
# Riptide webhook server start script
# Loads .env and starts the server
cd "$(dirname "$0")" || exit 1
set -a
. ./.env
set +a
exec /home/sc/.hermes/hermes-agent/venv/bin/python3 server.py --prod
