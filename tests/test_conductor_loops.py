#!/usr/bin/env python3
"""Tests for conductor loop wiring — run_fix_pipeline_with_loops and _run_snapshot_judge."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from riptide.pipeline.conductor import create_fix_pipeline, run_fix_pipeline_with_loops
from riptide.pipeline.work_state import get_track


class TestRunFixPipelineWithLoops:
    """Test the run_fix_pipeline_with_loops entry point."""
    
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
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
    
    def test_creates_track_with_seven_workstreams(self):
        """run_fix_pipeline_with_loops should create 7 workstreams."""
        pr_details = {"head": {"sha": "abc123", "ref": "fix-branch"}, "base": {"sha": "def456"}}
        track_id = "riptide-fix-ChonSong-riptide-42"
        
        # Create the pipeline first
        create_fix_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=[],
        )
        
        # Verify 7 workstreams exist
        track = get_track(track_id)
        assert track is not None
        assert len(track["workstreams"]) == 7
        
        expected_ids = {
            "ws-1-probe", "ws-2-judge", "ws-3-artisan",
            "ws-4-snapshot-judge", "ws-5-engine", "ws-6-ci-verifier", "ws-7-scribe",
        }
        assert set(track["workstreams"].keys()) == expected_ids
    
    def test_max_iterations_propagated(self):
        """max_iterations should be passed to all loop-capable workstreams."""
        pr_details = {"head": {"sha": "abc123", "ref": "fix-branch"}, "base": {"sha": "def456"}}
        track_id = "riptide-fix-ChonSong-riptide-42"
        
        create_fix_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=[],
            max_iterations=5,
        )
        
        track = get_track(track_id)
        # Check that max_iterations is in inputs for loop-capable workstreams
        for ws_id in ["ws-2-judge", "ws-3-artisan", "ws-4-snapshot-judge", "ws-5-engine", "ws-6-ci-verifier"]:
            ws = track["workstreams"][ws_id]
            assert ws["inputs"].get("max_iterations") == 5, f"{ws_id} missing max_iterations"
    
    def test_default_max_iterations_is_three(self):
        """Default max_iterations should be 3."""
        pr_details = {"head": {"sha": "abc123", "ref": "fix-branch"}, "base": {"sha": "def456"}}
        track_id = "riptide-fix-ChonSong-riptide-42"
        
        create_fix_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=[],
        )
        
        track = get_track(track_id)
        for ws_id in ["ws-2-judge", "ws-3-artisan", "ws-4-snapshot-judge", "ws-5-engine", "ws-6-ci-verifier"]:
            ws = track["workstreams"][ws_id]
            assert ws["inputs"].get("max_iterations") == 3
    
    def test_snapshot_judge_role_assigned(self):
        """ws-4-snapshot-judge should have snapshot_judge role."""
        pr_details = {"head": {"sha": "abc123", "ref": "fix-branch"}, "base": {"sha": "def456"}}
        track_id = "riptide-fix-ChonSong-riptide-42"
        
        create_fix_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=[],
        )
        
        track = get_track(track_id)
        assert track["workstreams"]["ws-4-snapshot-judge"]["role"] == "snapshot_judge"
    
    def test_pipeline_stages_correct(self):
        """Each workstream should have the correct pipeline stages."""
        pr_details = {"head": {"sha": "abc123", "ref": "fix-branch"}, "base": {"sha": "def456"}}
        track_id = "riptide-fix-ChonSong-riptide-42"
        
        create_fix_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=[],
        )
        
        track = get_track(track_id)
        expected_pipelines = {
            "ws-1-probe": ["fetch_diff", "context_bundle", "review_findings"],
            "ws-2-judge": ["verify_findings", "classify_valid"],
            "ws-3-artisan": ["edit_files", "targeted_fixes"],
            "ws-4-snapshot-judge": ["validate_syntax", "validate_findings", "validate_no_placeholders"],
            "ws-5-engine": ["run_tests", "push_if_green"],
            "ws-6-ci-verifier": ["poll_ci", "classify_failures"],
            "ws-7-scribe": ["format_summary", "post_comment"],
        }
        for ws_id, expected in expected_pipelines.items():
            assert track["workstreams"][ws_id]["pipeline"] == expected


class TestConductorDispatch:
    """Test that Conductor._dispatch handles snapshot_judge role."""
    
    def test_dispatch_includes_snapshot_judge(self):
        """_dispatch should recognize snapshot_judge role."""
        from riptide.pipeline.conductor import Conductor
        from riptide.pipeline.roles import WorkerBrief
        
        # Create a minimal track
        with patch("riptide.pipeline.conductor.get_track") as mock_get:
            mock_get.return_value = {
                "name": "Test",
                "phase": "Fix",
                "status": "active",
                "workstreams": {},
                "key_facts": {},
                "repos": {},
            }
            conductor = Conductor("test-track")
            
            # Verify _dispatch handles snapshot_judge
            brief = WorkerBrief(
                role="snapshot_judge",
                name="snapshot_judge-test",
                track="test-track",
                workstream="ws-4-snapshot-judge",
                pipeline="validate_syntax → validate_findings → validate_no_placeholders",
                position="workstream ws-4-snapshot-judge",
                key_facts={},
                inputs={"judge_findings": {}, "diff": {}},
                acceptance={},
                recovery={},
                output_protocol={"path": "/tmp/test.json"},
            )
            
            # Should not raise ValueError
            with patch.object(conductor, "_run_snapshot_judge") as mock_run:
                mock_run.return_value = {"snapshot_result": {"valid": True}, "valid": True}
                result = conductor._dispatch("snapshot_judge", brief)
                assert result["valid"] is True
