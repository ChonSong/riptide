#!/usr/bin/env python3
"""
Tests for stratified Hermes sessions architecture.

Verifies:
- Session spawner creates role-specific prompts
- Async conductor chains sessions correctly
- Work-state transitions happen per-worker
- Key facts propagate between workers
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

# Ensure riptide is importable
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from riptide.pipeline.session_spawner import (
    spawn_worker_session,
    ROLE_CONFIGS,
    _build_worker_prompt,
)
from riptide.pipeline.async_conductor import (
    AsyncConductor,
    create_stratified_review_pipeline,
    create_stratified_fix_pipeline,
)
from riptide.pipeline.work_state import (
    read_state, write_state, get_track, create_track, create_workstream,
    update_workstream, next_pending_workstream, update_key_facts,
    WORK_STATE_PATH,
)


@pytest.fixture(autouse=True)
def clean_state():
    """Clean up work-state before each test."""
    if Path(WORK_STATE_PATH).exists():
        os.unlink(WORK_STATE_PATH)
    yield
    if Path(WORK_STATE_PATH).exists():
        os.unlink(WORK_STATE_PATH)


@pytest.fixture
def sample_pr():
    """Sample PR details for testing."""
    return {
        "title": "Test PR",
        "author": {"login": "testuser"},
        "head": {"sha": "abc123def456", "ref": "feature-branch"},
        "additions": 150,
        "deletions": 50,
    }


@pytest.fixture
def sample_files():
    """Sample changed files for testing."""
    return [
        {"filename": "riptide/webhook.py", "additions": 100, "deletions": 30},
        {"filename": "riptide/companion.py", "additions": 50, "deletions": 20},
    ]


class TestRoleConfigs:
    """Test that role configurations are properly stratified."""

    def test_all_roles_defined(self):
        """All 10 worker roles must be defined."""
        expected_roles = {"probe", "judge", "artisan", "engine", "warden", "scribe", "ci_verifier", "test_oracle", "review_memory", "documentarian"}
        assert set(ROLE_CONFIGS.keys()) == expected_roles

    def test_probe_limited_tools(self):
        """Probe should only have terminal and file tools."""
        config = ROLE_CONFIGS["probe"]
        assert config["tools"] == ["terminal", "read_file"]
        assert "write_file" not in config["tools"]
        assert "patch" not in config["tools"]

    def test_judge_no_terminal(self):
        """Judge should not have terminal access (read-only role)."""
        config = ROLE_CONFIGS["judge"]
        assert "terminal" not in config["tools"]
        assert "read_file" in config["tools"]
        assert "write_file" in config["tools"]

    def test_artisan_full_file_access(self):
        """Artisan needs full file access for creating diagrams."""
        config = ROLE_CONFIGS["artisan"]
        assert "read_file" in config["tools"]
        assert "write_file" in config["tools"]
        assert "patch" in config["tools"]
        assert "terminal" in config["tools"]

    def test_engine_terminal_only(self):
        """Engine should only have terminal access."""
        config = ROLE_CONFIGS["engine"]
        assert config["tools"] == ["terminal"]
        assert "write_file" not in config["tools"]

    def test_scribe_has_github_skill(self):
        """Scribe needs GitHub PR lifecycle skill for posting comments."""
        config = ROLE_CONFIGS["scribe"]
        assert "github-pr-lifecycle" in config["skills"]

    def test_ci_verifier_terminal_only(self):
        """CI verifier only needs terminal for gh CLI."""
        config = ROLE_CONFIGS["ci_verifier"]
        assert config["tools"] == ["terminal"]


class TestSpawnWorkerSession:
    """Test session spawning with mocked Hermes CLI."""

    @patch("riptide.pipeline.session_spawner.subprocess.run")
    @patch("riptide.pipeline.session_spawner._is_cron_available", return_value=True)
    def test_spawn_probe_session(self, mock_available, mock_run):
        """Probe session spawns with correct skills and toolsets."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        result = spawn_worker_session(
            role="probe",
            track_id="test-track",
            workstream_id="ws-1",
            inputs={"pr_number": 42, "owner": "ChonSong", "repo": "riptide"},
            acceptance={"output_exists": True},
        )

        assert result is True
        assert mock_run.call_count == 1

        # Check the command
        cmd = mock_run.call_args[0][0]
        assert "hermes" in cmd
        assert "cron" in cmd
        assert "create" in cmd
        assert "--skill" in cmd
        # Probe should have file skill
        skill_idx = cmd.index("--skill")
        assert cmd[skill_idx + 1] == "terminal"

    @patch("riptide.pipeline.session_spawner.subprocess.run")
    @patch("riptide.pipeline.session_spawner._is_cron_available", return_value=True)
    def test_spawn_judge_session(self, mock_available, mock_run):
        """Judge session spawns with deep-think and code-review skills."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        result = spawn_worker_session(
            role="judge",
            track_id="test-track",
            workstream_id="ws-2",
            inputs={"context_path": "/tmp/context.json"},
            acceptance={"findings_valid": True},
        )

        assert result is True
        cmd = mock_run.call_args[0][0]
        skills = [cmd[i+1] for i, x in enumerate(cmd) if x == "--skill"]
        assert "deep-think" in skills
        assert "code-review" in skills

    @patch("riptide.pipeline.session_spawner.subprocess.run")
    @patch("riptide.pipeline.session_spawner._is_cron_available", return_value=True)
    def test_spawn_retries_on_failure(self, mock_available, mock_run):
        """Session spawn retries up to 3 times on failure."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Failed to create job: Blocked",
            stderr="",
        )

        result = spawn_worker_session(
            role="probe",
            track_id="test-track",
            workstream_id="ws-1",
            inputs={},
            acceptance={},
        )

        assert result is False
        assert mock_run.call_count == 3

    @patch("riptide.pipeline.session_spawner.subprocess.run")
    @patch("riptide.pipeline.session_spawner._is_cron_available", return_value=True)
    def test_spawn_handles_unknown_role(self, mock_available, mock_run):
        """Unknown role raises ValueError."""
        with pytest.raises(ValueError, match="Unknown role"):
            spawn_worker_session(
                role="unknown_role",
                track_id="test-track",
                workstream_id="ws-1",
                inputs={},
                acceptance={},
            )


class TestAsyncConductor:
    """Test the async state-machine conductor."""

    def test_create_review_pipeline(self, sample_pr, sample_files):
        """Review pipeline creates 5 workstreams with correct roles."""
        track = create_stratified_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=sample_pr,
            files=sample_files,
        )

        assert track is not None
        workstreams = track.get("workstreams", {})
        assert len(workstreams) == 5

        # Verify roles
        roles = [ws.get("role") for ws in workstreams.values()]
        assert roles == ["probe", "judge", "artisan", "warden", "scribe"]

    def test_create_fix_pipeline(self, sample_pr, sample_files):
        """Fix pipeline creates 6 workstreams with correct roles."""
        track = create_stratified_fix_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=sample_pr,
            files=sample_files,
        )

        assert track is not None
        workstreams = track.get("workstreams", {})
        assert len(workstreams) == 6

        roles = [ws.get("role") for ws in workstreams.values()]
        assert roles == ["probe", "judge", "artisan", "engine", "ci_verifier", "scribe"]

    def test_conductor_dispatches_first_workstream(self, sample_pr, sample_files):
        """Conductor dispatches the first pending workstream."""
        create_stratified_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=sample_pr,
            files=sample_files,
        )

        track_id = "riptide-review-ChonSong-riptide-42"
        conductor = AsyncConductor(track_id)

        with patch("riptide.pipeline.async_conductor.spawn_worker_session", return_value=True) as mock_spawn:
            result = conductor.run()

        # Should have dispatched probe (first workstream)
        assert mock_spawn.call_count == 1
        assert mock_spawn.call_args[1]["role"] == "probe"

        # Probe should be marked in_progress
        track = get_track(track_id)
        assert track is not None
        assert track.get("workstreams", {}).get("ws-1-probe", {}).get("status") == "in_progress"

    def test_conductor_resumes_after_completion(self, sample_pr, sample_files):
        """Conductor resumes and dispatches next workstream after completion."""
        create_stratified_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=sample_pr,
            files=sample_files,
        )

        track_id = "riptide-review-ChonSong-riptide-42"

        # Simulate probe completion: write output to temp file
        probe_output = {
            "graphify": {"nodes": 5},
            "already_reviewed": False,
        }
        probe_path = f"/tmp/riptide-{track_id}-probe-output.json"
        with open(probe_path, "w") as f:
            json.dump(probe_output, f)

        # Simulate probe completion
        update_workstream(track_id, "ws-1-probe", status="done", outputs={
            "graphify": {"nodes": 5},
            "already_reviewed": False,
        })

        conductor = AsyncConductor(track_id)

        with patch("riptide.pipeline.async_conductor.spawn_worker_session", return_value=True) as mock_spawn:
            result = conductor.resume("ws-1-probe")

        # Should have dispatched judge (second workstream)
        assert mock_spawn.call_count == 1
        assert mock_spawn.call_args[1]["role"] == "judge"
        
        # Cleanup
        try:
            os.unlink(probe_path)
        except OSError:
            pass

    def test_key_facts_propagate(self, sample_pr, sample_files):
        """Key facts from probe propagate to judge's prompt."""
        create_stratified_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=sample_pr,
            files=sample_files,
        )

        track_id = "riptide-review-ChonSong-riptide-42"

        # Simulate probe completion: write output to temp file
        probe_output = {
            "graphify": {"nodes": 5, "edges": 3},
            "already_reviewed": False,
            "key_facts": {"blast_radius": "high"},
        }
        probe_path = f"/tmp/riptide-{track_id}-probe-output.json"
        with open(probe_path, "w") as f:
            json.dump(probe_output, f)

        # Simulate probe completion
        update_workstream(track_id, "ws-1-probe", status="done", outputs={
            "graphify": {"nodes": 5, "edges": 3},
            "already_reviewed": False,
        })

        conductor = AsyncConductor(track_id)
        conductor.resume("ws-1-probe")

        # Check key facts were stored
        track = get_track(track_id)
        assert track is not None
        assert track.get("key_facts", {}).get("graphify") == {"nodes": 5, "edges": 3}
        assert track.get("key_facts", {}).get("already_reviewed") is False
        
        # Cleanup
        try:
            os.unlink(probe_path)
        except OSError:
            pass


class TestWorkerPromptGeneration:
    """Test that worker prompts are properly stratified."""

    def test_probe_prompt_contains_role_context(self):
        """Probe prompt includes role-specific instructions."""
        config = ROLE_CONFIGS["probe"]
        prompt = _build_worker_prompt(
            role="probe",
            config=config,
            inputs={"pr_number": 42, "owner": "ChonSong", "repo": "riptide", "output_path": "/tmp/out.json"},
            acceptance={"output_exists": True},
        )

        assert "PROBE" in prompt
        assert "gather deterministic context" in prompt.lower()
        assert "fetch_diff" in prompt or "diff" in prompt.lower()

    def test_judge_prompt_contains_findings_schema(self):
        """Judge prompt includes findings schema."""
        config = ROLE_CONFIGS["judge"]
        prompt = _build_worker_prompt(
            role="judge",
            config=config,
            inputs={"context_path": "/tmp/context.json", "output_path": "/tmp/findings.json"},
            acceptance={"findings_valid": True},
        )

        assert "JUDGE" in prompt
        assert "findings" in prompt.lower()
        assert "severity" in prompt

    def test_scratch_worker_prompt_includes_all_upstream_paths(self):
        """Scribe prompt includes paths to all upstream outputs."""
        config = ROLE_CONFIGS["scribe"]
        prompt = _build_worker_prompt(
            role="scribe",
            config=config,
            inputs={
                "pr_number": 42,
                "owner": "ChonSong",
                "repo": "riptide",
                "context_path": "/tmp/context.json",
                "findings_path": "/tmp/findings.json",
                "diagram_url": "https://example.com/diagram.png",
                "output_path": "/tmp/scribe.json",
            },
            acceptance={"posted": True},
        )

        assert "SCRIBE" in prompt
        assert "assemble_review" in prompt
        assert "ChonSong" in prompt
        assert "riptide" in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
