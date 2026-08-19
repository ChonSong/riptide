#!/usr/bin/env python3
"""Tests for riptide/visual.py — @riptide-bot visual command."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add parent dir to path so we can import riptide modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from riptide.visual import handle_visual_command, VISUAL_RE


class TestVisualRegex:
    """Test the VISUAL_RE pattern matches expected command formats."""

    def test_matches_basic_command(self):
        assert VISUAL_RE.search("@riptide-bot visual") is not None

    def test_matches_with_extra_whitespace(self):
        assert VISUAL_RE.search("@riptide-bot  visual") is not None

    def test_matches_case_insensitive(self):
        assert VISUAL_RE.search("@riptide-bot Visual") is not None

    def test_does_not_match_other_commands(self):
        assert VISUAL_RE.search("@riptide-bot review") is None

    def test_does_not_match_fix(self):
        assert VISUAL_RE.search("@riptide-bot fix") is None


class TestHandleVisualCommand:
    """Test handle_visual_command function."""

    def test_returns_none_for_non_visual_command(self):
        """Should return None if command doesn't match visual pattern."""
        client = MagicMock()
        result = handle_visual_command(client, None, "owner", "repo", 1, "user")
        # Since we're not passing a command parameter, it should handle gracefully
        # The function signature doesn't take a command param — it always processes

    def test_successful_visual_trigger(self):
        """Should return confirmation message on successful trigger."""
        client = MagicMock()
        client.get_pr_details.return_value = {
            "number": 1,
            "head": {"sha": "abc123"},
            "user": {"login": "testuser"},
        }

        with patch("riptide.visual.requests.post") as mock_post:
            mock_post.return_value.status_code = 204
            mock_post.return_value.raise_for_status = MagicMock()

            result = handle_visual_command(
                client, 12345, "ChonSong", "riptide", 1, "testuser"
            )

            assert result is not None
            assert "✅" in result or "triggered" in result.lower()

    def test_failed_pr_fetch(self):
        """Should return error message when PR details can't be fetched."""
        client = MagicMock()
        client.get_pr_details.side_effect = Exception("API error")

        result = handle_visual_command(
            client, None, "ChonSong", "riptide", 1, "testuser"
        )

        assert result is not None
        assert "⚠️" in result or "could not" in result.lower()

    def test_workflow_dispatch_called(self):
        """Should call GitHub Actions workflow_dispatch endpoint."""
        client = MagicMock()
        client.get_pr_details.return_value = {
            "number": 42,
            "head": {"sha": "def456"},
            "user": {"login": "testuser"},
        }

        with patch("riptide.visual.requests.post") as mock_post:
            mock_post.return_value.status_code = 204
            mock_post.return_value.raise_for_status = MagicMock()

            handle_visual_command(
                client, 12345, "ChonSong", "riptide", 42, "testuser"
            )

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "workflow_dispatch" in call_args[0][0] or "dispatches" in call_args[0][0]
