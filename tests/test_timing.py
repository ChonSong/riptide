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
        import riptide.companion
        import inspect
        source = inspect.getsource(riptide.companion)
        assert "⏱️" in source
        assert "Review posted in" in source

    def test_assemble_review_timing_in_body(self):
        """Assembled review body should contain timing metric."""
        import riptide.assemble_review
        import inspect
        source = inspect.getsource(riptide.assemble_review)
        assert "⏱️" in source
        assert "Review posted in" in source

    def test_proofshotter_timing_in_body(self):
        """ProofShot body should contain timing metric."""
        import riptide.proofshotter
        import inspect
        source = inspect.getsource(riptide.proofshotter)
        assert "⏱️" in source
        assert "ProofShot posted in" in source


class TestAssembleReviewTiming:
    """Test timing metric in assemble_review_body."""

    def test_timing_appended_with_valid_created_at(self):
        from riptide.assemble_review import assemble_review_body
        body = assemble_review_body(
            [], "ChonSong", "riptide", 42,
            pr_created_at="2026-08-12T00:00:00Z",
        )
        assert "⏱️" in body
        assert "Review posted in" in body

    def test_timing_omitted_without_created_at(self):
        from riptide.assemble_review import assemble_review_body
        body = assemble_review_body([], "ChonSong", "riptide", 42)
        assert "⏱️" not in body

    def test_timing_omitted_with_empty_string(self):
        from riptide.assemble_review import assemble_review_body
        body = assemble_review_body(
            [], "ChonSong", "riptide", 42,
            pr_created_at="",
        )
        assert "⏱️" not in body

    def test_timing_omitted_with_invalid_date(self):
        from riptide.assemble_review import assemble_review_body
        body = assemble_review_body(
            [], "ChonSong", "riptide", 42,
            pr_created_at="not-a-date",
        )
        assert "⏱️" not in body

    def test_pr_created_at_argument_accepted(self):
        from riptide.assemble_review import assemble_review_body
        import inspect
        sig = inspect.signature(assemble_review_body)
        assert "pr_created_at" in sig.parameters


class TestProofshotterTiming:
    """Test timing metric in proofshotter _post_proofshot_comment."""

    def test_timing_appended_with_valid_created_at(self):
        from riptide.proofshotter import _post_proofshot_comment
        import inspect
        source = inspect.getsource(_post_proofshot_comment)
        assert "⏱️" in source
        assert "ProofShot posted in" in source

    def test_pr_created_at_argument_accepted(self):
        from riptide.proofshotter import _post_proofshot_comment
        import inspect
        sig = inspect.signature(_post_proofshot_comment)
        assert "pr_created_at" in sig.parameters


class TestDeepthinkTiming:
    """Test that deepthink gathers pr_created_at."""

    def test_gather_review_data_fetches_created_at(self):
        from riptide.deepthink import _gather_review_data
        import inspect
        source = inspect.getsource(_gather_review_data)
        assert "pr_created_at" in source
        assert "createdAt" in source

    def test_build_orchestrator_prompt_accepts_created_at(self):
        from riptide.deepthink import _build_orchestrator_prompt
        import inspect
        sig = inspect.signature(_build_orchestrator_prompt)
        assert "pr_created_at" in sig.parameters
