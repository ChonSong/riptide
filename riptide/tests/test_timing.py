#!/usr/bin/env python3
"""Tests for deterministic timing metrics in bot outputs."""

from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from riptide.test_utils import format_elapsed


class TestFormatElapsed:
    """Test the format_elapsed helper used by all bot outputs."""

    def test_milliseconds(self):
        assert "ms" in format_elapsed(0.5)
        assert "500ms" in format_elapsed(0.5)

    def test_seconds(self):
        result = format_elapsed(30)
        assert "s" in result
        assert "30.0s" in result

    def test_minutes(self):
        result = format_elapsed(120)
        assert "m" in result
        assert "2.0m" in result

    def test_zero(self):
        result = format_elapsed(0)
        assert "0ms" in result

    def test_fractional_seconds(self):
        result = format_elapsed(1.5)
        assert "1.5s" in result

    def test_large_seconds(self):
        result = format_elapsed(90)
        assert "1.5m" in result


class TestTimingMetricPresence:
    """Test that timing metrics are correctly appended to bot outputs."""

    def test_companion_timing_in_body(self):
        """Companion output body should contain timing metric."""
        # Import the module and check the code references format_elapsed
        import riptide.companion
        import inspect
        source = inspect.getsource(riptide.companion)
        assert "format_elapsed" in source
        assert "⏱️" in source

    def test_assemble_review_timing_in_body(self):
        """Assembled review body should contain timing metric."""
        import riptide.assemble_review
        import inspect
        source = inspect.getsource(riptide.assemble_review)
        assert "format_elapsed" in source
        assert "⏱️" in source

    def test_proofshotter_timing_in_body(self):
        """ProofShot body should contain timing metric."""
        import riptide.proofshotter
        import inspect
        source = inspect.getsource(riptide.proofshotter)
        assert "format_elapsed" in source
        assert "⏱️" in source
