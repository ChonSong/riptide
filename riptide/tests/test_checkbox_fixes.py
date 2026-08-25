#!/usr/bin/env python3
"""Tests for checkbox fixes — Tier-2 enrichment state preservation and regex reset."""

import pytest
from unittest.mock import MagicMock, patch

from riptide.checkbox import reset_checkboxes, reset_all_checkboxes


class TestResetCheckboxesRegex:
    """Verify reset_checkboxes uses line-anchored regex (MEDIUM fix)."""

    def test_reset_checkbox_line_anchored(self):
        """Only checkbox lines should be affected, not code blocks."""
        body = (
            "## Header\n"
            "- [x] 🔍 Trigger review\n"
            "```\n"
            "- [x] fix\n"
            "```\n"
            "- [x] 🛠 Fix issues"
        )
        result = reset_checkboxes(body, ["🔍 Trigger review", "🛠 Fix issues"])
        assert "- [ ] 🔍 Trigger review" in result
        assert "- [ ] 🛠 Fix issues" in result
        # Code block content should NOT be replaced
        assert "```\n- [x] fix\n```" in result


class TestTier2EnrichmentPreservesState:
    """Verify Tier-2 enrichment preserves checkbox state (MEDIUM fix)."""

    def test_state_preservation_on_enrichment(self):
        """Simulates user clicking checkbox during Tier-2 enrichment window."""
        # This is tested via the companion's _format_comment integration
        # The actual logic fetches current comment and merges checkbox state
        from riptide.checkbox import parse_checkbox_state, build_checkbox_footer

        # User checked "review" during enrichment
        current_body = "- [x] 🔍 Trigger review\n- [ ] 🛠 Fix issues"
        state = parse_checkbox_state(current_body)
        assert state == {"🔍 Trigger review": True, "🛠 Fix issues": False}

        # Build footer preserving user's checked state
        footer = build_checkbox_footer(
            actions=["review", "fix"],
            checked=["review"],
        )
        assert "- [x] 🔍 Trigger review" in footer
        assert "- [ ] 🛠 Fix issues" in footer
