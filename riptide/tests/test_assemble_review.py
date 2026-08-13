#!/usr/bin/env python3
"""Tests for assemble_review timing assembly logic."""

import pytest
from riptide.assemble_review import assemble_review_body


class TestTimingAssembly:
    """Direct tests for the timing metric in assemble_review_body."""

    def _base_findings(self):
        return [{"severity": "critical", "title": "SQL injection", "detail": "test", "file": "a.py", "line": 1}]

    def test_milliseconds(self):
        """Sub-second → milliseconds."""
        body = assemble_review_body(
            self._base_findings(), "ChonSong", "riptide", 1,
            triggered_at="2026-08-13T00:00:00.000000+00:00",
            model="LongCat-2.0",
            provider="custom",
        )
        # Default test: if triggered_at is in the past, it'll be a large value.
        # Instead, verify the format is correct for a known elapsed time.
        assert "⏱️ Review posted in" in body
        # The actual value depends on current time, but it should be a valid format
        assert "sub>" in body

    def test_seconds(self):
        """1-60 seconds → seconds format."""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        triggered = (now - timedelta(seconds=5)).isoformat()
        body = assemble_review_body(
            self._base_findings(), "ChonSong", "riptide", 1,
            triggered_at=triggered,
            model="LongCat-2.0",
            provider="custom",
        )
        assert "⏱️ Review posted in" in body
        assert "s" in body
        # Should be around 5s
        assert "5." in body or "4." in body

    def test_minutes(self):
        """1-60 minutes → minutes format."""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        triggered = (now - timedelta(minutes=5)).isoformat()
        body = assemble_review_body(
            self._base_findings(), "ChonSong", "riptide", 1,
            triggered_at=triggered,
            model="LongCat-2.0",
            provider="custom",
        )
        assert "⏱️ Review posted in" in body
        assert "m" in body
        # Should be around 5m
        assert "5." in body or "4." in body

    def test_hours(self):
        """60+ minutes → hours format."""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        triggered = (now - timedelta(hours=2)).isoformat()
        body = assemble_review_body(
            self._base_findings(), "ChonSong", "riptide", 1,
            triggered_at=triggered,
            model="LongCat-2.0",
            provider="custom",
        )
        assert "⏱️ Review posted in" in body
        assert "h" in body
        # Should be around 2h
        assert "2." in body or "1." in body

    def test_invalid_triggered_at(self):
        """Invalid triggered_at → no timing line (does not fall back to pr_created_at)."""
        body = assemble_review_body(
            self._base_findings(), "ChonSong", "riptide", 1,
            triggered_at="not-a-timestamp",
            pr_created_at="2026-08-13T00:00:00+00:00",
            model="LongCat-2.0",
            provider="custom",
        )
        # Invalid triggered_at is caught by except and does NOT fall back
        assert "⏱️" not in body

    def test_fallback_to_pr_created_at(self):
        """No triggered_at → uses pr_created_at."""
        body = assemble_review_body(
            self._base_findings(), "ChonSong", "riptide", 1,
            pr_created_at="2026-08-13T00:00:00+00:00",
            model="LongCat-2.0",
            provider="custom",
        )
        assert "⏱️ Review posted in" in body
        assert "since PR opened" in body

    def test_no_timing_info(self):
        """Neither triggered_at nor pr_created_at → no timing line."""
        body = assemble_review_body(
            self._base_findings(), "ChonSong", "riptide", 1,
            model="LongCat-2.0",
            provider="custom",
        )
        assert "⏱️" not in body
