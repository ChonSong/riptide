#!/usr/bin/env python3
"""Tests for checkbox dispatch ordering — verify dispatch happens before dedup/reset."""

import time
from unittest.mock import MagicMock, patch

import pytest

from riptide.checkbox_handler import handle_checkbox_toggle
from riptide.state import StateStore
import tempfile
from pathlib import Path


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


class TestDispatchOrdering:
    """Verify that dispatch happens before dedup/reset (HIGH fix)."""

    def test_dispatch_called_before_dedup_written(self, tmp_path):
        """Action dispatch should happen before dedup record is written."""
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

        call_order = []

        def mock_dispatch(*args, **kwargs):
            # Check that dedup has NOT been written yet
            pr_key = "owner/repo#456"
            last_trigger = state.get_last_checkbox_trigger(pr_key, "🔍 Trigger review")
            if last_trigger is not None:
                call_order.append("dedup_before_dispatch")
            else:
                call_order.append("dispatch_before_dedup")

        with patch("riptide.checkbox_handler._dispatch_action", side_effect=mock_dispatch):
            handle_checkbox_toggle(
                payload, github, state, 789, "owner", "repo", 456, "user", "pr_author"
            )

        # Dispatch should happen before dedup
        assert "dispatch_before_dedup" in call_order
        assert "dedup_before_dispatch" not in call_order

    def test_dedup_not_written_when_dispatch_fails(self, tmp_path):
        """If dispatch fails, dedup record should NOT be written."""
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

        def mock_dispatch_fails(*args, **kwargs):
            raise Exception("Dispatch failed")

        with patch("riptide.checkbox_handler._dispatch_action", side_effect=mock_dispatch_fails):
            result = handle_checkbox_toggle(
                payload, github, state, 789, "owner", "repo", 456, "user", "pr_author"
            )

        # Should return empty (dispatch failed)
        assert result == []

        # Dedup should NOT be written
        pr_key = "owner/repo#456"
        last_trigger = state.get_last_checkbox_trigger(pr_key, "🔍 Trigger review")
        assert last_trigger is None

    def test_checkbox_reset_after_successful_dispatch(self, tmp_path):
        """Checkbox should be reset only after successful dispatch."""
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

        # Verify update_pr_comment was called (checkbox reset)
        github.update_pr_comment.assert_called_once()

        # Verify the checkbox was reset in the body
        call_args = github.update_pr_comment.call_args
        body = call_args[0][4]  # 5th positional arg is body
        assert "- [ ] 🔍 Trigger review" in body

    def test_multiple_toggles_all_dispatched(self, tmp_path):
        """Multiple checkbox toggles should all be dispatched."""
        payload = {
            "action": "edited",
            "comment": {
                "id": 123,
                "body": "- [x] 🔍 Trigger review\n- [x] 🏷️ Relabel",
                "user": {"login": "user", "type": "User"},
                "performed_via_github_app": {"id": "12345"},
            },
            "changes": {"body": {"from": "- [ ] 🔍 Trigger review\n- [ ] 🏷️ Relabel"}},
            "issue": {"number": 456, "pull_request": {}},
            "repository": {"full_name": "owner/repo", "name": "repo"},
            "installation": {"id": 789},
        }
        state = make_state_store(tmp_path)
        github = make_github_client()

        dispatched_actions = []

        def mock_dispatch(action, *args, **kwargs):
            dispatched_actions.append(action)

        with patch("riptide.checkbox_handler._dispatch_action", side_effect=mock_dispatch):
            result = handle_checkbox_toggle(
                payload, github, state, 789, "owner", "repo", 456, "user", "pr_author"
            )

        assert "review" in result
        assert "relabel" in result
        assert len(dispatched_actions) == 2
