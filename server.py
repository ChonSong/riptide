#!/usr/bin/env python3
"""
server.py — Riptide webhook server entry point.

Usage:
  python server.py                    # development (single-worker uvicorn)
  python server.py --prod             # production (gunicorn multi-worker)

Environment:
  GITHUB_APP_ID              GitHub App ID (default: 4262983)
  GITHUB_PRIVATE_KEY_PATH    Path to .pem private key
  GITHUB_WEBHOOK_SECRET      Webhook secret for signature verification
  GITHUB_APP_SLUG            App slug (default: octopus-selfhost)
  RIPTIDE_DATA_DIR           Where to store logs, metadata.db, state (default: /tmp/riptide)
  HOST                       Bind host (default: 0.0.0.0)
  PORT                       Bind port (default: 8477)
"""
import logging
import logging.handlers
import os
import sys
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8477"))
DATA_DIR = Path(os.environ.get("RIPTIDE_DATA_DIR", "/tmp/riptide"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROD = "--prod" in sys.argv

# ── Logging to file (always) + stdout (development) ──────────────────────────
log_path = DATA_DIR / "riptide.log"
rotating = logging.handlers.RotatingFileHandler(
    log_path, maxBytes=10 * 1024 * 1024, backupCount=3
)
rotating.setFormatter(logging.Formatter(
    "%(asctime)s [%(process)d] %(name)s: %(message)s"
))

root = logging.getLogger()
root.setLevel(logging.INFO)
root.addHandler(rotating)

# In dev mode also log to stderr
if not PROD:
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(process)d] %(name)s: %(message)s"
    ))
    root.addHandler(console)

log = logging.getLogger("riptide")

# ── Import app after config ───────────────────────────────────────────────────
from riptide.webhook import app

if __name__ == "__main__":
    if PROD:
        # Production: gunicorn multi-worker
        try:
            import gunicorn.app.wsgiapp  # noqa: F401 — ensure importable
        except ImportError:
            log.warning("gunicorn not installed, falling back to uvicorn single-worker")
            import uvicorn
            log.info("Starting Riptide (dev mode) on %s:%s", HOST, PORT)
            uvicorn.run(app, host=HOST, port=PORT, log_level="info")
            sys.exit(0)

        log.info(
            "Starting Riptide (production) on %s:%s, logging to %s",
            HOST, PORT, log_path,
        )
        # Cast argv so gunicorn CLI can find our app
        sys.argv = [
            "gunicorn",
            "riptide.webhook:app",
            "--bind", f"{HOST}:{PORT}",
            "--workers", "2",
            "--worker-class", "uvicorn.workers.UvicornWorker",
            "--timeout", "120",
            "--access-logfile", "-",
        ]
        gunicorn.app.wsgiapp.run()
    else:
        import uvicorn
        log.info("Starting Riptide (dev mode) on %s:%s", HOST, PORT)
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
