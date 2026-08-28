#!/usr/bin/env python3
"""Tests for riptide/pipeline/ — conductor, work_state, engine."""

from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from riptide.pipeline.work_state import create_workstream, create_track, read_state, write_state
from riptide.pipeline.conductor import Conductor
from riptide.pipeline.engine import Engine


class TestCreateWorkstream:
    """Test that create_workstream accepts role and pipeline parameters."""

    def test_create_workstream_with_role(self):
        """create_workstream should accept and store a role."""
        ws = create_workstream(
            "test-track",
            "ws-test",
            role="probe",
            pipeline=["step1", "step2"],
        )
        assert ws["role"] == "probe"
        assert ws["pipeline"] == ["step1", "step2"]

    def test_create_workstream_default_role(self):
        """create_workstream should default role to 'engine'."""
        ws = create_workstream("test-track", "ws-default")
        assert ws["role"] == "engine"
        assert ws["pipeline"] == []

    def test_create_workstream_without_role(self):
        """create_workstream without role should work (backwards compatible)."""
        ws = create_workstream("test-track", "ws-no-role")
        assert ws["status"] == "pending"
        assert "inputs" in ws


class TestConductorInit:
    """Test that Conductor.__init__ correctly references self.track."""

    def test_conductor_init_valid_track(self):
        """Conductor should initialize with a valid track."""
        track = create_track("test-track", name="Test Track", phase="Review", repos={})
        conductor = Conductor("test-track")
        assert conductor.track_id == "test-track"
        assert conductor.track is not None

    def test_conductor_init_invalid_track(self):
        """Conductor should raise ValueError for non-existent track."""
        with pytest.raises(ValueError, match="not found"):
            Conductor("non-existent-track")


class TestEngine:
    """Test Engine.run() with shell commands."""

    def test_engine_run_success(self):
        """Engine should return success for a valid command."""
        engine = Engine()
        result = engine.run("echo hello")
        assert result["success"] is True
        assert "hello" in result["stdout"]

    def test_engine_run_failure(self):
        """Engine should return failure for an invalid command."""
        engine = Engine()
        result = engine.run("false")
        assert result["success"] is False

    def test_engine_run_timeout(self):
        """Engine should handle timeout gracefully."""
        engine = Engine()
        result = engine.run("sleep 10", timeout=1)
        assert result["success"] is False
        assert result.get("timed_out") is True


class TestWorkStateThreadSafety:
    """Test that work_state read-modify-write is thread-safe."""

    def test_concurrent_writes(self):
        """Concurrent writes should not corrupt state."""
        import threading

        errors = []

        def write_state_thread(thread_id):
            try:
                for i in range(10):
                    ws = create_workstream(
                        f"track-{thread_id}",
                        f"ws-{i}",
                        role="engine",
                    )
                    assert ws["status"] == "pending"
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_state_thread, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent write errors: {errors}"
