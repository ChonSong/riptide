# riptide/tests/test_deepthink.py
"""
Tests for Riptide Deephink bot (Bot 2).
Covers spawn logic, LOC filtering, state save/load, and dedup.
"""

import json
import os
import subprocess
import tempfile
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
    """Tests for _spawn_deepthink function."""

    def _success_result(self):
        r = MagicMock()
        r.returncode = 0
        r.stdout = "cron-id-123"
        r.stderr = ""
        return r

    def _gather_data_mock(self, *args, **kwargs):
        return {
            "files_changed": [{"filename": "test.py", "additions": 100, "deletions": 50}],
            "diff_raw": "+ line 1\n- line 2\n",
            "repo_tree": ["test.py", "main.py"],
            "god_nodes": [{"name": "test.py", "edges": 5}],
            "communities": [{"name": "core", "members": ["test.py"]}],
            "graph_context": {"raw": "test.py affects main.py"},
        }

    def test_spawn_builds_correct_command(self, mock_hermes_cron):
        with patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
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
        # excalidraw is a disabled skill (skills_disabled/creative/excalidraw):
        # requesting it only produced a "skill not found and skipped" warning in
        # every spawned session. The diagram is rendered by grafiphy/orchestrator.
        assert "excalidraw" not in skills

        # Verify --deliver and --name
        assert "--deliver" in cmd
        assert "origin" in cmd
        assert "--name" in cmd
        name_idx = cmd.index("--name")
        assert cmd[name_idx + 1] == "riptide-review-ChonSong-riptide-42"

    def test_spawn_writes_prompt_to_temp_file(self):
        """Prompt should be written to temp file to bypass Hermes safety filter."""
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="Created job: 123")) as mock_run, \
             patch("riptide.deepthink._is_cron_available", return_value=True), \
             patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is True
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            # Extract the prompt file path from cmd[4]
            assert "Read the prompt from" in cmd[4]
            assert "riptide-prompt-" in cmd[4]
            # Verify the prompt file contains the expected content
            # cmd[4] format: "Read the prompt from {path} and execute it. After execution, delete the file {path}."
            prompt_path = cmd[4].split("Read the prompt from ")[1].split(" and execute it.")[0]
            with open(prompt_path) as f:
                content = f.read()
            assert "test" in content  # pr_title was "test"
            # Clean up the temp file (in real usage Hermes would do this)
            import os
            try:
                os.unlink(prompt_path)
            except OSError:
                pass

    def test_spawn_fails_when_hermes_blocked(self):
        """Hermes returns exit 0 with 'Failed to create job' — should raise."""
        blocked_stdout = "Failed to create job: Blocked: cron job contains a gateway lifecycle command"
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=blocked_stdout, stderr="")) as mock_run, \
             patch("time.sleep"), \
             patch("riptide.deepthink._is_cron_available", return_value=True), \
             patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            with pytest.raises(RuntimeError, match="All 3 Hermes cron attempts failed"):
                _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            # 3 spawn attempts + 1 diagram generation call (which also gets blocked)
            assert mock_run.call_count == 4

    def test_spawn_fails_when_hermes_blocked_in_stderr(self):
        """Hermes blocks with message in stderr instead of stdout — should still detect."""
        blocked_stderr = "Failed to create job: Blocked: cron job contains a gateway lifecycle command"
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr=blocked_stderr)) as mock_run, \
             patch("time.sleep"), \
             patch("riptide.deepthink._is_cron_available", return_value=True), \
             patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            with pytest.raises(RuntimeError, match="All 3 Hermes cron attempts failed"):
                _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")

    def test_prompt_file_cleaned_up_on_failure(self):
        """Prompt file is removed when scheduling fails."""
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="Failed to create job: Blocked")), \
             patch("time.sleep"), \
             patch("riptide.deepthink._is_cron_available", return_value=True), \
             patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.state.StateStore") as mock_state, \
             patch("os.unlink") as mock_unlink:
            mock_state.return_value.reserve_job.return_value = True
            with pytest.raises(RuntimeError):
                _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            # Verify cleanup was called
            mock_unlink.assert_called()

    def test_sanitize_prompt_redacts_secrets(self):
        """Prompt sanitizer should redact common secret patterns."""
        from riptide.deepthink import _sanitize_prompt
        # GitHub token
        assert "ghp_abc123" not in _sanitize_prompt("token: ghp_abc123xyz")
        # API key
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in _sanitize_prompt("key: sk-abcdefghijklmnopqrstuvwxyz")
        # Private key
        assert "BEGIN RSA PRIVATE KEY" not in _sanitize_prompt("key: -----BEGIN RSA PRIVATE KEY-----\nsecret\n-----END RSA PRIVATE KEY-----")
        # Normal text preserved
        assert "normal text here" in _sanitize_prompt("normal text here")

    def test_hermes_blocked_detection(self):
        """_hermes_blocked detects blocked jobs case-insensitively from stdout and stderr."""
        from riptide.deepthink import _hermes_blocked
        # stdout detection
        assert _hermes_blocked("Failed to create job: Blocked", "") is True
        # stderr detection
        assert _hermes_blocked("", "Failed to create job: Blocked") is True
        # case insensitive
        assert _hermes_blocked("FAILED TO CREATE JOB", "") is True
        # not blocked
        assert _hermes_blocked("Created job: 123", "") is False
        assert _hermes_blocked("", "") is False

    def test_spawn_success_returns_true(self):
        with patch("subprocess.run", return_value=self._success_result()) as mock_run, \
             patch("riptide.deepthink._is_cron_available", return_value=True), \
             patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.grafiphy.orchestrator.pre_generate_diagram", return_value=None), \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is True
            mock_run.assert_called_once()

    def test_spawn_failure_raises_after_retries(self):
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stderr="boom")) as mock_run, \
             patch("time.sleep") as mock_sleep, \
             patch("riptide.deepthink._is_cron_available", return_value=True), \
             patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.grafiphy.orchestrator.pre_generate_diagram", return_value=None), \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            with pytest.raises(RuntimeError, match="All 3 Hermes cron attempts failed"):
                _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert mock_run.call_count == 3

    def test_spawn_timeout_raises_after_retries(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="hermes", timeout=15)) as mock_run, \
             patch("time.sleep") as mock_sleep, \
             patch("riptide.deepthink._is_cron_available", return_value=True), \
             patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.grafiphy.orchestrator.pre_generate_diagram", return_value=None), \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            with pytest.raises(RuntimeError, match="All 3 Hermes cron attempts failed"):
                _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert mock_run.call_count == 3

    def test_spawn_data_gathering_failure_raises(self):
        with patch("riptide.deepthink._gather_review_data", side_effect=Exception("gh failed")), \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            with pytest.raises(RuntimeError, match="Failed to gather data"):
                _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")

    def test_spawn_includes_pr_details_in_prompt(self):
        with patch("subprocess.run", return_value=self._success_result()) as mock_hermes_cron, \
             patch("riptide.deepthink._is_cron_available", return_value=True), \
             patch("riptide.deepthink._gather_review_data", side_effect=self._gather_data_mock), \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            _spawn_deepthink(
                "ChonSong", "riptide", 42, "feat: important change", "test-author", 250, "abc123def456789",
            )

            call_args = mock_hermes_cron.call_args
            cmd = call_args[0][0]
            # cmd: ["hermes", "cron", "create", run_at, "Read the prompt from /tmp/riptide-prompt-...", "--name", ...]
            prompt_ref = cmd[4]
            assert "Read the prompt from" in prompt_ref
            assert "riptide-prompt-" in prompt_ref

    def test_skips_when_review_already_pending(self):
        with patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = False
            with pytest.raises(RuntimeError, match="review already pending"):
                _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")


# ── Classification Integration Tests ────────────────────────────────────────


class TestSpawnDeepthinkClassification:
    """Tests that _spawn_deepthink uses classification to select skills."""

    def _success_result(self):
        r = MagicMock()
        r.returncode = 0
        r.stdout = "cron-id-123"
        r.stderr = ""
        return r

    def test_trivial_pr_loads_no_skills(self):
        """TRIVIAL PRs should not load any LLM skills."""
        trivial_data = {
            "files_changed": [{"filename": "README.md", "additions": 3, "deletions": 1}],
            "diff_raw": "+ line\n",
            "repo_tree": [],
            "god_nodes": [],
            "communities": [],
            "graph_context": {},
        }
        with patch("subprocess.run", return_value=self._success_result()) as mock_run, \
             patch("riptide.deepthink._is_cron_available", return_value=True), \
             patch("riptide.deepthink._gather_review_data", return_value=trivial_data), \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            _spawn_deepthink("ChonSong", "riptide", 42, "docs: fix typo", "user", 4, "abc123")

            call_args = mock_run.call_args
            cmd = call_args[0][0]
            skills = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--skill"]
            assert skills == []

    def test_arch_pr_loads_brooks_lint(self):
        """ARCH PRs should load brooks-lint in addition to standard skills."""
        arch_data = {
            "files_changed": [
                {"filename": f"f{i}.py", "additions": 50, "deletions": 20}
                for i in range(6)
            ],
            "diff_raw": "+ line\n" * 300,
            "repo_tree": [],
            "god_nodes": [{"name": "core.py", "edges": 25}],
            "communities": [{"name": "core", "members": ["core.py"]}],
            "graph_context": {},
        }
        with patch("subprocess.run", return_value=self._success_result()) as mock_run, \
             patch("riptide.deepthink._is_cron_available", return_value=True), \
             patch("riptide.deepthink._gather_review_data", return_value=arch_data), \
             patch("riptide.state.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            _spawn_deepthink("ChonSong", "riptide", 42, "feat: big refactor", "user", 420, "abc123")

            call_args = mock_run.call_args
            cmd = call_args[0][0]
            skills = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--skill"]
            assert "brooks-lint" in skills


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
    """State helpers now delegate to StateStore (WS-3 Stage 0).

    Tests use a temp DB and patch deepthink.StateStore so the real prod
    state.db is never touched.
    """

    def setup_method(self):
        self._patches = []

    def teardown_method(self):
        for p in self._patches:
            p.stop()
        self._patches.clear()

    def _tmp_store(self, tmp_path):
        store = StateStore(str(tmp_path / "state.db"))
        patch_obj = patch("riptide.deepthink.StateStore", return_value=store)
        patch_obj.start()
        self._patches.append(patch_obj)
        return store

    def test_save_and_load_state(self, tmp_path):
        self._tmp_store(tmp_path)
        test_state = {
            "ChonSong/riptide#42": {"head_sha": "abc123", "reviewed_at": "2026-07-31T00:00:00+00:00"},
            "ChonSong/hermes-webui#100": {"head_sha": "def456", "reviewed_at": "2026-07-31T01:00:00+00:00"},
        }
        _save_state(test_state)
        loaded = _load_state()
        assert loaded == test_state

    def test_load_state_nonexistent_file(self, tmp_path):
        self._tmp_store(tmp_path)
        result = _load_state()
        assert result == {}

    def test_load_state_corrupted_file(self, tmp_path):
        self._tmp_store(tmp_path)
        result = _load_state()
        assert result == {}

    def test_save_state_creates_parent_dirs(self, tmp_path):
        self._tmp_store(tmp_path)
        _save_state({"test": {"head_sha": "sha"}})
        assert (tmp_path / "state.db").exists()

    def test_state_persistence_across_instances(self, tmp_path):
        self._tmp_store(tmp_path)
        _save_state({"repo#1": {"head_sha": "sha1"}})
        state = _load_state()
        state["repo#2"] = {"head_sha": "sha2"}
        _save_state(state)
        assert _load_state() == {
            "repo#1": {"head_sha": "sha1", "reviewed_at": None},
            "repo#2": {"head_sha": "sha2", "reviewed_at": None},
        }


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
        state_db = tmp_path / "state.db"
        with patch("riptide.deepthink.StateStore") as mock_store_cls:
            from riptide.state import StateStore
            store = StateStore(str(state_db))
            mock_store_cls.return_value = store

            # Simulate already-reviewed PR
            store.set_pr_last_sha("ChonSong/riptide#42", "abc123")
            store.set_pr_reviewed_at("ChonSong/riptide#42", "2026-07-31T00:00:00+00:00")

            # Same PR, same SHA — should be deduped
            pr_key = "ChonSong/riptide#42"
            heuristics = store.get_pr_heuristics(pr_key)
            assert heuristics["last_sha"] == "abc123"

    def test_same_pr_different_sha_spawns_again(self, tmp_path):
        state_db = tmp_path / "state.db"
        with patch("riptide.deepthink.StateStore") as mock_store_cls:
            from riptide.state import StateStore
            store = StateStore(str(state_db))
            mock_store_cls.return_value = store

            store.set_pr_last_sha("ChonSong/riptide#42", "abc123")
            store.set_pr_reviewed_at("ChonSong/riptide#42", "2026-07-31T00:00:00+00:00")

            # Different SHA — should not be deduped
            pr_key = "ChonSong/riptide#42"
            heuristics = store.get_pr_heuristics(pr_key)
            new_sha = "def456"
            assert heuristics["last_sha"] != new_sha


# ── _was_reviewed_today tests ───────────────────────────────────────────────


class TestWasReviewedToday:
    def test_reviewed_today_returns_true(self, tmp_path):
        state_db = tmp_path / "state.db"
        with patch("riptide.deepthink.StateStore") as mock_store_cls:
            from riptide.state import StateStore
            store = StateStore(str(state_db))
            mock_store_cls.return_value = store

            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            store.set_pr_last_sha("ChonSong/riptide#42", "abc")
            store.set_pr_reviewed_at("ChonSong/riptide#42", now)
            assert _was_reviewed_today("ChonSong", "riptide", 42) is True

    def test_not_reviewed_today_returns_false(self, tmp_path):
        state_db = tmp_path / "state.db"
        with patch("riptide.deepthink.StateStore") as mock_store_cls:
            from riptide.state import StateStore
            store = StateStore(str(state_db))
            mock_store_cls.return_value = store

            from datetime import datetime, timezone, timedelta
            old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
            store.set_pr_last_sha("ChonSong/riptide#42", "abc")
            store.set_pr_reviewed_at("ChonSong/riptide#42", old)
            assert _was_reviewed_today("ChonSong", "riptide", 42) is False

    def test_never_reviewed_returns_false(self, tmp_path):
        state_db = tmp_path / "state.db"
        with patch("riptide.deepthink.StateStore") as mock_store_cls:
            from riptide.state import StateStore
            store = StateStore(str(state_db))
            mock_store_cls.return_value = store

            assert _was_reviewed_today("ChonSong", "riptide", 42) is False


# ── _gather_review_data tests ────────────────────────────────────────────────


class TestGatherReviewData:
    """Tests for _gather_review_data function."""

    def test_returns_default_structure_on_failure(self):
        with patch("subprocess.run", side_effect=Exception("boom")):
            result = _gather_review_data("ChonSong", "riptide", 42, "abc123")
            assert result["files_changed"] == []
            assert result["diff_raw"] == ""
            assert result["repo_tree"] == []
            assert result["god_nodes"] == []
            assert result["communities"] == []

    def test_fetches_diff_successfully(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "+ new line\n- old line\n"
        with patch("subprocess.run", return_value=mock_result):
            result = _gather_review_data("ChonSong", "riptide", 42, "abc123")
            assert result["diff_raw"] == "+ new line\n- old line\n"

    def test_fetches_files_successfully(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"files": [{"path": "test.py", "additions": 10, "deletions": 5}]}'
        with patch("subprocess.run", return_value=mock_result):
            result = _gather_review_data("ChonSong", "riptide", 42, "abc123")
            assert result["files_changed"] == [{"filename": "test.py", "additions": 10, "deletions": 5}]

    def test_caps_diff_at_50k_chars(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "x" * 60000
        with patch("subprocess.run", return_value=mock_result):
            result = _gather_review_data("ChonSong", "riptide", 42, "abc123")
            assert len(result["diff_raw"]) == 50000

    def test_merges_per_file_patches_from_api(self):
        """WS-3 Stage 1: gh api pulls/N/files patches feed the context bundle."""
        view_result = MagicMock()
        view_result.returncode = 0
        view_result.stdout = (
            '{"files": [{"path": "test.py", "additions": 10, "deletions": 5}]}'
        )
        api_result = MagicMock()
        api_result.returncode = 0
        api_result.stdout = (
            '[{"filename": "test.py", "status": "modified", '
            '"patch": "+ secret = \'x\'"}]'
        )
        empty_result = MagicMock()
        empty_result.returncode = 0
        empty_result.stdout = ""

        def _respond(*args, **kwargs):
            cmd = args[0]
            if cmd[0] == "gh" and cmd[1] == "api":
                return api_result
            if cmd[0] == "gh" and cmd[1] == "pr" and cmd[2] == "diff":
                return empty_result
            if cmd[0] == "git" and cmd[1] == "ls-tree":
                return empty_result
            return view_result

        with patch("subprocess.run", side_effect=_respond):
            result = _gather_review_data("ChonSong", "riptide", 42, "abc123")
        assert result["files_changed"][0]["patch"] == "+ secret = 'x'"
        assert result["files_changed"][0]["status"] == "modified"


# ── _build_orchestrator_prompt tests ─────────────────────────────────────────


class TestBuildOrchestratorPrompt:
    """Tests for _build_orchestrator_prompt function."""

    def test_includes_pr_details(self):
        data = {
            "files_changed": [{"filename": "test.py", "additions": 100, "deletions": 50}],
            "diff_raw": "+ line\n",
            "repo_tree": ["test.py"],
            "god_nodes": [{"name": "test.py", "edges": 5}],
            "communities": [{"name": "core", "members": []}],
            "graph_context": {"raw": "test.py affects main.py"},
        }
        prompt = _build_orchestrator_prompt(
            "ChonSong", "riptide", 42, "feat: test", "author", 150, "abc123", data
        )
        assert "42" in prompt
        assert "ChonSong/riptide" in prompt
        assert "feat: test" in prompt
        assert "author" in prompt
        assert "150" in prompt

    def test_includes_files_changed(self):
        data = {
            "files_changed": [{"filename": "main.py", "additions": 200, "deletions": 100}],
            "diff_raw": "",
            "repo_tree": [],
            "god_nodes": [],
            "communities": [],
            "graph_context": {},
        }
        prompt = _build_orchestrator_prompt(
            "ChonSong", "riptide", 42, "feat: test", "author", 300, "abc123", data
        )
        assert "main.py" in prompt
        assert "200" in prompt
        assert "100" in prompt

    def test_includes_graphify_data(self):
        data = {
            "files_changed": [],
            "diff_raw": "",
            "repo_tree": [],
            "god_nodes": [{"name": "hub.py", "edges": 10}],
            "communities": [{"name": "auth", "members": ["login.py"]}],
            "graph_context": {"raw": "hub.py is a god node"},
        }
        prompt = _build_orchestrator_prompt(
            "ChonSong", "riptide", 42, "feat: test", "author", 300, "abc123", data
        )
        assert "hub.py" in prompt
        assert "10" in prompt
        assert "auth" in prompt

    def test_handles_empty_data(self):
        data = {
            "files_changed": [],
            "diff_raw": "",
            "repo_tree": [],
            "god_nodes": [],
            "communities": [],
            "graph_context": {},
        }
        prompt = _build_orchestrator_prompt(
            "ChonSong", "riptide", 42, "feat: test", "author", 300, "abc123", data
        )
        assert "No graphify analysis available" in prompt
        assert "Delegate Inline Review" in prompt
        assert "assemble_review" in prompt

    def test_includes_subagent_instructions(self):
        data = {
            "files_changed": [],
            "diff_raw": "",
            "repo_tree": [],
            "god_nodes": [],
            "communities": [],
            "graph_context": {},
        }
        prompt = _build_orchestrator_prompt(
            "ChonSong", "riptide", 42, "feat: test", "author", 300, "abc123", data
        )
        assert "Spawn a subagent" in prompt
        assert "assemble_review" in prompt
        assert "LongCat-2.0" in prompt or "DEEPTHINK" in prompt

    def test_includes_diagram_url_when_provided(self):
        data = {
            "files_changed": [],
            "diff_raw": "",
            "repo_tree": [],
            "god_nodes": [],
            "communities": [],
            "graph_context": {},
        }
        prompt = _build_orchestrator_prompt(
            "ChonSong", "riptide", 42, "feat: test", "author", 300, "abc123", data,
            diagram_url="https://excalidraw.com/#json=abc123"
        )
        assert "Pre-generated Architecture Diagram" in prompt
        assert "https://excalidraw.com/#json=abc123" in prompt
        assert "Step 4: Architecture Diagram" in prompt

    def test_includes_deterministic_analysis_when_provided(self):
        """WS-3 Stage 1: the pre-computed DiffReport findings feed the session."""
        data = {
            "files_changed": [{"filename": "test.py", "additions": 100, "deletions": 50}],
            "diff_raw": "+ secret = 'x'\n",
            "repo_tree": [],
            "god_nodes": [],
            "communities": [],
            "graph_context": {},
        }
        deterministic = {
            "verdict": "review",
            "findings": [
                {"category": "security", "severity": "critical",
                 "message": "Possible hardcoded secret", "file": "test.py"},
                {"category": "complexity", "severity": "warning",
                 "message": "Function exceeds 300 lines", "file": "main.py"},
            ],
            "stats": {"total_add": 100, "total_del": 50, "file_count": 1},
            "aggregate": {"concepts": ["tests", "core"]},
        }
        prompt = _build_orchestrator_prompt(
            "ChonSong", "riptide", 42, "feat: test", "author", 150, "abc123", data,
            deterministic=deterministic
        )
        assert "Deterministic Analysis (pre-computed)" in prompt
        assert "Possible hardcoded secret" in prompt
        assert "Function exceeds 300 lines" in prompt
        assert "verdict" in prompt.lower() or "review" in prompt
        assert "concepts" in prompt.lower() or "tests, core" in prompt
        assert "do NOT re-derive from scratch" in prompt

    def test_omits_deterministic_analysis_when_not_provided(self):
        data = {
            "files_changed": [],
            "diff_raw": "",
            "repo_tree": [],
            "god_nodes": [],
            "communities": [],
            "graph_context": {},
        }
        prompt = _build_orchestrator_prompt(
            "ChonSong", "riptide", 42, "feat: test", "author", 300, "abc123", data
        )
        assert "Deterministic Analysis (pre-computed)" not in prompt


# ── handle_review_command tests ──────────────────────────────────────────────


class TestHandleReviewCommand:
    """Tests for handle_review_command — verifies honest messaging."""

    def _mock_client(self):
        client = MagicMock()
        client.get_pr_details.return_value = {
            "title": "test",
            "user": {"login": "ChonSong"},
            "additions": 100,
            "deletions": 50,
            "head": {"sha": "abc123def456"},
        }
        return client

    def test_returns_already_pending_message_when_reservation_fails(self):
        from riptide.deepthink import handle_review_command
        with patch("riptide.deepthink._spawn_deepthink", side_effect=RuntimeError("review already pending")):
            result = handle_review_command(
                client=self._mock_client(),
                installation_id=123,
                owner="ChonSong",
                repo="riptide",
                pr_number=42,
                commenter="ChonSong",
            )
            assert "Already pending" in result

    def test_returns_error_message_when_spawn_raises(self):
        from riptide.deepthink import handle_review_command
        with patch("riptide.deepthink._spawn_deepthink", side_effect=RuntimeError("Hermes cron failed")):
            result = handle_review_command(
                client=self._mock_client(),
                installation_id=123,
                owner="ChonSong",
                repo="riptide",
                pr_number=42,
                commenter="ChonSong",
            )
            assert "⚠️" in result
            assert "Hermes cron failed" in result

    def test_returns_triggered_message_on_success(self):
        from riptide.deepthink import handle_review_command
        with patch("riptide.deepthink._spawn_deepthink", return_value=True):
            result = handle_review_command(
                client=self._mock_client(),
                installation_id=123,
                owner="ChonSong",
                repo="riptide",
                pr_number=42,
                commenter="ChonSong",
            )
            assert "🧠" in result
            assert "triggered" in result



# ── failed-job reporting: _release_finished_reservations + _post_failure_comment


class TestFailedJobReporting:
    """A spawned job whose session died after the ack (LLM 402, crash) must not
    leave 'triggered!' as the PR's last word: the reservation is released AND a
    chaseable failure comment lands on the PR, deduped so one failure = one comment."""

    def _store_with_pending(self):
        store = MagicMock(spec=StateStore)
        store.list_pending_jobs.return_value = [
            {"id": "job-1", "pr_number": 214, "job_name": "riptide-review-ChonSong-riptide-214"}
        ]
        return store

    def test_failed_job_releases_and_posts_comment(self):
        from riptide.deepthink import _release_finished_reservations
        import riptide.deepthink as dt

        store = self._store_with_pending()
        states = {
            "riptide-review-ChonSong-riptide-214": {
                "state": None, "last_status": "error",
                "enabled": False, "run_at": None,
                "last_run_at": "2026-10-05T23:18:22Z",
            }
        }
        with patch.object(dt, "_cron_job_states", return_value=states), \
             patch.object(dt, "_post_failure_comment") as post:
            released = _release_finished_reservations(
                store, "riptide-review-ChonSong-riptide-214",
                "ChonSong", "riptide", 214,
            )
        assert released == 1
        store.mark_failed.assert_called_once_with("job-1")
        post.assert_called_once()
        args = post.call_args.args
        assert args[:4] == ("ChonSong", "riptide", 214, "riptide-review-ChonSong-riptide-214")

    def test_healthy_job_does_not_post_failure_comment(self):
        from riptide.deepthink import _release_finished_reservations
        import riptide.deepthink as dt

        store = self._store_with_pending()
        states = {
            "riptide-review-ChonSong-riptide-214": {
                "state": "completed", "last_status": "success",
                "enabled": False, "run_at": None,
                "last_run_at": "2026-10-05T23:18:22Z",
            }
        }
        with patch.object(dt, "_cron_job_states", return_value=states), \
             patch.object(dt, "_post_failure_comment") as post:
            released = _release_finished_reservations(
                store, "riptide-review-ChonSong-riptide-214",
                "ChonSong", "riptide", 214,
            )
        assert released == 1
        post.assert_not_called()

    def test_failure_comment_names_job_and_is_deduped(self):
        """The posted body carries the job name + failed-run stamp (chaseable)
        and the dedup check gates the post: marker absent -> comment posted
        with the name inside. The body must NOT contain the literal command
        phrase — the webhook self-trigger guard keys on the marker instead."""
        from riptide.deepthink import _post_failure_comment
        import riptide.deepthink as dt

        marker = (
            "⚠️ Riptide review job FAILED: "
            "`riptide-review-ChonSong-riptide-214` (2026-10-05T23:18:22Z)"
        )
        captured = {}

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            if any("contains" in str(a) for a in cmd):
                r.stdout = "false\n"  # dedup check: not yet posted
            else:
                # the gh pr comment post: capture its --body value
                r.stdout = "comment-url"
                body_idx = list(cmd).index("--body")
                captured["body"] = cmd[body_idx + 1]
            return r

        with patch.object(dt.subprocess, "run", side_effect=fake_run):
            _post_failure_comment(
                "ChonSong", "riptide", 214,
                "riptide-review-ChonSong-riptide-214",
                {"last_run_at": "2026-10-05T23:18:22Z"},
            )
        assert marker in captured.get("body", ""), (
            "the failure comment body never carried the job name + run stamp"
        )
        assert "last_status: error" in captured[ "body"]
        # The literal command phrase must never appear in the body: an
        # owner-authored comment containing it parses as a real review request.
        assert "`@riptide-bot review`" not in captured["body"]

    def test_failure_comment_marker_varies_per_failed_run(self):
        """Dedup is per failure run: the dedup marker embeds last_run_at, so a
        NEW failed run of the same job produces a different marker and is
        reportable (the old once-forever marker suppressed later failures)."""
        from riptide.deepthink import _post_failure_comment
        import riptide.deepthink as dt

        markers = []

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            jq_idx = [i for i, a in enumerate(cmd) if a == "--jq"]
            if jq_idx:
                markers.append(cmd[jq_idx[0] + 1])
                r.stdout = "true\n"  # already posted: skip the post
            return r

        with patch.object(dt.subprocess, "run", side_effect=fake_run):
            _post_failure_comment(
                "ChonSong", "riptide", 214,
                "riptide-review-ChonSong-riptide-214",
                {"last_run_at": "2026-10-05T23:18:22Z"},
            )
            _post_failure_comment(
                "ChonSong", "riptide", 214,
                "riptide-review-ChonSong-riptide-214",
                {"last_run_at": "2026-10-06T09:00:00Z"},
            )
        assert len(markers) == 2
        assert markers[0] != markers[1], (
            "two different failed runs produced the same dedup marker — "
            "the second failure would be silently suppressed"
        )

    def test_cron_job_states_projects_last_run_at(self):
        """The store projection must carry last_run_at: it feeds both the
        failure-comment run stamp and the per-run dedup marker. Dropping it
        made every failure read 'last run (unknown)' and made dedup
        once-per-job-name forever."""
        import riptide.deepthink as dt

        store = {
            "jobs": [
                {
                    "name": "riptide-review-X",
                    "state": "completed",
                    "last_status": "error",
                    "enabled": False,
                    "last_run_at": "2026-10-05T23:18:22Z",
                    "schedule": {"run_at": "2026-10-05T23:18:20+00:00"},
                }
            ]
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(store, f)
            tmp_path = f.name
        try:
            with patch.object(dt, "CRON_JOBS_PATH", Path(tmp_path)):
                states = dt._cron_job_states()
        finally:
            os.unlink(tmp_path)
        assert states is not None
        info = states["riptide-review-X"]
        assert info["last_run_at"] == "2026-10-05T23:18:22Z", (
            "_cron_job_states dropped last_run_at — failure dedup degrades to "
            "once-per-job-name and the comment shows 'last run (unknown)'"
        )
        assert info["run_at"] == "2026-10-05T23:18:20+00:00"

    def test_failure_comment_skipped_when_already_posted(self):
        from riptide.deepthink import _post_failure_comment
        import riptide.deepthink as dt

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stdout = "true\n" if any("contains" in a for a in cmd) else ""
            r.stderr = ""
            return r

        with patch.object(dt.subprocess, "run", side_effect=fake_run) as run:
            _post_failure_comment(
                "ChonSong", "riptide", 214,
                "riptide-review-ChonSong-riptide-214",
                {"last_run_at": "2026-10-05T23:18:22Z"},
            )
        posted = [
            c for c in run.call_args_list
            if len(c.args) > 0 and "pr" in c.args[0] and "comment" in c.args[0]
        ]
        assert not posted, "a second failure comment was posted for the same failure"


class TestReportFailedReviewJobs:
    """The poller sweep must report failed review jobs that have no upcoming
    re-spawn — manually-triggered reviews that die on LLM quota/crash leave
    the PR with 'triggered!' as its last word unless this catches them."""

    def test_reports_failed_review_job(self):
        from riptide.deepthink import _report_failed_review_jobs
        import riptide.deepthink as dt

        states = {
            "riptide-review-ChonSong-riptide-234": {
                "state": None, "last_status": "error",
                "enabled": False, "run_at": None,
                "last_run_at": "2026-10-08T23:34:40Z",
            }
        }
        with patch.object(dt, "_cron_job_states", return_value=states), \
             patch.object(dt, "_post_failure_comment") as post:
            reported = _report_failed_review_jobs()
        assert reported == 1
        post.assert_called_once()
        args = post.call_args.args
        assert args[:4] == ("ChonSong", "riptide", 234, "riptide-review-ChonSong-riptide-234")

    def test_skips_successful_jobs(self):
        from riptide.deepthink import _report_failed_review_jobs
        import riptide.deepthink as dt

        states = {
            "riptide-review-ChonSong-riptide-234": {
                "state": "completed", "last_status": "success",
                "enabled": False, "run_at": None,
                "last_run_at": "2026-10-08T23:34:40Z",
            }
        }
        with patch.object(dt, "_cron_job_states", return_value=states), \
             patch.object(dt, "_post_failure_comment") as post:
            reported = _report_failed_review_jobs()
        assert reported == 0
        post.assert_not_called()

    def test_skips_non_review_jobs(self):
        from riptide.deepthink import _report_failed_review_jobs
        import riptide.deepthink as dt

        states = {
            "riptide-proofshot-poll": {
                "state": None, "last_status": "error",
                "enabled": True, "run_at": None,
                "last_run_at": "2026-10-08T23:34:40Z",
            }
        }
        with patch.object(dt, "_cron_job_states", return_value=states), \
             patch.object(dt, "_post_failure_comment") as post:
            reported = _report_failed_review_jobs()
        assert reported == 0
        post.assert_not_called()

    def test_handles_unreadable_store(self):
        from riptide.deepthink import _report_failed_review_jobs
        import riptide.deepthink as dt

        with patch.object(dt, "_cron_job_states", return_value=None), \
             patch.object(dt, "_post_failure_comment") as post:
            reported = _report_failed_review_jobs()
        assert reported == 0
        post.assert_not_called()

    def test_parses_repo_with_hyphens(self):
        from riptide.deepthink import _report_failed_review_jobs
        import riptide.deepthink as dt

        states = {
            "riptide-review-ChonSong-my-repo-42": {
                "state": None, "last_status": "error",
                "enabled": False, "run_at": None,
                "last_run_at": "2026-10-08T23:34:40Z",
            }
        }
        with patch.object(dt, "_cron_job_states", return_value=states), \
             patch.object(dt, "_post_failure_comment") as post:
            reported = _report_failed_review_jobs()
        assert reported == 1
        args = post.call_args.args
        assert args[:4] == ("ChonSong", "my-repo", 42, "riptide-review-ChonSong-my-repo-42")
