#!/usr/bin/env python3
"""
End-to-end integration test for stratified Hermes sessions.

Tests the full pipeline:
1. Creates a track with stratified workstreams
2. Simulates each worker completing (writes output to temp file)
3. Calls the resume endpoint via webhook
4. Verifies the next worker is dispatched
5. Continues through the full chain
6. Verifies the track is complete at the end
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from riptide.pipeline.async_conductor import (
    AsyncConductor,
    create_stratified_review_pipeline,
)
from riptide.pipeline.work_state import (
    get_track,
    get_workstream,
    update_workstream,
    WORK_STATE_PATH,
)
from riptide.pipeline.session_spawner import spawn_worker_session
from riptide.webhook import app


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


class TestEndToEndPipeline:
    """End-to-end test of the stratified pipeline."""

    def test_full_pipeline_chain(self, sample_pr, sample_files):
        """Test the complete pipeline: probe → judge → artisan → warden → scribe."""
        track_id = "riptide-review-ChonSong-riptide-42"
        
        # Create the pipeline
        track = create_stratified_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=sample_pr,
            files=sample_files,
        )
        
        assert track is not None
        assert len(track.get("workstreams", {})) == 5
        
        # Use a context manager that stays open for all dispatch calls
        with patch("riptide.pipeline.session_spawner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            
            # Step 1: Dispatch probe
            conductor = AsyncConductor(track_id)
            result = conductor.run()
            
            assert result["results"][0]["status"] == "dispatched"
            assert result["results"][0]["role"] == "probe"
            
            # Simulate probe completing: write output to temp file
            probe_output = {
                "pr_data": {"title": "Test PR", "author": "testuser"},
                "diff_report": {"files": ["webhook.py"]},
                "bundle": {"findings": []},
                "graphify": {"nodes": 5},
                "already_reviewed": False,
                "previous_findings": [],
                "key_facts": {"blast_radius": "low"},
            }
            probe_path = f"/tmp/riptide-{track_id}-probe-output.json"
            with open(probe_path, "w") as f:
                json.dump(probe_output, f)
            
            # Step 2: Resume from probe completion → dispatches judge
            result = conductor.resume("ws-1-probe")
            
            assert result["results"][0]["status"] == "dispatched"
            assert result["results"][0]["role"] == "judge"
            
            # Verify probe was marked done
            ws = get_workstream(track_id, "ws-1-probe")
            assert ws is not None
            assert ws["status"] == "done"
            
            # Simulate judge completing
            judge_output = {
                "findings": [
                    {
                        "severity": "warning",
                        "title": "Test finding",
                        "detail": "Test detail",
                        "file": "webhook.py",
                        "line": 100,
                        "suggestion": "Fix it",
                    }
                ]
            }
            judge_path = f"/tmp/riptide-{track_id}-judge-output.json"
            with open(judge_path, "w") as f:
                json.dump(judge_output, f)
            
            # Step 3: Resume from judge → dispatches artisan
            result = conductor.resume("ws-2-judge")
            
            assert result["results"][0]["status"] == "dispatched"
            assert result["results"][0]["role"] == "artisan"
            
            # Simulate artisan completing
            artisan_output = {
                "diagram_url": "https://excalidraw.com/#json=test",
                "diagram_path": "/tmp/review.excalidraw",
                "uploaded": True,
            }
            artisan_path = f"/tmp/riptide-{track_id}-artisan-output.json"
            with open(artisan_path, "w") as f:
                json.dump(artisan_output, f)
            
            # Step 4: Resume from artisan → dispatches warden
            result = conductor.resume("ws-3-artisan")
            
            assert result["results"][0]["status"] == "dispatched"
            assert result["results"][0]["role"] == "warden"
            
            # Simulate warden completing
            warden_output = {
                "pass": True,
                "checks": [
                    {"method": "check_file_exists", "passed": True},
                    {"method": "validate_findings", "passed": True},
                ],
                "issues": [],
            }
            warden_path = f"/tmp/riptide-{track_id}-warden-output.json"
            with open(warden_path, "w") as f:
                json.dump(warden_output, f)
            
            # Step 5: Resume from warden → dispatches scribe
            result = conductor.resume("ws-4-warden")
            
            assert result["results"][0]["status"] == "dispatched"
            assert result["results"][0]["role"] == "scribe"
            
            # Simulate scribe completing
            scribe_output = {
                "posted": True,
                "comment_url": "https://github.com/test",
                "body_length": 1000,
            }
            scribe_path = f"/tmp/riptide-{track_id}-scribe-output.json"
            with open(scribe_path, "w") as f:
                json.dump(scribe_output, f)
            
            # Step 6: Resume from scribe → track complete
            result = conductor.resume("ws-5-scribe")
        
        # No more pending workstreams
        assert result["results"] == []
        
        # Verify all workstreams are done
        track = get_track(track_id)
        for ws_id, ws in track["workstreams"].items():
            assert ws["status"] == "done", f"{ws_id} not done: {ws['status']}"
        
        # Cleanup temp files
        for path in [probe_path, judge_path, artisan_path, warden_path, scribe_path]:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_pipeline_with_invalid_output(self, sample_pr, sample_files):
        """Test that invalid worker output stops the pipeline."""
        track_id = "riptide-review-ChonSong-riptide-42"
        
        create_stratified_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=sample_pr,
            files=sample_files,
        )
        
        with patch("riptide.pipeline.session_spawner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            
            conductor = AsyncConductor(track_id)
            result = conductor.run()
            
            # Write INVALID output (missing required fields)
            probe_path = f"/tmp/riptide-{track_id}-probe-output.json"
            with open(probe_path, "w") as f:
                f.write("not valid json")
            
            # Resume should detect invalid output and fail
            result = conductor.resume("ws-1-probe")
        
        assert result["results"][0]["status"] == "failed"
        assert "errors" in result["results"][0]
        
        # Verify probe was marked failed
        ws = get_workstream(track_id, "ws-1-probe")
        assert ws is not None
        assert ws["status"] == "failed"
        
        # Cleanup
        try:
            os.unlink(probe_path)
        except OSError:
            pass

    def test_pipeline_with_missing_output(self, sample_pr, sample_files):
        """Test that missing worker output stops the pipeline."""
        track_id = "riptide-review-ChonSong-riptide-42"
        
        create_stratified_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=sample_pr,
            files=sample_files,
        )
        
        with patch("riptide.pipeline.session_spawner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            
            conductor = AsyncConductor(track_id)
            result = conductor.run()
            
            # Don't write any output - simulate worker failure
            # Resume should detect missing output and fail
            result = conductor.resume("ws-1-probe")
        
        assert result["results"][0]["status"] == "failed"
        assert any("Output file not found" in e for e in result["results"][0]["errors"])

    def test_pipeline_retry(self, sample_pr, sample_files):
        """Test that a failed workstream can be retried."""
        track_id = "riptide-review-ChonSong-riptide-42"
        
        create_stratified_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=sample_pr,
            files=sample_files,
        )
        
        conductor = AsyncConductor(track_id)
        
        # First: dispatch succeeds
        with patch("riptide.pipeline.session_spawner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = conductor.run()
        
        assert result["results"][0]["status"] == "dispatched"
        
        # Simulate worker failing (no output written)
        result = conductor.resume("ws-1-probe")
        assert result["results"][0]["status"] == "failed"
        
        # Write valid output and retry
        probe_path = f"/tmp/riptide-{track_id}-probe-output.json"
        with open(probe_path, "w") as f:
            json.dump({"test": "data"}, f)
        
        with patch("riptide.pipeline.session_spawner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = conductor.retry_workstream("ws-1-probe")
        
        # Result is a dict with status (from _dispatch_workstream)
        assert result["status"] == "dispatched"
        
        # Cleanup
        try:
            os.unlink(probe_path)
        except OSError:
            pass

    def test_conductor_resume_endpoint(self, sample_pr, sample_files):
        """Test the /conductor/resume webhook endpoint."""
        from starlette.testclient import TestClient
        
        track_id = "riptide-review-ChonSong-riptide-42"
        
        create_stratified_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=sample_pr,
            files=sample_files,
        )
        
        with patch("riptide.pipeline.session_spawner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            
            conductor = AsyncConductor(track_id)
            conductor.run()
            
            # Write probe output
            probe_path = f"/tmp/riptide-{track_id}-probe-output.json"
            with open(probe_path, "w") as f:
                json.dump({"test": "data"}, f)
            
            # Call the webhook endpoint
            client = TestClient(app)
            response = client.get(
                f"/conductor/resume?track={track_id}&workstream=ws-1-probe"
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["result"]["results"][0]["status"] == "dispatched"
        
        # Cleanup
        try:
            os.unlink(probe_path)
        except OSError:
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
