"""Tests for review timing and diagram wiring."""
import os
from unittest.mock import patch, MagicMock

import pytest


class TestDeepthinkPromptArgs:
    """Verify the orchestrator prompt passes all required arguments."""

    def test_prompt_includes_diagram_url_arg(self):
        from riptide.deepthink import _build_orchestrator_prompt

        prompt = _build_orchestrator_prompt(
            owner="ChonSong", repo="riptide", pr_number=42,
            pr_title="test: fix something", pr_author="alice",
            total_loc=150, head_sha="abc123def4567890",
            data={"files_changed": [], "diff_raw": "", "repo_tree": [], "god_nodes": [], "communities": []},
            diagram_url="https://excalidraw.com/#json=test123",
            deterministic=None,
            pr_created_at="2026-08-16T00:00:00+00:00",
            triggered_at="2026-08-16T00:05:00+00:00",
        )

        assert "--diagram-url" in prompt
        assert "https://excalidraw.com/#json=test123" in prompt

    def test_prompt_includes_triggered_at_arg(self):
        from riptide.deepthink import _build_orchestrator_prompt

        prompt = _build_orchestrator_prompt(
            owner="ChonSong", repo="riptide", pr_number=42,
            pr_title="test: fix something", pr_author="alice",
            total_loc=150, head_sha="abc123def4567890",
            data={"files_changed": [], "diff_raw": "", "repo_tree": [], "god_nodes": [], "communities": []},
            diagram_url=None, deterministic=None,
            pr_created_at="2026-08-16T00:00:00+00:00",
            triggered_at="2026-08-16T00:05:00+00:00",
        )

        assert "--triggered-at" in prompt
        assert "2026-08-16T00:05:00+00:00" in prompt


class TestAssembleReviewTiming:
    """Verify assemble_review accepts and forwards triggered_at."""

    def test_triggered_at_included_in_body(self):
        from riptide.assemble_review import assemble_review_body

        body = assemble_review_body(
            findings=[], owner="ChonSong", repo="riptide", pr_number=42,
            triggered_at="2026-08-16T00:05:00+00:00",
        )

        assert "⏱️ Review posted in" in body

    def test_triggered_at_none_works(self):
        from riptide.assemble_review import assemble_review_body

        body = assemble_review_body(
            findings=[], owner="ChonSong", repo="riptide", pr_number=42,
            triggered_at=None,
        )

        assert "Review posted in" not in body

    def test_diagram_url_included_in_body(self):
        from riptide.assemble_review import assemble_review_body

        body = assemble_review_body(
            findings=[], owner="ChonSong", repo="riptide", pr_number=42,
            diagram_url="https://excalidraw.com/#json=abc123",
        )

        assert "https://excalidraw.com/#json=abc123" in body
