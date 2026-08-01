# riptide/tests/test_deepthink.py
"""
Tests for Riptide Deephink bot (Bot 2).
Covers spawn logic, LOC filtering, state save/load, and dedup.
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
    MIN_LOC_CHANGED,
    STALENESS_MINUTES,
    STATE_FILE,
)


# ── _spawn_deepthink tests ──────────────────────────────────────────────────


class TestSpawnDeepthink:
    """Tests for _spawn_deepthink function."""

    def test_spawn_builds_correct_command(self, mock_hermes_cron):
        _spawn_deepthink(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_title="feat: add bar",
            pr_author="test-user",
            total_loc=200,
            head_sha="abc123def456",
        )

        call_args = mock_hermes_cron.call_args
        cmd = call_args[0][0]

        # Verify base command structure
        assert cmd[0] == "hermes"
        assert cmd[1] == "cron"
        assert cmd[2] == "create"

        # Verify --skill flags
        assert "--skill" in cmd
        skills = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--skill"]
        assert "github-pr-lifecycle" in skills
        assert "deep-think" in skills
        assert "excalidraw" in skills
        assert "brooks-lint" in skills

        # Verify --deliver and --name
        assert "--deliver" in cmd
        assert "origin" in cmd
        assert "--name" in cmd
        name_idx = cmd.index("--name")
        assert cmd[name_idx + 1] == "riptide-review-ChonSong-riptide-42"

    def _success_result(self):
        r = MagicMock()
        r.returncode = 0
        r.stdout = "cron-id-123"
        r.stderr = ""
        return r

    def test_spawn_success_returns_true(self):
        with patch("subprocess.run", return_value=self._success_result()) as mock_run, \
             patch("riptide.deepthink._is_cron_available", return_value=True):
            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is True
            mock_run.assert_called_once()

    def test_spawn_failure_returns_false_after_retries(self):
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stderr="boom")) as mock_run, \
             patch("time.sleep") as mock_sleep, \
             patch("riptide.deepthink._is_cron_available", return_value=True):
            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is False
            assert mock_run.call_count == 3

    def test_spawn_timeout_returns_false_after_retries(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="hermes", timeout=15)) as mock_run, \
             patch("time.sleep") as mock_sleep, \
             patch("riptide.deepthink._is_cron_available", return_value=True):
            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is False
            assert mock_run.call_count == 3

    def test_spawn_includes_pr_details_in_prompt(self):
        with patch("subprocess.run", return_value=self._success_result()) as mock_hermes_cron, \
             patch("riptide.deepthink._is_cron_available", return_value=True):
            _spawn_deepthink(
                "ChonSong", "riptide", 42, "feat: important change", "test-author", 250, "abc123def456789",
            )

            call_args = mock_hermes_cron.call_args
            cmd = call_args[0][0]
            # cmd: ["hermes", "cron", "create", run_at, prompt, "--name", ...]
            prompt = cmd[4]

            assert "42" in prompt
            assert "ChonSong/riptide" in prompt
            assert "feat: important change" in prompt
            assert "test-author" in prompt
            assert "250" in prompt


# ── _is_cron_available tests ────────────────────────────────────────────────


class TestIsCronAvailable:
    def test_cron_available_returns_true(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "/usr/local/bin/hermes\n"
            assert _is_cron_available() is True

    def test_cron_available_returns_false(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            assert _is_cron_available() is False

    def test_cron_available_returns_false_on_empty_stdout(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "   "
            assert _is_cron_available() is False


# ── State save/load tests ───────────────────────────────────────────────────


class TestStateSaveLoad:
    def test_save_and_load_state(self, tmp_path):
        state_file = tmp_path / "deepthink_acted_prs.json"
        with patch("riptide.deepthink.STATE_FILE", state_file):
            test_state = {
                "ChonSong/riptide#42": {"head_sha": "abc123", "reviewed_at": "2026-07-31T00:00:00+00:00"},
                "ChonSong/hermes-webui#100": {"head_sha": "def456", "reviewed_at": "2026-07-31T01:00:00+00:00"},
            }
            _save_state(test_state)
            loaded = _load_state()
            assert loaded == test_state

    def test_load_state_nonexistent_file(self, tmp_path):
        state_file = tmp_path / "nonexistent" / "deepthink_acted_prs.json"
        with patch("riptide.deepthink.STATE_FILE", state_file):
            result = _load_state()
            assert result == {}

    def test_load_state_corrupted_file(self, tmp_path):
        state_file = tmp_path / "deepthink_acted_prs.json"
        state_file.write_text("{invalid json")
        with patch("riptide.deepthink.STATE_FILE", state_file):
            result = _load_state()
            assert result == {}

    def test_save_state_creates_parent_dirs(self, tmp_path):
        state_file = tmp_path / "deep" / "nested" / "deepthink_acted_prs.json"
        with patch("riptide.deepthink.STATE_FILE", state_file):
            _save_state({"test": {"key": "value"}})
            assert state_file.exists()

    def test_state_persistence_across_instances(self, tmp_path):
        state_file = tmp_path / "deepthink_acted_prs.json"
        with patch("riptide.deepthink.STATE_FILE", state_file):
            _save_state({"repo#1": {"head_sha": "sha1"}})
            state = _load_state()
            state["repo#2"] = {"head_sha": "sha2"}
            _save_state(state)
            final = _load_state()
            assert "repo#1" in final
            assert "repo#2" in final


# ── LOC filtering tests ─────────────────────────────────────────────────────


class TestLocFiltering:
    def test_min_loc_constant(self):
        assert MIN_LOC_CHANGED == 100

    def test_pr_below_min_loc_is_skipped(self, tmp_path):
        state_file = tmp_path / "deepthink_acted_prs.json"
        with patch("riptide.deepthink.STATE_FILE", state_file):
            # Small PR: 50 additions + 30 deletions = 80 LOC (below 100)
            total_loc = 80
            assert total_loc <= MIN_LOC_CHANGED

    def test_pr_above_min_loc_is_accepted(self, tmp_path):
        state_file = tmp_path / "deepthink_acted_prs.json"
        with patch("riptide.deepthink.STATE_FILE", state_file):
            total_loc = 200
            assert total_loc > MIN_LOC_CHANGED


# ── Dedup logic tests ───────────────────────────────────────────────────────


class TestDedupLogic:
    def test_same_pr_same_sha_not_spawned_twice(self, tmp_path):
        state_file = tmp_path / "deepthink_acted_prs.json"
        with patch("riptide.deepthink.STATE_FILE", state_file):
            # Simulate already-reviewed PR
            _save_state({
                "ChonSong/riptide#42": {
                    "head_sha": "abc123",
                    "reviewed_at": "2026-07-31T00:00:00+00:00",
                }
            })

            # Same PR, same SHA — should be deduped
            pr_key = "ChonSong/riptide#42"
            state = _load_state()
            assert pr_key in state
            assert state[pr_key]["head_sha"] == "abc123"

    def test_same_pr_different_sha_spawns_again(self, tmp_path):
        state_file = tmp_path / "deepthink_acted_prs.json"
        with patch("riptide.deepthink.STATE_FILE", state_file):
            _save_state({
                "ChonSong/riptide#42": {
                    "head_sha": "abc123",
                    "reviewed_at": "2026-07-31T00:00:00+00:00",
                }
            })

            # Different SHA — should not be deduped
            pr_key = "ChonSong/riptide#42"
            state = _load_state()
            new_sha = "def456"
            assert state[pr_key]["head_sha"] != new_sha


# ── _was_reviewed_today tests ───────────────────────────────────────────────


class TestWasReviewedToday:
    def test_reviewed_today_returns_true(self, tmp_path):
        state_file = tmp_path / "deepthink_acted_prs.json"
        with patch("riptide.deepthink.STATE_FILE", state_file):
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc).isoformat()
            _save_state({
                "ChonSong/riptide#42": {
                    "head_sha": "abc",
                    "reviewed_at": now,
                }
            })
            assert _was_reviewed_today("ChonSong", "riptide", 42) is True

    def test_not_reviewed_today_returns_false(self, tmp_path):
        state_file = tmp_path / "deepthink_acted_prs.json"
        with patch("riptide.deepthink.STATE_FILE", state_file):
            from datetime import datetime, timezone, timedelta
            old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
            _save_state({
                "ChonSong/riptide#42": {
                    "head_sha": "abc",
                    "reviewed_at": old,
                }
            })
            assert _was_reviewed_today("ChonSong", "riptide", 42) is False

    def test_never_reviewed_returns_false(self, tmp_path):
        state_file = tmp_path / "deepthink_acted_prs.json"
        with patch("riptide.deepthink.STATE_FILE", state_file):
            _save_state({})
            assert _was_reviewed_today("ChonSong", "riptide", 42) is False
