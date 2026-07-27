#!/usr/bin/env python3
"""
server.py — Riptide webhook server entry point.

Usage:
  python server.py                    # development
  gunicorn server:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8477  # production

Environment:
  GITHUB_APP_ID              GitHub App ID (default: 4262983)
  GITHUB_PRIVATE_KEY_PATH    Path to .pem private key
  GITHUB_WEBHOOK_SECRET      Webhook secret for signature verification
  GITHUB_APP_SLUG            App slug (default: octopus-selfhost)
  RIPTIDE_DATA_DIR           Where to store metadata.db
  HOST                       Bind host (default: 0.0.0.0)
  PORT                       Bind port (default: 8477)
"""
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(process)d] %(name)s: %(message)s",
)
log = logging.getLogger("riptide")

# ── Config ────────────────────────────────────────────────────────────────────
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8477"))

# ── Import app after config ───────────────────────────────────────────────────
from riptide.webhook import app

if __name__ == "__main__":
    import uvicorn
    log.info(f"Starting Riptide webhook server on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
