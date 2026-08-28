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
import os
import subprocess
import sys
import time

import requests

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
SYSTEMD_SERVICE = "ollama.service"
DEFAULT_TIMEOUT = 10  # seconds to wait for recovery (webhook-friendly)


def is_healthy(base_url: str = OLLAMA_BASE_URL) -> bool:
    """Check if Ollama API is responding."""
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def is_systemd_available() -> bool:
    """Check if systemd user session is functional."""
    return os.environ.get("DBUS_SESSION_BUS_ADDRESS") is not None and os.path.exists(
        f"/run/user/{os.getuid()}"
    )


class SystemdStatus:
    """Distinct outcomes for systemd service probing."""
    HEALTHY = "healthy"       # service is enabled/static
    ABSENT = "absent"         # unit file not found (FileNotFoundError)
    DISABLED = "disabled"     # unit exists but not enabled (rc=1)
    PROBE_FAILED = "probe_failed"  # systemctl unavailable or timed out

def is_systemd_service_loaded() -> str:
    """Check systemd service status. Returns SystemdStatus string."""
    if not is_systemd_available():
        return SystemdStatus.PROBE_FAILED
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-enabled", SYSTEMD_SERVICE],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        print("❌ systemctl not found", file=sys.stderr)
        return SystemdStatus.ABSENT
    except subprocess.TimeoutExpired:
        print(f"❌ systemd probe timed out after 5s", file=sys.stderr)
        return SystemdStatus.PROBE_FAILED

    if result.returncode == 0:
        return SystemdStatus.HEALTHY
    elif result.returncode == 3:
        return SystemdStatus.HEALTHY  # static
    elif result.returncode == 1:
        # Check if unit exists at all
        try:
            check = subprocess.run(
                ["systemctl", "--user", "cat", SYSTEMD_SERVICE],
                capture_output=True, text=True, timeout=5,
            )
            if check.returncode != 0 or "No files found" in check.stdout:
                return SystemdStatus.ABSENT
        except Exception:
            pass
        return SystemdStatus.DISABLED
    else:
        return SystemdStatus.PROBE_FAILED


def restart_ollama() -> bool:
    """Restart the Ollama systemd user service."""
    print(f"🛠  Restarting {SYSTEMD_SERVICE}...")
    try:
        result = subprocess.run(
            ["systemctl", "--user", "restart", SYSTEMD_SERVICE],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print(f"❌ Restart timed out after 30s", file=sys.stderr)
        return False

    if result.returncode != 0:
        print(f"❌ Restart failed: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def wait_for_recovery(timeout: int = DEFAULT_TIMEOUT, base_url: str = OLLAMA_BASE_URL) -> bool:
    """Poll Ollama until it responds or timeout expires. Uses monotonic clock."""
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        if is_healthy(base_url):
            print(f"✅ Ollama recovered after {attempt}s")
            return True
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            break
        print(f"⏳ Waiting for Ollama... ({remaining}s remaining)")
        time.sleep(min(5, remaining))
    return False


def heal(wait_timeout: int = DEFAULT_TIMEOUT, base_url: str = OLLAMA_BASE_URL) -> int:
    """Self-heal Ollama. Returns exit code."""
    # 1. Already healthy?
    if is_healthy(base_url):
        print("✅ Ollama is healthy")
        return 0

    print(f"⚠️  Ollama is unreachable at {base_url}")

    # 2. Probe systemd directly with a bounded timeout.
    #    is_systemd_available() checks env vars, but we probe systemctl --user
    #    directly for a more accurate picture.
    service_status = is_systemd_service_loaded()

    if service_status == SystemdStatus.ABSENT:
        print(f"❌ {SYSTEMD_SERVICE} not found in systemd user units", file=sys.stderr)
        print("   Run: systemctl --user enable --now ollama.service", file=sys.stderr)
        return 2
    elif service_status == SystemdStatus.DISABLED:
        print(f"⚠️  {SYSTEMD_SERVICE} is installed but not enabled", file=sys.stderr)
        print("   Run: systemctl --user enable --now ollama.service", file=sys.stderr)
        # Attempt restart anyway — may still work
    elif service_status == SystemdStatus.HEALTHY:
        # Service is enabled but Ollama is down — restart it
        pass
    else:
        # PROBE_FAILED — systemctl unavailable or timed out.
        # Fall back to Docker / non-systemd path:
        #     (Ollama may be running as a container or standalone process)
        print("⚠️  systemd probe failed — skipping restart (Docker/non-systemd environment)")
        # If we reach here, Ollama is down and we can't restart it.
        # Return 1 (down, couldn't restart) rather than 2 (systemd issue)
        return 1

    # 3. DISABLED or HEALTHY — attempt restart in both cases
    if not restart_ollama():
        return 1

    # 4. Wait for recovery
    if wait_for_recovery(wait_timeout, base_url):
        return 0

    print(f"❌ Ollama did not recover within {wait_timeout}s", file=sys.stderr)
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

    if args.check:
        sys.exit(0 if is_healthy() else 1)

    sys.exit(heal(wait_timeout=args.wait))


if __name__ == "__main__":
    main()
