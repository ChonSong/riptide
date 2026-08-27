# riptide/tests/test_bot_autonomy.py
"""
Tests for Bot 2 spawn retry/backoff and Companion Bot 2 state reporting.
Updated for stratified Hermes sessions architecture.
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

    def _gather_data_mock(self, *args, **kwargs):
        return {
            "files_changed": [],
            "diff_raw": "",
            "repo_tree": [],
            "god_nodes": [],
            "communities": [],
            "graph_context": {},
        }

    def test_spawn_succeeds_on_first_attempt(self):
        with patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.pipeline.async_conductor.create_stratified_review_pipeline") as mock_pipeline, \
             patch("riptide.pipeline.async_conductor.AsyncConductor") as mock_conductor, \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            mock_pipeline.return_value = {"id": "riptide-review-ChonSong-riptide-42", "workstreams": {}}
            mock_conductor.return_value.run.return_value = {
                "results": [{"status": "dispatched", "role": "probe"}]
            }

            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is True
            mock_conductor.return_value.run.assert_called_once()

    def test_spawn_retries_after_failure_then_succeeds(self):
        """After 2 failures, 3rd attempt succeeds."""
        with patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.pipeline.async_conductor.create_stratified_review_pipeline") as mock_pipeline, \
             patch("riptide.pipeline.async_conductor.AsyncConductor") as mock_conductor, \
             patch("riptide.deepthink._is_cron_available", return_value=True), \
             patch("time.sleep") as mock_sleep, \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            mock_pipeline.return_value = {"id": "riptide-review-ChonSong-riptide-42", "workstreams": {}}
            # First 2 attempts return empty results, 3rd succeeds
            mock_conductor.return_value.run.side_effect = [
                {"results": []},
                {"results": []},
                {"results": [{"status": "dispatched", "role": "probe"}]},
            ]

            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is True
            assert mock_conductor.return_value.run.call_count == 3
            # Exponential backoff: 5*2^1=10s then 5*2^2=20s
            delays = [c.args[0] for c in mock_sleep.call_args_list]
            assert delays == [10, 20]

    def test_spawn_gives_up_after_all_retries(self):
        """All 3 attempts fail — raises RuntimeError."""
        with patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.pipeline.async_conductor.create_stratified_review_pipeline") as mock_pipeline, \
             patch("riptide.pipeline.async_conductor.AsyncConductor") as mock_conductor, \
             patch("riptide.deepthink._is_cron_available", return_value=True), \
             patch("time.sleep") as mock_sleep, \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            mock_pipeline.return_value = {"id": "riptide-review-ChonSong-riptide-42", "workstreams": {}}
            # All attempts return empty results
            mock_conductor.return_value.run.return_value = {"results": []}

            with pytest.raises(RuntimeError, match="All 3 dispatch attempts failed"):
                _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert mock_conductor.return_value.run.call_count == 3
            assert mock_sleep.call_count == 2  # 5s then 10s (no sleep after final)

    def test_spawn_skips_attempt_when_hermes_unavailable(self):
        """_is_cron_available False -> skips that attempt entirely."""
        with patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.pipeline.async_conductor.create_stratified_review_pipeline") as mock_pipeline, \
             patch("riptide.pipeline.async_conductor.AsyncConductor") as mock_conductor, \
             patch("riptide.deepthink._is_cron_available", side_effect=[False, True]), \
             patch("time.sleep") as mock_sleep, \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            mock_pipeline.return_value = {"id": "riptide-review-ChonSong-riptide-42", "workstreams": {}}
            mock_conductor.return_value.run.return_value = {
                "results": [{"status": "dispatched", "role": "probe"}]
            }

            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is True
            # First attempt skipped (hermes unavailable), second attempt ran
            assert mock_conductor.return_value.run.call_count == 1

    def test_spawn_timeout_retries(self):
        """TimeoutExpired on attempts 1-2, success on 3."""
        with patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.pipeline.async_conductor.create_stratified_review_pipeline") as mock_pipeline, \
             patch("riptide.pipeline.async_conductor.AsyncConductor") as mock_conductor, \
             patch("riptide.deepthink._is_cron_available", return_value=True), \
             patch("time.sleep") as mock_sleep, \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            mock_pipeline.return_value = {"id": "riptide-review-ChonSong-riptide-42", "workstreams": {}}
            # First 2 attempts raise, 3rd succeeds
            mock_conductor.return_value.run.side_effect = [
                subprocess.TimeoutExpired(cmd="hermes", timeout=15),
                subprocess.TimeoutExpired(cmd="hermes", timeout=15),
                {"results": [{"status": "dispatched", "role": "probe"}]},
            ]

            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is True
            assert mock_conductor.return_value.run.call_count == 3

    def test_skips_when_review_already_pending(self):
        """If a review is already pending, raise RuntimeError."""
        with patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = False
            with pytest.raises(RuntimeError, match="review already pending"):
                _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")


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
            assert "will auto-review" in status


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
