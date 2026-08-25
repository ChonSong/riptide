#!/usr/bin/env python3
"""ci_verifier.py — CI status verification pipeline stage.

Polls GitHub CI checks after a fix push, classifies failures, and returns
a structured verdict for the Conductor to act on.

Fixable failures (test/lint): can be retried once by the fix loop.
Non-fixable failures (CodeRabbit, review-required, GitGuardian): escalate to human.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Any

log = logging.getLogger(__name__)

# CI check categories — which failures the fixer can auto-retry vs escalate.
FIXABLE_CHECKS = {
    "test-required",       # pytest failures — deterministic, code-addressable
    "agentlint",           # lint failures — deterministic, code-addressable
    "continuous-integration",  # generic CI group name
}

NON_FIXABLE_CHECKS = {
    "riptide-review-required",  # AI review gate — needs human judgment
    "coderabbit",               # AI review — false-positive prone
    "gitguardian",              # security scan — needs human triage
    "codeql",                   # security analysis — needs human triage
}

# Timeout for CI polling (seconds) — CI typically finishes in 2-5 minutes.
POLL_TIMEOUT = 600  # 10 minutes
POLL_INTERVAL = 30  # seconds between polls


class CIVerifier:
    """Polls GitHub CI checks and classifies results.

    Uses `gh pr checks` for structured JSON output of all check statuses.
    """

    def __init__(self, owner: str, repo: str, pr_number: int):
        self.owner = owner
        self.repo = repo
        self.pr_number = pr_number

    def poll(self, timeout: int = POLL_TIMEOUT, interval: int = POLL_INTERVAL) -> dict[str, Any]:
        """Poll CI until all checks complete or timeout.

        Returns structured result:
        {
            "status": "success" | "failure" | "timeout" | "error",
            "checks": [...],           # all check details
            "failed": [...],           # failed checks
            "fixable": [...],          # failed checks that are fixable
            "non_fixable": [...],      # failed checks that need human
            "passed": [...],           # passed checks
            "pending": [...],          # still running (on timeout)
            "duration_s": float,       # total polling duration
            "poll_count": int,         # number of poll attempts
        }
        """
        start = time.monotonic()
        poll_count = 0
        transient_failures = 0
        MAX_TRANSIENT = 3

        while True:
            poll_count += 1
            elapsed = time.monotonic() - start

            checks = self._fetch_checks()
            if checks is None:
                transient_failures += 1
                if transient_failures < MAX_TRANSIENT:
                    time.sleep(interval)
                    continue
                return {
                    "status": "error",
                    "error": "Failed to fetch CI checks",
                    "checks": [],
                    "failed": [],
                    "fixable": [],
                    "non_fixable": [],
                    "passed": [],
                    "pending": [],
                    "duration_s": round(elapsed, 2),
                    "poll_count": poll_count,
                }

            transient_failures = 0  # Reset on successful fetch

            # Classify checks by state
            passed = [c for c in checks if c.get("state") == "success"]
            failed = [c for c in checks if c.get("state") == "failure"]
            pending = [c for c in checks if c.get("state") in ("pending", "in_progress", "queued")]

            # All checks complete?
            if not pending:
                fixable = [c for c in failed if self._is_fixable(c)]
                non_fixable = [c for c in failed if not self._is_fixable(c)]

                return {
                    "status": "success" if not failed else "failure",
                    "checks": checks,
                    "failed": failed,
                    "fixable": fixable,
                    "non_fixable": non_fixable,
                    "passed": passed,
                    "pending": [],
                    "duration_s": round(elapsed, 2),
                    "poll_count": poll_count,
                }

            # Timeout?
            if elapsed >= timeout:
                fixable = [c for c in failed if self._is_fixable(c)]
                non_fixable = [c for c in failed if not self._is_fixable(c)]

                return {
                    "status": "timeout",
                    "checks": checks,
                    "failed": failed,
                    "fixable": fixable,
                    "non_fixable": non_fixable,
                    "passed": passed,
                    "pending": pending,
                    "duration_s": round(elapsed, 2),
                    "poll_count": poll_count,
                }

            time.sleep(interval)

    def _fetch_checks(self) -> list[dict[str, Any]] | None:
        """Fetch CI checks via `gh pr checks` JSON output."""
        try:
            result = subprocess.run(
                [
                    "gh", "pr", "checks", str(self.pr_number),
                    "--repo", f"{self.owner}/{self.repo}",
                    "--json", "name,state,workflow,event",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                log.warning("gh pr checks failed (rc=%d): %s", result.returncode, result.stderr.strip())
                return None
            return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            return None

    def _is_fixable(self, check: dict[str, Any]) -> bool:
        """Determine if a failed check is auto-fixable by the fixer."""
        name = check.get("name", "").lower()
        # Check against known fixable patterns
        for pattern in FIXABLE_CHECKS:
            if pattern in name:
                return True
        # Check against known non-fixable patterns
        for pattern in NON_FIXABLE_CHECKS:
            if pattern in name:
                return False
        # Unknown check — assume non-fixable (escalate to human)
        return False

    def format_report(self, result: dict[str, Any]) -> str:
        """Format CI result as a human-readable comment snippet."""
        status = result["status"]

        if status == "success":
            return "✅ All CI checks passed"

        if status == "timeout":
            pending = ", ".join(c.get("name", "?") for c in result.get("pending", []))
            return f"⏰ CI timeout — still pending: {pending}"

        if status == "error":
            return "⚠️ Could not fetch CI status"

        # failure
        lines = ["❌ CI checks failed:"]
        fixable_names = {c.get("name") for c in result.get("fixable", [])}
        for check in result.get("failed", []):
            name = check.get("name", "unknown")
            fixable = " (fixable)" if name in fixable_names else " (needs human)"
            lines.append(f"  - {name}{fixable}")

        if result.get("fixable"):
            lines.append(f"\n🔧 {len(result['fixable'])} fixable — will retry once")

        return "\n".join(lines)
