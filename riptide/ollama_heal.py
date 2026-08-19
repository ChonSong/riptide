#!/usr/bin/env python3
"""Self-healing Ollama watchdog.

Called before LLM-dependent operations (Tier-2 ELI5 enrichment) to ensure
Ollama is reachable. If the API is down, attempt a systemd restart.

Usage:
    python3 ollama_heal.py              # probe + restart if needed
    python3 ollama_heal.py --wait 30    # wait up to 30s for recovery
    python3 ollama_heal.py --check      # probe only, no restart

Exit codes:
    0 = Ollama is healthy (or recovered)
    1 = Ollama is down and could not be restarted
    2 = systemd service not found (install issue)
"""

import argparse
import logging
import os
import subprocess
import sys
import time

import requests

logger = logging.getLogger(__name__)

OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434")
SYSTEMD_SERVICE = "ollama.service"
DEFAULT_TIMEOUT = 30  # seconds to wait for recovery
PROBE_ENDPOINT = "/api/tags"


def is_healthy(base_url: str = OLLAMA_BASE) -> bool:
    """Check if Ollama API is responding (2xx = healthy)."""
    try:
        resp = requests.get(f"{base_url}{PROBE_ENDPOINT}", timeout=3)
        if not (200 <= resp.status_code < 300):
            logger.debug("Probe returned HTTP %d from %s", resp.status_code, base_url)
        return 200 <= resp.status_code < 300
    except Exception as e:
        logger.debug("Probe failed: %s", e)
        return False


def is_systemd_service_loaded() -> bool:
    """Check if the systemd user service exists and is loaded."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-enabled", SYSTEMD_SERVICE],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # 0=enabled, 3=static (both mean "loaded"); 1=disabled; 4=not-found
        if result.returncode in (0, 3):
            return True
        if result.returncode == 4:
            return False
        # For ambiguous return codes, check if service exists at all
        result2 = subprocess.run(
            ["systemctl", "cat", SYSTEMD_SERVICE],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result2.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("systemd detection failed: %s", e)
        return False


def restart_ollama() -> bool:
    """Restart the Ollama systemd user service."""
    logger.info("Restarting %s...", SYSTEMD_SERVICE)
    try:
        result = subprocess.run(
            ["systemctl", "--user", "restart", SYSTEMD_SERVICE],
            capture_output=True,
            text=True,
        )
    except OSError as e:
        logger.error("Restart command failed: %s", e)
        return False
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "(no stderr)"
        logger.error("Restart failed (exit %d): %s", result.returncode, stderr)
        return False
    return True


def wait_for_recovery(timeout: int = DEFAULT_TIMEOUT, base_url: str = OLLAMA_BASE) -> bool:
    """Poll Ollama until it responds or timeout expires."""
    start_time = time.time()
    deadline = start_time + timeout
    while time.time() < deadline:
        if is_healthy(base_url):
            elapsed = int(time.time() - start_time)
            logger.info("Ollama recovered after %ds", elapsed)
            return True
        remaining = int(deadline - time.time())
        logger.info("Waiting for Ollama... (%ds remaining)", remaining)
        time.sleep(min(5, remaining))
    return False


def heal(wait_timeout: int = DEFAULT_TIMEOUT, base_url: str = OLLAMA_BASE) -> int:
    """Self-heal Ollama. Returns exit code."""
    # 1. Already healthy?
    if is_healthy(base_url):
        logger.info("Ollama is healthy")
        return 0

    logger.warning("Ollama is unreachable at %s", base_url)

    # 2. Is the systemd service available?
    if not is_systemd_service_loaded():
        logger.error(
            "%s not found in systemd user units. "
            "Run: systemctl --user enable --now %s",
            SYSTEMD_SERVICE,
            SYSTEMD_SERVICE,
        )
        return 2

    # 3. Attempt restart
    if not restart_ollama():
        return 1

    # 4. Wait for recovery
    if wait_for_recovery(wait_timeout, base_url):
        return 0

    logger.error("Ollama did not recover within %ds", wait_timeout)
    return 1


def main():
    parser = argparse.ArgumentParser(description="Self-healing Ollama watchdog")
    parser.add_argument("--check", action="store_true", help="Probe only, no restart")
    parser.add_argument(
        "--wait",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Seconds to wait for recovery (default: {DEFAULT_TIMEOUT})",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.check:
        sys.exit(0 if is_healthy() else 1)

    sys.exit(heal(wait_timeout=args.wait))


if __name__ == "__main__":
    main()
