# riptide/tests/test_bot_autonomy.py
"""
Tests for Bot 2 spawn retry/backoff and Companion Bot 2 state reporting.
"""

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from riptide.deepthink import _spawn_deepthink, _is_cron_available
from riptide.companion import Companion


# ── _spawn_deepthink retry/backoff ────────────────────────────────────────────


class TestSpawnRetry:
    """Verify exponential backoff and state-only-on-success behavior."""

    def _success_result(self):
        r = MagicMock()
        r.returncode = 0
        r.stdout = "cron-id-123"
        r.stderr = ""
        return r

    def test_spawn_succeeds_on_first_attempt(self):
        with patch("subprocess.run", return_value=self._success_result()) as mock_run, \
             patch("riptide.deepthink._is_cron_available", return_value=True):
            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is True
            assert mock_run.call_count == 1

    def test_spawn_retries_after_failure_then_succeeds(self):
        """After 2 failures, 3rd attempt succeeds — no timeout wait."""
        failures = [MagicMock(returncode=1, stderr="boom"), MagicMock(returncode=1, stderr="boom")]
        success = MagicMock(returncode=0, stdout="cron-id")

        with patch("subprocess.run", side_effect=failures + [success]) as mock_run, \
             patch("time.sleep") as mock_sleep, \
             patch("riptide.deepthink._is_cron_available", return_value=True):
            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is True
            assert mock_run.call_count == 3
            # Exponential backoff: 5*2^1=10s then 5*2^2=20s (attempt 0 has no sleep)
            delays = [c.args[0] for c in mock_sleep.call_args_list]
            assert delays == [10, 20]

    def test_spawn_gives_up_after_all_retries(self):
        """All 3 attempts fail — returns False, no exception."""
        failures = [MagicMock(returncode=1, stderr="x") for _ in range(3)]
        with patch("subprocess.run", side_effect=failures) as mock_run, \
             patch("time.sleep") as mock_sleep, \
             patch("riptide.deepthink._is_cron_available", return_value=True):
            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is False
            assert mock_run.call_count == 3
            assert mock_sleep.call_count == 2  # 5s then 10s (no sleep after final)

    def test_spawn_skips_attempt_when_hermes_unavailable(self):
        """_is_cron_available False -> skips that attempt entirely."""
        with patch("subprocess.run", return_value=self._success_result()) as mock_run, \
             patch("time.sleep") as mock_sleep, \
             patch("riptide.deepthink._is_cron_available", side_effect=[False, True]):
            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is True
            # First attempt skipped (no subprocess), second attempt ran
            assert mock_run.call_count == 1
    def test_spawn_timeout_retries(self):
        """TimeoutExpired on attempts 1-2, success on 3."""
        timeout = subprocess.TimeoutExpired(cmd="hermes", timeout=15)
        success = MagicMock(returncode=0, stdout="cron-id")
        with patch("subprocess.run", side_effect=[timeout, timeout, success]) as mock_run, \
             patch("time.sleep") as mock_sleep, \
             patch("riptide.deepthink._is_cron_available", return_value=True):
            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is True
            assert mock_run.call_count == 3


# ── Companion Bot 2 status footer ─────────────────────────────────────────────


class TestBot2Status:
    """Verify _get_bot2_status reads deepthink state and formats footer."""

    def test_no_state_file_returns_none(self, tmp_path):
        with patch("riptide.companion.Path") as mock_path, \
             patch.dict("os.environ", {"RIPTIDE_DATA_DIR": str(tmp_path)}):
            mock_path.side_effect = lambda *a, **k: __import__("pathlib").Path(*a, **k)
            assert Companion._get_bot2_status("ChonSong", "riptide", 42) is None

    def test_no_entry_returns_none(self, tmp_path):
        state_file = tmp_path / "deepthink_acted_prs.json"
        state_file.write_text(json.dumps({"other/repo#1": {"reviewed_at": "2026-07-31T00:00:00+00:00"}}))
        with patch.dict("os.environ", {"RIPTIDE_DATA_DIR": str(tmp_path)}):
            assert Companion._get_bot2_status("ChonSong", "riptide", 42) is None

    def test_reviewed_recently_returns_hours_ago(self, tmp_path):
        from datetime import datetime, timezone, timedelta
        reviewed = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        state_file = tmp_path / "deepthink_acted_prs.json"
        state_file.write_text(json.dumps({"ChonSong/riptide#42": {"reviewed_at": reviewed}}))
        with patch.dict("os.environ", {"RIPTIDE_DATA_DIR": str(tmp_path)}):
            status = Companion._get_bot2_status("ChonSong", "riptide", 42)
            assert status is not None
            assert "3h ago" in status
            assert "@riptide-bot review" in status

    def test_reviewed_long_ago_returns_will_autoreview(self, tmp_path):
        from datetime import datetime, timezone, timedelta
        reviewed = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        state_file = tmp_path / "deepthink_acted_prs.json"
        state_file.write_text(json.dumps({"ChonSong/riptide#42": {"reviewed_at": reviewed}}))
        with patch.dict("os.environ", {"RIPTIDE_DATA_DIR": str(tmp_path)}):
            status = Companion._get_bot2_status("ChonSong", "riptide", 42)
            assert status is not None
            assert "30h+ ago" in status
            assert "auto-review" in status

    def test_corrupted_state_returns_none(self, tmp_path):
        state_file = tmp_path / "deepthink_acted_prs.json"
        state_file.write_text("{not valid json")
        with patch.dict("os.environ", {"RIPTIDE_DATA_DIR": str(tmp_path)}):
            assert Companion._get_bot2_status("ChonSong", "riptide", 42) is None

    def test_format_comment_includes_bot2_status(self, tmp_path):
        from datetime import datetime, timezone, timedelta
        reviewed = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        state_file = tmp_path / "deepthink_acted_prs.json"
        state_file.write_text(json.dumps({"ChonSong/riptide#42": {"reviewed_at": reviewed}}))

        with patch.dict("os.environ", {"RIPTIDE_DATA_DIR": str(tmp_path)}), \
             patch("threading.Thread"):
            companion = Companion(MagicMock())
            body = companion._format_comment(
                "✨", "testuser", "Some TL;DR.", None,
                owner="ChonSong", repo="riptide", pr_number=42,
            )
            assert "🤖 Bot 2" in body

    def test_format_comment_skips_bot2_when_no_owner(self, tmp_path):
        with patch("threading.Thread"):
            companion = Companion(MagicMock())
            body = companion._format_comment("✨", "testuser", "Some TL;DR.", None)
            assert "🤖 Bot 2" not in body
