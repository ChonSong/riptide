#!/usr/bin/env python3
"""Tests for database locking fix and checkbox identification."""

import pytest
from unittest.mock import MagicMock, patch
import tempfile
import os
from pathlib import Path

from riptide.state import StateStore


class TestDatabaseLocking:
    """Verify database operations handle locking gracefully."""

    def test_reserve_delivery_no_lock_error(self, tmp_path):
        """reserve_delivery should not raise on database locked."""
        db_path = str(tmp_path / "test.db")
        store = StateStore(db_path)
        # First reservation should succeed
        assert store.reserve_delivery("test-1") is True
        # Duplicate should return False
        assert store.reserve_delivery("test-1") is False

    def test_reserve_job_no_lock_error(self, tmp_path):
        """reserve_job should not raise on database locked."""
        db_path = str(tmp_path / "test.db")
        store = StateStore(db_path)
        result = store.reserve_job("job-1", 123, "t1", "riptide-fix-owner-repo")
        assert result is True

    def test_concurrent_writes(self, tmp_path):
        """Multiple stores writing to same DB should not crash."""
        db_path = str(tmp_path / "test.db")
        store1 = StateStore(db_path)
        store2 = StateStore(db_path)
        
        # Both should be able to write
        assert store1.reserve_delivery("del-1") is True
        assert store2.reserve_delivery("del-2") is True


class TestCheckboxIdentification:
    """Verify checkbox identification by pattern, not via_app."""

    def test_identify_by_checkbox_pattern(self):
        """Comments with checkbox pattern should be identified as ours."""
        from riptide.checkbox import parse_checkbox_state
        
        body = "- [ ] 🔍 Trigger review\n- [ ] 🛠 Fix issues"
        state = parse_checkbox_state(body)
        assert "🔍 Trigger review" in state
        assert state["🔍 Trigger review"] is False

    def test_checked_checkbox_detected(self):
        """Checked checkbox should be detected."""
        from riptide.checkbox import parse_checkbox_state
        
        body = "- [x] 🔍 Trigger review"
        state = parse_checkbox_state(body)
        assert state["🔍 Trigger review"] is True


class TestCheckboxToggleDetection:
    """Verify toggle detection logic."""

    def test_unchecked_to_checked(self):
        """[ ] → [x] should be detected as toggle."""
        from riptide.checkbox import parse_checkbox_toggles
        
        old = "- [ ] 🔍 Trigger review"
        new = "- [x] 🔍 Trigger review"
        toggled = parse_checkbox_toggles(old, new)
        assert "🔍 Trigger review" in toggled

    def test_already_checked_not_toggled(self):
        """[x] → [x] should not be detected as toggle."""
        from riptide.checkbox import parse_checkbox_toggles
        
        old = "- [x] 🔍 Trigger review"
        new = "- [x] 🔍 Trigger review"
        toggled = parse_checkbox_toggles(old, new)
        assert len(toggled) == 0

    def test_uncheck_not_toggled(self):
        """[x] → [ ] should not be detected as toggle."""
        from riptide.checkbox import parse_checkbox_toggles
        
        old = "- [x] 🔍 Trigger review"
        new = "- [ ] 🔍 Trigger review"
        toggled = parse_checkbox_toggles(old, new)
        assert len(toggled) == 0
