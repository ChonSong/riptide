#!/usr/bin/env python3
"""Tests for riptide.pipeline.conductor — create_fix_pipeline functional tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from riptide.pipeline.conductor import create_fix_pipeline


class TestCreateFixPipeline:
    """Functional tests for create_fix_pipeline."""

    def setup_method(self):
        """Set up a temporary work state for each test."""
        self.tmpdir = tempfile.mkdtemp()
        self.work_state_path = os.path.join(self.tmpdir, "work-state.json")
        self._patch = patch(
            "riptide.pipeline.work_state.WORK_STATE_PATH", self.work_state_path
        )
        self._patch.start()

    def teardown_method(self):
        self._patch.stop()
        # Clean up temp files
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_pr_details(self, sha="abc123", ref="fix-branch"):
        return {
            "head": {"sha": sha, "ref": ref},
            "base": {"sha": "def456"},
        }

    def test_creates_track_with_six_workstreams(self):
        """create_fix_pipeline should create a track with 6 workstreams."""
        pr_details = self._make_pr_details()
        result = create_fix_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=[{"filename": "test.py"}],
        )
        assert result is not None
        assert "workstreams" in result
        assert len(result["workstreams"]) == 6
        expected_ids = {
            "ws-1-probe", "ws-2-judge", "ws-3-artisan",
            "ws-4-engine", "ws-5-ci_verifier", "ws-6-scribe",
        }
        assert set(result["workstreams"].keys()) == expected_ids

    def test_returns_fresh_track_with_staged_workstreams(self):
        """Return value must include the newly created workstreams."""
        pr_details = self._make_pr_details()
        result = create_fix_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=[],
        )
        # The returned track must have workstreams (not stale/empty)
        assert result is not None
        assert len(result["workstreams"]) == 6

    def test_idempotent_does_not_reset_existing_workstreams(self):
        """Calling create_fix_pipeline twice should not reset completed workstreams."""
        from riptide.pipeline.work_state import modify_state
        pr_details = self._make_pr_details()
        # First call creates the track
        first = create_fix_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=[],
        )
        # Mark one workstream as done and persist it
        ws_id = "ws-1-probe"
        track_id = "riptide-fix-ChonSong-riptide-42"
        def mark_done(state):
            state["tracks"][track_id]["workstreams"][ws_id]["status"] = "done"
        modify_state(mark_done)
        # Second call should not reset it
        second = create_fix_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=[],
        )
        assert second["workstreams"][ws_id]["status"] == "done"

    def test_raises_on_missing_head_sha(self):
        """create_fix_pipeline should raise ValueError if PR has no head SHA."""
        pr_details = {"head": {}, "base": {"sha": "def456"}}
        with pytest.raises(ValueError, match="no head SHA"):
            create_fix_pipeline(
                owner="ChonSong",
                repo="riptide",
                pr_number=42,
                pr_details=pr_details,
                files=[],
            )

    def test_state_paths_are_unique_per_owner_repo(self):
        """State paths should include owner/repo to avoid collisions."""
        pr_details = self._make_pr_details()
        result = create_fix_pipeline(
            owner="OwnerA",
            repo="repo-a",
            pr_number=1,
            pr_details=pr_details,
            files=[],
        )
        # Check that the judge workstream has a unique path
        judge_inputs = result["workstreams"]["ws-2-judge"]["inputs"]
        context_path = judge_inputs["context_path"]
        assert "OwnerA" in context_path
        assert "repo-a" in context_path
        assert "/tmp/" not in context_path or context_path.startswith(str(Path.home()))

    def test_push_eligible_propagated(self):
        """push_eligible should be passed to artisan and engine workstreams."""
        pr_details = self._make_pr_details()
        result = create_fix_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=[],
            push_eligible=False,
        )
        artisan_inputs = result["workstreams"]["ws-3-artisan"]["inputs"]
        engine_inputs = result["workstreams"]["ws-4-engine"]["inputs"]
        assert artisan_inputs["push_eligible"] is False
        assert engine_inputs["push_eligible"] is False

    def test_workstream_roles_assigned(self):
        """Each workstream should have the correct role."""
        pr_details = self._make_pr_details()
        result = create_fix_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=[],
        )
        expected_roles = {
            "ws-1-probe": "probe",
            "ws-2-judge": "judge",
            "ws-3-artisan": "artisan",
            "ws-4-engine": "engine",
            "ws-5-ci_verifier": "ci_verifier",
            "ws-6-scribe": "scribe",
        }
        for ws_id, expected_role in expected_roles.items():
            assert result["workstreams"][ws_id]["role"] == expected_role

    def test_workstream_pipelines_assigned(self):
        """Each workstream should have the correct pipeline steps."""
        pr_details = self._make_pr_details()
        result = create_fix_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=[],
        )
        expected_pipelines = {
            "ws-1-probe": ["fetch_diff", "context_bundle", "review_findings"],
            "ws-2-judge": ["verify_findings", "classify_valid"],
            "ws-3-artisan": ["edit_files", "targeted_fixes"],
            "ws-4-engine": ["run_tests", "push_if_green"],
            "ws-5-ci_verifier": ["poll_ci", "classify_failures"],
            "ws-6-scribe": ["format_summary", "post_comment"],
        }
        for ws_id, expected_pipeline in expected_pipelines.items():
            assert result["workstreams"][ws_id]["pipeline"] == expected_pipeline
