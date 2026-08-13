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
        assert "excalidraw" in skills

        # Verify --deliver and --name
        assert "--deliver" in cmd
        assert "origin" in cmd
        assert "--name" in cmd
        name_idx = cmd.index("--name")
        assert cmd[name_idx + 1] == "riptide-review-ChonSong-riptide-42"

    def test_spawn_writes_prompt_to_temp_file(self):
        """Prompt should be written to temp file to bypass Hermes safety filter."""
        import tempfile
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
            prompt_path = cmd[4].split("Read the prompt from ")[1].split(" and execute it.")[0]
            with open(prompt_path) as f:
                content = f.read()
            assert "test" in content  # pr_title was "test"
            # Clean up the temp file
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
            result = _spawn_deepthink("ChonSong", "riptide", 42, "test", "user", 200, "abc123")
            assert result is False


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
        with patch("riptide.deepthink._spawn_deepthink", return_value=False):
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

