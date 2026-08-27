# riptide/tests/test_deepthink.py
"""
Tests for Riptide Deepthink bot (Bot 2).
Covers stratified pipeline creation, spawn logic, LOC filtering, state save/load, and dedup.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from riptide.deepthink import (
    _spawn_deepthink,
    _is_cron_available,
    _load_state,
    _save_state,
    _was_reviewed_today,
    _gather_review_data,
    _build_orchestrator_prompt,
    MIN_LOC_CHANGED,
    STALENESS_MINUTES,
    STATE_FILE,
)
from riptide.state import StateStore


# ── _spawn_deepthink tests ──────────────────────────────────────────────────


class TestSpawnDeepthink:
    """Tests for _spawn_deepthink function (stratified pipeline architecture)."""

    def _gather_data_mock(self, *args, **kwargs):
        return {
            "files_changed": [{"filename": "test.py", "additions": 100, "deletions": 50}],
            "diff_raw": "+ line 1\n- line 2\n",
            "repo_tree": ["test.py", "main.py"],
            "god_nodes": [{"name": "test.py", "edges": 5}],
            "communities": [{"name": "core", "members": ["test.py"]}],
            "graph_context": {"raw": "test.py affects main.py"},
        }

    def test_spawn_creates_stratified_pipeline(self):
        """_spawn_deepthink creates a stratified review pipeline and dispatches probe."""
        with patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.pipeline.async_conductor.create_stratified_review_pipeline") as mock_pipeline, \
             patch("riptide.pipeline.async_conductor.AsyncConductor") as mock_conductor, \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            mock_pipeline.return_value = {
                "id": "riptide-review-ChonSong-riptide-42",
                "workstreams": {"ws-1": {}, "ws-2": {}},
            }
            mock_conductor.return_value.run.return_value = {
                "results": [{"status": "dispatched", "role": "probe"}]
            }

            result = _spawn_deepthink(
                owner="ChonSong",
                repo="riptide",
                pr_number=42,
                pr_title="feat: add bar",
                pr_author="test-user",
                total_loc=200,
                head_sha="abc123def456",
            )

            assert result is True
            mock_pipeline.assert_called_once()
            mock_conductor.assert_called_once_with("riptide-review-ChonSong-riptide-42")
            mock_conductor.return_value.run.assert_called_once()

    def test_spawn_dispatches_probe_first(self):
        """Stratified pipeline dispatches probe as the first worker."""
        with patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.pipeline.async_conductor.create_stratified_review_pipeline") as mock_pipeline, \
             patch("riptide.pipeline.async_conductor.AsyncConductor") as mock_conductor, \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            mock_pipeline.return_value = {
                "id": "riptide-review-ChonSong-riptide-42",
                "workstreams": {"ws-1-probe": {"role": "probe"}},
            }
            mock_conductor.return_value.run.return_value = {
                "results": [{"status": "dispatched", "role": "probe"}]
            }

            _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")

            # Verify conductor was created with the correct track ID
            mock_conductor.assert_called_once_with("riptide-review-ChonSong-riptide-42")

    def test_spawn_fails_when_dispatch_fails(self):
        """When dispatch fails, mark job as failed and return False."""
        with patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.pipeline.async_conductor.create_stratified_review_pipeline") as mock_pipeline, \
             patch("riptide.pipeline.async_conductor.AsyncConductor") as mock_conductor, \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            mock_state.return_value.mark_failed.reset_mock()
            mock_pipeline.return_value = {
                "id": "riptide-review-ChonSong-riptide-42",
                "workstreams": {},
            }
            mock_conductor.return_value.run.return_value = {
                "results": [{"status": "failed", "message": "dispatch failed"}]
            }

            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")

            assert result is False
            mock_state.return_value.mark_failed.assert_called_once()

    def test_spawn_raises_after_retries(self):
        """All dispatch attempts fail — should raise RuntimeError."""
        with patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.pipeline.async_conductor.create_stratified_review_pipeline") as mock_pipeline, \
             patch("riptide.pipeline.async_conductor.AsyncConductor") as mock_conductor, \
             patch("riptide.deepthink._is_cron_available", return_value=True), \
             patch("time.sleep"), \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            mock_pipeline.return_value = {
                "id": "riptide-review-ChonSong-riptide-42",
                "workstreams": {},
            }
            # All attempts return no results
            mock_conductor.return_value.run.return_value = {"results": []}

            with pytest.raises(RuntimeError, match="All 3 dispatch attempts failed"):
                _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")

    def test_spawn_skips_when_review_already_pending(self):
        """When review already pending, raise RuntimeError."""
        with patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = False

            with pytest.raises(RuntimeError, match="review already pending"):
                _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")

    def test_spawn_data_gathering_failure_raises(self):
        """Data gathering failure raises RuntimeError."""
        with patch("riptide.deepthink._gather_review_data", side_effect=Exception("gh CLI failed")), \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True

            with pytest.raises(RuntimeError, match="Failed to create stratified pipeline"):
                _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")

    def test_spawn_pipeline_creation_failure_raises(self):
        """Pipeline creation failure raises RuntimeError."""
        with patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.pipeline.async_conductor.create_stratified_review_pipeline", side_effect=Exception("DB error")), \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True

            with pytest.raises(RuntimeError, match="Failed to create stratified pipeline"):
                _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")

    def test_spawn_includes_pr_details_in_pipeline(self):
        """Pipeline is created with correct PR details."""
        with patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.pipeline.async_conductor.create_stratified_review_pipeline") as mock_pipeline, \
             patch("riptide.pipeline.async_conductor.AsyncConductor") as mock_conductor, \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            mock_pipeline.return_value = {
                "id": "riptide-review-ChonSong-riptide-42",
                "workstreams": {},
            }
            mock_conductor.return_value.run.return_value = {
                "results": [{"status": "dispatched", "role": "probe"}]
            }

            _spawn_deepthink(
                owner="ChonSong",
                repo="riptide",
                pr_number=42,
                pr_title="feat: add bar",
                pr_author="test-user",
                total_loc=200,
                head_sha="abc123def456",
            )

            call_kwargs = mock_pipeline.call_args[1]
            assert call_kwargs["owner"] == "ChonSong"
            assert call_kwargs["repo"] == "riptide"
            assert call_kwargs["pr_number"] == 42
            assert call_kwargs["pr_details"]["title"] == "feat: add bar"
            assert call_kwargs["pr_details"]["author"] == "test-user"
            assert call_kwargs["pr_details"]["head"]["sha"] == "abc123def456"


class TestGatherReviewData:
    """Tests for _gather_review_data function."""

    @patch("subprocess.run")
    def test_gather_review_data_success(self, mock_run):
        """Successful data gathering returns structured data."""
        # Mock different subprocess calls
        def side_effect(cmd, **kwargs):
            result = MagicMock()
            if "diff" in cmd:
                result.returncode = 0
                result.stdout = "+ line 1\n- line 2\n"
            elif "view" in cmd and "files" in cmd:
                result.returncode = 0
                result.stdout = json.dumps({"files": [
                    {"path": "test.py", "additions": 100, "deletions": 50}
                ]})
            elif "api" in cmd:
                result.returncode = 0
                result.stdout = json.dumps([
                    {"filename": "test.py", "patch": "+ line\n", "status": "modified"}
                ])
            else:
                result.returncode = 1
                result.stdout = ""
            result.stderr = ""
            return result

        mock_run.side_effect = side_effect

        result = _gather_review_data("ChonSong", "riptide", 42, "abc123")

        assert "files_changed" in result
        assert len(result["files_changed"]) == 1
        assert result["files_changed"][0]["filename"] == "test.py"

    @patch("subprocess.run")
    def test_gather_review_data_gh_failure(self, mock_run):
        """gh CLI failure returns data with empty files_changed."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Not Found")

        result = _gather_review_data("ChonSong", "riptide", 42, "abc123")

        assert result["files_changed"] == []


class TestBuildOrchestratorPrompt:
    """Tests for _build_orchestrator_prompt function."""

    def test_prompt_contains_pr_title(self):
        """Prompt should contain the PR title."""
        result = _build_orchestrator_prompt(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_title="feat: add bar",
            pr_author="test-user",
            total_loc=200,
            head_sha="abc123",
            data={"files_changed": [], "diff_raw": "", "god_nodes": [], "communities": [], "graph_context": {}},
            diagram_url=None,
            deterministic=None,
            pr_created_at="2026-01-01T00:00:00Z",
            triggered_at="2026-01-01T00:00:00Z",
        )

        assert "feat: add bar" in result

    def test_prompt_contains_pr_author(self):
        """Prompt should contain the PR author."""
        result = _build_orchestrator_prompt(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_title="test",
            pr_author="test-user",
            total_loc=200,
            head_sha="abc123",
            data={"files_changed": [], "diff_raw": "", "god_nodes": [], "communities": [], "graph_context": {}},
            diagram_url=None,
            deterministic=None,
            pr_created_at="2026-01-01T00:00:00Z",
            triggered_at="2026-01-01T00:00:00Z",
        )

        assert "test-user" in result


class TestLocFiltering:
    """Tests for LOC-based filtering."""

    def test_min_loc_changed_constant(self):
        """MIN_LOC_CHANGED should be 100."""
        assert MIN_LOC_CHANGED == 100

    def test_staleness_minutes_constant(self):
        """STALENESS_MINUTES should be 30."""
        assert STALENESS_MINUTES == 30


class TestStateOperations:
    """Tests for state save/load operations."""

    def test_load_state_empty(self):
        """Loading state from empty DB returns empty dict."""
        with patch("riptide.deepthink.StateStore") as mock_state:
            mock_state.return_value._get_conn.return_value.execute.return_value.fetchall.return_value = []
            result = _load_state()
            assert result == {}

    def test_save_and_load_state(self):
        """Saving and loading state preserves data."""
        with patch("riptide.deepthink.StateStore") as mock_state:
            mock_state.return_value._get_conn.return_value.execute.return_value.fetchall.return_value = [
                ("ChonSong/riptide#42", "abc123", "2026-01-01T00:00:00Z")
            ]
            _save_state({"ChonSong/riptide#42": {"head_sha": "abc123", "reviewed_at": "2026-01-01T00:00:00Z"}})
            result = _load_state()
            assert "ChonSong/riptide#42" in result


class TestWasReviewedToday:
    """Tests for _was_reviewed_today function."""

    def test_not_reviewed(self):
        """PR that was never reviewed returns False."""
        with patch("riptide.deepthink.StateStore") as mock_state:
            mock_state.return_value.get_pr_heuristics.return_value = {
                "reviewed_at": None,
            }
            result = _was_reviewed_today("ChonSong", "riptide", 42)
            assert result is False

    def test_reviewed_recently(self):
        """PR reviewed in last 24h returns True."""
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with patch("riptide.deepthink.StateStore") as mock_state:
            mock_state.return_value.get_pr_heuristics.return_value = {
                "reviewed_at": recent,
            }
            result = _was_reviewed_today("ChonSong", "riptide", 42)
            assert result is True

    def test_reviewed_long_ago(self):
        """PR reviewed more than 24h ago returns False."""
        from datetime import datetime, timezone, timedelta
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        with patch("riptide.deepthink.StateStore") as mock_state:
            mock_state.return_value.get_pr_heuristics.return_value = {
                "reviewed_at": old,
            }
            result = _was_reviewed_today("ChonSong", "riptide", 42)
            assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
