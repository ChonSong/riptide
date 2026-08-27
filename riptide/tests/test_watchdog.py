#!/usr/bin/env python3
"""
Tests for pipeline watchdog and monitoring endpoints.

Verifies:
- get_stuck_tracks finds workstreams stuck in in_progress
- cleanup_stuck_tracks marks stuck workstreams as failed
- get_pipeline_status returns progress info
- HTTP endpoints work correctly
"""

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from riptide.pipeline.work_state import (
    read_state, write_state,
    get_track, create_track, create_workstream, update_workstream,
    get_stuck_tracks, cleanup_stuck_tracks, get_pipeline_status,
    WORK_STATE_PATH,
)
from riptide.webhook import app


@pytest.fixture(autouse=True)
def clean_state():
    """Clean up work-state before each test."""
    if Path(WORK_STATE_PATH).exists():
        os.unlink(WORK_STATE_PATH)
    yield
    if Path(WORK_STATE_PATH).exists():
        os.unlink(WORK_STATE_PATH)


class TestStuckTracks:
    """Test stuck-pipeline detection."""

    def test_no_stuck_tracks(self):
        """No tracks = no stuck workstreams."""
        assert get_stuck_tracks() == []

    def test_not_stuck_if_recent(self):
        """Workstream started recently is not stuck."""
        track_id = "test-track"
        create_track(track_id, "Test", "Review", {})
        create_workstream(track_id, "ws-1", role="probe", pipeline=["step1"])
        
        # Set started_at to now
        def _do(state):
            state["tracks"][track_id]["workstreams"]["ws-1"]["started_at"] = datetime.now(timezone.utc).isoformat()
            state["tracks"][track_id]["workstreams"]["ws-1"]["status"] = "in_progress"
        from riptide.pipeline.work_state import modify_state
        modify_state(_do)
        
        stuck = get_stuck_tracks(max_age_minutes=30)
        assert len(stuck) == 0

    def test_stuck_if_old(self):
        """Workstream started >30 min ago is stuck."""
        track_id = "test-track"
        create_track(track_id, "Test", "Review", {})
        create_workstream(track_id, "ws-1", role="probe", pipeline=["step1"])
        
        # Set started_at to 60 minutes ago
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        def _do(state):
            state["tracks"][track_id]["workstreams"]["ws-1"]["started_at"] = old_time
            state["tracks"][track_id]["workstreams"]["ws-1"]["status"] = "in_progress"
        from riptide.pipeline.work_state import modify_state
        modify_state(_do)
        
        stuck = get_stuck_tracks(max_age_minutes=30)
        assert len(stuck) == 1
        assert stuck[0]["workstream_id"] == "ws-1"
        assert stuck[0]["role"] == "probe"
        assert stuck[0]["age_minutes"] > 55  # ~60 min

    def test_cleanup_marks_as_failed(self):
        """Cleanup marks stuck workstreams as failed."""
        track_id = "test-track"
        create_track(track_id, "Test", "Review", {})
        create_workstream(track_id, "ws-1", role="probe", pipeline=["step1"])
        
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        def _do(state):
            state["tracks"][track_id]["workstreams"]["ws-1"]["started_at"] = old_time
            state["tracks"][track_id]["workstreams"]["ws-1"]["status"] = "in_progress"
        from riptide.pipeline.work_state import modify_state
        modify_state(_do)
        
        cleaned = cleanup_stuck_tracks(max_age_minutes=30)
        assert len(cleaned) == 1
        assert cleaned[0]["workstream_id"] == "ws-1"
        
        # Verify marked as failed
        ws = get_track(track_id)["workstreams"]["ws-1"]
        assert ws["status"] == "failed"
        assert "Stuck in_progress" in ws["failure_reason"]


class TestPipelineStatus:
    """Test pipeline status reporting."""

    def test_status_no_track(self):
        """Returns None for non-existent track."""
        assert get_pipeline_status("nonexistent") is None

    def test_status_progress(self):
        """Returns correct progress counts."""
        track_id = "test-track"
        create_track(track_id, "Test Review", "StratifiedReview", {})
        create_workstream(track_id, "ws-1", role="probe", pipeline=["step1"])
        create_workstream(track_id, "ws-2", role="judge", pipeline=["step2"])
        create_workstream(track_id, "ws-3", role="artisan", pipeline=["step3"])
        
        # Mark some as done/failed/in_progress
        update_workstream(track_id, "ws-1", status="done")
        update_workstream(track_id, "ws-2", status="failed")
        update_workstream(track_id, "ws-3", status="in_progress")
        
        status = get_pipeline_status(track_id)
        assert status is not None
        assert status["name"] == "Test Review"
        assert status["progress"]["total"] == 3
        assert status["progress"]["done"] == 1
        assert status["progress"]["failed"] == 1
        assert status["progress"]["in_progress"] == 1
        assert status["progress"]["pending"] == 0
        assert status["progress"]["percent"] == 33.3
        assert status["current_workstream"]["role"] == "artisan"


class TestMonitoringEndpoints:
    """Test HTTP monitoring endpoints."""

    def test_status_endpoint_404(self, client):
        """404 for non-existent track."""
        response = client.get("/conductor/status/nonexistent")
        assert response.status_code == 404

    def test_status_endpoint_200(self, client):
        """200 for existing track."""
        track_id = "test-track"
        create_track(track_id, "Test", "Review", {})
        create_workstream(track_id, "ws-1", role="probe", pipeline=["step1"])
        
        response = client.get(f"/conductor/status/{track_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["track_id"] == track_id
        assert data["progress"]["total"] == 1

    def test_stuck_endpoint(self, client):
        """Returns list of stuck workstreams."""
        response = client.get("/conductor/stuck")
        assert response.status_code == 200
        data = response.json()
        assert "stuck_count" in data
        assert "stuck" in data

    def test_cleanup_endpoint(self, client):
        """Cleanup endpoint marks stuck as failed."""
        track_id = "test-track"
        create_track(track_id, "Test", "Review", {})
        create_workstream(track_id, "ws-1", role="probe", pipeline=["step1"])
        
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        def _do(state):
            state["tracks"][track_id]["workstreams"]["ws-1"]["started_at"] = old_time
            state["tracks"][track_id]["workstreams"]["ws-1"]["status"] = "in_progress"
        from riptide.pipeline.work_state import modify_state
        modify_state(_do)
        
        response = client.post("/conductor/cleanup")
        assert response.status_code == 200
        data = response.json()
        assert data["cleaned_count"] == 1


@pytest.fixture
def client():
    """FastAPI test client."""
    from starlette.testclient import TestClient
    return TestClient(app)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
