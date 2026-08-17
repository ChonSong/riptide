"""Tests for riptide/deepthink.py — orchestrator prompt wiring."""
import os
from unittest.mock import patch, MagicMock

import pytest


class TestDeepthinkPromptArgs:
    """Verify the orchestrator prompt passes all required arguments."""

    def test_prompt_includes_diagram_url_arg(self):
        """The assemble_review command must include --diagram-url."""
        from riptide.deepthink import _build_orchestrator_prompt

        prompt = _build_orchestrator_prompt(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_title="test: fix something",
            pr_author="alice",
            total_loc=150,
            head_sha="abc123def4567890",
            data={"files_changed": [], "diff_raw": "", "repo_tree": [], "god_nodes": [], "communities": []},
            diagram_url="https://excalidraw.com/#json=test123",
            deterministic=None,
            pr_created_at="2026-08-16T00:00:00+00:00",
            triggered_at="2026-08-16T00:05:00+00:00",
        )

        assert "--diagram-url" in prompt
        assert "https://excalidraw.com/#json=test123" in prompt

    def test_prompt_includes_triggered_at_arg(self):
        """The assemble_review command must include --triggered-at."""
        from riptide.deepthink import _build_orchestrator_prompt

        prompt = _build_orchestrator_prompt(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_title="test: fix something",
            pr_author="alice",
            total_loc=150,
            head_sha="abc123def4567890",
            data={"files_changed": [], "diff_raw": "", "repo_tree": [], "god_nodes": [], "communities": []},
            diagram_url=None,
            deterministic=None,
            pr_created_at="2026-08-16T00:00:00+00:00",
            triggered_at="2026-08-16T00:05:00+00:00",
        )

        assert "--triggered-at" in prompt
        assert "2026-08-16T00:05:00+00:00" in prompt

    def test_prompt_diagram_url_none_still_includes_arg(self):
        """Even when diagram_url is None, the arg should be present."""
        from riptide.deepthink import _build_orchestrator_prompt

        prompt = _build_orchestrator_prompt(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_title="test: fix something",
            pr_author="alice",
            total_loc=150,
            head_sha="abc123def4567890",
            data={"files_changed": [], "diff_raw": "", "repo_tree": [], "god_nodes": [], "communities": []},
            diagram_url=None,
            deterministic=None,
            pr_created_at="2026-08-16T00:00:00+00:00",
            triggered_at="2026-08-16T00:05:00+00:00",
        )

        assert "--diagram-url" in prompt
        assert "--triggered-at" in prompt
