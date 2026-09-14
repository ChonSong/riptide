"""Tests for the auto-update watchdog (`watchdog.sh`).

The script is executed for real with `git` and `systemctl` shimmed onto PATH, so
each branch is exercised — no change, remote ahead, local ahead, diverged —
without touching the live checkout or restarting the service.
"""

import os
import pathlib
import subprocess

# Captured at import time: several test modules monkeypatch subprocess.run, and a
# test that shells out must not be at the mercy of whatever else is running.
_REAL_RUN = subprocess.run

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WATCHDOG = REPO_ROOT / "watchdog.sh"

# Emulates only the git calls watchdog.sh makes.
FAKE_GIT = """#!/bin/bash
case "$1 $2" in
  "rev-parse --abbrev-ref") echo "main"; exit 0 ;;
  "rev-parse main") echo "${FAKE_LOCAL}"; exit 0 ;;
  "rev-parse origin/main") echo "${FAKE_REMOTE}"; exit 0 ;;
  "fetch --quiet") exit 0 ;;
  "merge-base --is-ancestor") exit "${FAKE_ANCESTOR_EXIT:-0}" ;;
esac
exit 0
"""

FAKE_SYSTEMCTL = """#!/bin/bash
echo "$@" >> "${SYSTEMCTL_LOG}"
exit 0
"""


def _run(tmp_path, local, remote, ancestor_exit):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("git", FAKE_GIT), ("systemctl", FAKE_SYSTEMCTL)):
        script = bin_dir / name
        script.write_text(body)
        script.chmod(0o755)

    log = tmp_path / "systemctl.log"
    env = dict(os.environ)
    env.update(
        {
            # Fixed PATH: inheriting the suite's PATH lets another test's
            # mutation decide whether the shims are found at all.
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE_LOCAL": local,
            "FAKE_REMOTE": remote,
            "FAKE_ANCESTOR_EXIT": str(ancestor_exit),
            "SYSTEMCTL_LOG": str(log),
        }
    )
    proc = _REAL_RUN(
        ["bash", str(WATCHDOG)], capture_output=True, text=True, env=env
    )
    return proc, (log.read_text() if log.exists() else "")


def test_script_exists_and_is_valid_shell():
    assert WATCHDOG.exists()
    syntax = _REAL_RUN(["bash", "-n", str(WATCHDOG)], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr


def test_no_change_does_not_restart(tmp_path):
    proc, log = _run(tmp_path, "aaa", "aaa", 0)
    assert proc.returncode == 0
    assert log == ""


def test_remote_ahead_restarts(tmp_path):
    proc, log = _run(tmp_path, "aaa", "bbb", 0)
    assert proc.returncode == 0
    assert "restart riptide.service" in log


def test_local_ahead_is_an_operator_error_not_a_restart(tmp_path):
    """Local-only commits mean raw SHA inequality is true but nothing to deploy.

    Restarting would achieve nothing and loop every 5 minutes; the operator
    needs to know the checkout is ahead of origin instead.
    """
    proc, log = _run(tmp_path, "bbb", "aaa", 1)
    assert log == ""
    assert proc.returncode != 0
    assert "operator action required" in proc.stderr


def test_diverged_checkout_is_an_operator_error(tmp_path):
    proc, log = _run(tmp_path, "bbb", "ccc", 1)
    assert log == ""
    assert proc.returncode != 0
    assert "operator action required" in proc.stderr
