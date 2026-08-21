#!/usr/bin/env python3
"""
Tests for riptide/checkbox_handler.py — checkbox toggle handling, authorization, dedup.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from riptide.checkbox_handler import (
    check_authorization,
    handle_checkbox_toggle,
    _dispatch_action,
)
from riptide.state import StateStore
import tempfile
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_state_store(tmp_path):
    """Create an isolated StateStore for testing."""
    return StateStore(str(tmp_path / "state.db"))


def make_github_client():
    """Create a mock GitHub client."""
    client = MagicMock()
    client.get_pr_details.return_value = {
        "user": {"login": "pr_author"},
        "title": "Test PR",
    }
    return client


# ── Authorization ─────────────────────────────────────────────────────────────


class TestAuthorization:
    """Verify authorization gates for checkbox actions."""

    def test_review_anyone(self):
        assert check_authorization("review", "anyone", "pr_author", "owner") is True

    def test_visual_anyone(self):
        assert check_authorization("visual", "anyone", "pr_author", "owner") is True

    def test_relabel_anyone(self):
        assert check_authorization("relabel", "anyone", "pr_author", "owner") is True

    def test_fix_pr_author(self):
        assert check_authorization("fix", "pr_author", "pr_author", "owner") is True

    def test_fix_repo_owner(self):
        assert check_authorization("fix", "owner", "pr_author", "owner") is True

    def test_fix_other_user_denied(self):
        assert check_authorization("fix", "other_user", "pr_author", "owner") is False

    def test_unknown_action_denied(self):
        assert check_authorization("unknown", "anyone", "pr_author", "owner") is False


# ── Checkbox toggle handling ──────────────────────────────────────────────────


class TestHandleCheckboxToggle:
    """Verify handle_checkbox_toggle parses and dispatches correctly."""

    def test_no_toggle(self, tmp_path):
        """No checkbox toggle — should return empty list."""
        payload = {
            "action": "edited",
            "comment": {
                "id": 123,
                "body": "- [ ] 🔍 Trigger review",
                "user": {"login": "user", "type": "User"},
                "performed_via_github_app": {"id": "12345"},
            },
            "changes": {"body": {"from": "- [ ] 🔍 Trigger review"}},
            "issue": {"number": 456, "pull_request": {}},
            "repository": {"full_name": "owner/repo", "name": "repo"},
            "installation": {"id": 789},
        }
        state = make_state_store(tmp_path)
        github = make_github_client()

        result = handle_checkbox_toggle(
            payload, github, state, 789, "owner", "repo", 456, "user", "pr_author"
        )
        assert result == []

    def test_single_toggle_review(self, tmp_path):
        """Single review checkbox toggled — should dispatch review action."""
        payload = {
            "action": "edited",
            "comment": {
                "id": 123,
                "body": "- [x] 🔍 Trigger review",
                "user": {"login": "user", "type": "User"},
                "performed_via_github_app": {"id": "12345"},
            },
            "changes": {"body": {"from": "- [ ] 🔍 Trigger review"}},
            "issue": {"number": 456, "pull_request": {}},
            "repository": {"full_name": "owner/repo", "name": "repo"},
            "installation": {"id": 789},
        }
        state = make_state_store(tmp_path)
        github = make_github_client()

        with patch("riptide.checkbox_handler._dispatch_action") as mock_dispatch:
            result = handle_checkbox_toggle(
                payload, github, state, 789, "owner", "repo", 456, "user", "pr_author"
            )
            assert result == ["review"]
            mock_dispatch.assert_called_once()

    def test_unauthorized_fix(self, tmp_path):
        """Non-author trying to trigger fix — should be rejected."""
        payload = {
            "action": "edited",
            "comment": {
                "id": 123,
                "body": "- [x] 🛠 Fix issues",
                "user": {"login": "other_user", "type": "User"},
                "performed_via_github_app": {"id": "12345"},
            },
            "changes": {"body": {"from": "- [ ] 🛠 Fix issues"}},
            "issue": {"number": 456, "pull_request": {}},
            "repository": {"full_name": "owner/repo", "name": "repo"},
            "installation": {"id": 789},
        }
        state = make_state_store(tmp_path)
        github = make_github_client()

        with patch("riptide.checkbox_handler._dispatch_action") as mock_dispatch:
            result = handle_checkbox_toggle(
                payload, github, state, 789, "owner", "repo", 456, "other_user", "pr_author"
            )
            assert result == []
            mock_dispatch.assert_not_called()

    def test_dedup_within_30s(self, tmp_path):
        """Same action triggered within 30s — should be deduped."""
        payload = {
            "action": "edited",
            "comment": {
                "id": 123,
                "body": "- [x] 🔍 Trigger review",
                "user": {"login": "user", "type": "User"},
                "performed_via_github_app": {"id": "12345"},
            },
            "changes": {"body": {"from": "- [ ] 🔍 Trigger review"}},
            "issue": {"number": 456, "pull_request": {}},
            "repository": {"full_name": "owner/repo", "name": "repo"},
            "installation": {"id": 789},
        }
        state = make_state_store(tmp_path)
        github = make_github_client()

        # First toggle
        with patch("riptide.checkbox_handler._dispatch_action"):
            handle_checkbox_toggle(
                payload, github, state, 789, "owner", "repo", 456, "user", "pr_author"
            )

        # Second toggle (within 30s)
        with patch("riptide.checkbox_handler._dispatch_action") as mock_dispatch:
            result = handle_checkbox_toggle(
                payload, github, state, 789, "owner", "repo", 456, "user", "pr_author"
            )
            assert result == []
            mock_dispatch.assert_not_called()

    def test_bot_user_skipped(self, tmp_path):
        """Bot user toggling checkbox — should be ignored."""
        payload = {
            "action": "edited",
            "comment": {
                "id": 123,
                "body": "- [x] 🔍 Trigger review",
                "user": {"login": "riptide-bot", "type": "Bot"},
                "performed_via_github_app": {"id": "12345"},
            },
            "changes": {"body": {"from": "- [ ] 🔍 Trigger review"}},
            "issue": {"number": 456, "pull_request": {}},
            "repository": {"full_name": "owner/repo", "name": "repo"},
            "installation": {"id": 789},
        }
        state = make_state_store(tmp_path)
        github = make_github_client()

        with patch("riptide.checkbox_handler._dispatch_action") as mock_dispatch:
            result = handle_checkbox_toggle(
                payload, github, state, 789, "owner", "repo", 456, "riptide-bot", "pr_author"
            )
            assert result == []
            mock_dispatch.assert_not_called()

    def test_checkbox_reset(self, tmp_path):
        """After toggle, checkbox should be reset to unchecked."""
        payload = {
            "action": "edited",
            "comment": {
                "id": 123,
                "body": "- [x] 🔍 Trigger review",
                "user": {"login": "user", "type": "User"},
                "performed_via_github_app": {"id": "12345"},
            },
            "changes": {"body": {"from": "- [ ] 🔍 Trigger review"}},
            "issue": {"number": 456, "pull_request": {}},
            "repository": {"full_name": "owner/repo", "name": "repo"},
            "installation": {"id": 789},
        }
        state = make_state_store(tmp_path)
        github = make_github_client()

        with patch("riptide.checkbox_handler._dispatch_action"):
            handle_checkbox_toggle(
                payload, github, state, 789, "owner", "repo", 456, "user", "pr_author"
            )

        # Verify update_pr_comment was called with reset checkbox
        github.update_pr_comment.assert_called_once()
        call_args = github.update_pr_comment.call_args
        body = call_args[0][4]  # 5th positional arg is body
        assert "- [ ] 🔍 Trigger review" in body


# ── Dispatch ───────────────────────────────────────────────────────────────────


class TestDispatchAction:
    """Verify _dispatch_action calls the correct handler."""

    def test_dispatch_review(self, tmp_path):
        github = make_github_client()
        with patch("riptide.deepthink.handle_review_command") as mock_handler:
            mock_handler.return_value = "Review triggered"
            _dispatch_action("review", github, 789, "owner", "repo", 456, "user", "pr_author")
            mock_handler.assert_called_once()

    def test_dispatch_fix(self, tmp_path):
        github = make_github_client()
        with patch("riptide.fixer.handle_fix_command") as mock_handler:
            mock_handler.return_value = "Fix triggered"
            _dispatch_action("fix", github, 789, "owner", "repo", 456, "user", "pr_author")
            mock_handler.assert_called_once()

    def test_dispatch_visual(self, tmp_path):
        github = make_github_client()
        with patch("riptide.visual.handle_visual_command") as mock_handler:
            mock_handler.return_value = "Visual triggered"
            _dispatch_action("visual", github, 789, "owner", "repo", 456, "user", "pr_author")
            mock_handler.assert_called_once()
