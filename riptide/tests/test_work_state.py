#!/usr/bin/env python3
"""Tests for work_state.py atomic read-modify-write and unique tmp files."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import pytest

# Add parent dir to path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from riptide.pipeline.work_state import (
    create_workstream, create_track, get_track, get_workstream,
    modify_state, read_state, write_state,
    update_workstream, update_key_facts,
)


class TestModifyState:
    """Test atomic read-modify-write operations."""

    def test_modify_state_atomic(self):
        """modify_state should execute fn under lock."""
        track = create_track("test-track", "Test", "Review", {})
        assert track is not None

        def _update_status(s):
            s["tracks"]["test-track"]["status"] = "done"
        modify_state(_update_status)

        updated = get_track("test-track")
        assert updated is not None
        assert updated["status"] == "done"

    def test_concurrent_modifies(self):
        """Concurrent modify_state calls should not corrupt state."""
        create_track("concurrent-track", "Concurrent", "Review", {})

        errors = []

        def modify(i):
            try:
                for _ in range(10):
                    def _update(s, idx=i):
                        s["tracks"]["concurrent-track"]["last"] = idx
                    modify_state(_update)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=modify, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent modify errors: {errors}"


class TestCreateWorkstream:
    """Test create_workstream with role and pipeline."""

    def test_with_role_and_pipeline(self):
        track = create_track("ws-track", "Test", "Review", {})
        ws = create_workstream(
            "ws-track", "ws-1",
            role="probe",
            pipeline=["step1", "step2"],
        )
        assert ws is not None
        assert ws["role"] == "probe"
        assert ws["pipeline"] == ["step1", "step2"]

    def test_default_role(self):
        track = create_track("ws-track2", "Test", "Review", {})
        ws = create_workstream("ws-track2", "ws-2")
        assert ws is not None
        assert ws["role"] == "engine"
        assert ws["pipeline"] == []


class TestUniqueTmpFiles:
    """Test that concurrent writes don't corrupt state."""

    def test_concurrent_writes(self):
        """Concurrent writes should not lose updates."""
        create_track("write-track", "Write", "Review", {})

        errors = []

        def write(i):
            try:
                for j in range(5):
                    def _append(s, idx=i, jdx=j):
                        s["tracks"]["write-track"].setdefault("updates", []).append(f"{idx}-{jdx}")
                    modify_state(_append)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Write errors: {errors}"

        track = get_track("write-track")
        assert track is not None
        # All 25 updates should be present
        assert len(track.get("updates", [])) == 25
