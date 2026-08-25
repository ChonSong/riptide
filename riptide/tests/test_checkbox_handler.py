#!/usr/bin/env python3
# riptide/tests/test_checkbox_handler.py — Tests for checkbox toggle handler.

import os
import pytest
from unittest.mock import MagicMock, patch
from fastapi import Response

from riptide.checkbox_handler import (
    handle_checkbox_toggle,
    _is_bot_comment,
    _is_authorized,
    _get_state_store,
)


class TestIsBotComment:
    """Test _is_bot_comment helper."""

    def test_via_app_id_matches(self):
        payload = {
            "comment": {
                "performed_via_github_app": {"id": 4262983},
            }
        }
        with patch.dict(os.environ, {"GITHUB_APP_ID": "4262983"}):
            assert _is_bot_comment(payload) is True

    def test_via_app_id_mismatch(self):
        payload = {
            "comment": {
                "performed_via_github_app": {"id": 999},
            }
        }
        with patch.dict(os.environ, {"GITHUB_APP_ID": "4262983"}):
            assert _is_bot_comment(payload) is False

    def test_bot_user_type(self):
        payload = {
            "comment": {
                "user": {"type": "Bot", "login": "octopus-selfhost[bot]"},
            }
        }
        with patch.dict(os.environ, {"GITHUB_APP_SLUG": "octopus-selfhost"}):
            assert _is_bot_comment(payload) is True

    def test_bot_user_different_slug(self):
        payload = {
            "comment": {
                "user": {"type": "Bot", "login": "other-bot[bot]"},
            }
        }
        with patch.dict(os.environ, {"GITHUB_APP_SLUG": "octopus-selfhost"}):
            assert _is_bot_comment(payload) is False

    def test_human_user(self):
        payload = {
            "comment": {
                "user": {"type": "User", "login": "testuser"},
            }
        }
        assert _is_bot_comment(payload) is False

    def test_no_app_no_bot(self):
        payload = {"comment": {}}
        assert _is_bot_comment(payload) is False


class TestIsAuthorized:
    """Test _is_authorized helper."""

    def test_chonsong_always_authorized(self):
        payload = {
            "issue": {"user": {"login": "otheruser"}},
            "repository": {"owner": {"login": "otherowner"}},
        }
        with patch("riptide.checkbox_handler.BOT_OWNER", "ChonSong"):
            assert _is_authorized("ChonSong", payload) is True

    def test_pr_author_authorized(self):
        payload = {
            "issue": {"user": {"login": "author"}},
            "repository": {"owner": {"login": "owner"}},
        }
        with patch("riptide.checkbox_handler.BOT_OWNER", "ChonSong"):
            assert _is_authorized("author", payload) is True

    def test_repo_owner_authorized(self):
        payload = {
            "issue": {"user": {"login": "other"}},
            "repository": {"owner": {"login": "owner"}},
        }
        with patch("riptide.checkbox_handler.BOT_OWNER", "ChonSong"):
            assert _is_authorized("owner", payload) is True

    def test_random_user_not_authorized(self):
        payload = {
            "issue": {"user": {"login": "author"}},
            "repository": {"owner": {"login": "owner"}},
        }
        with patch("riptide.checkbox_handler.BOT_OWNER", "ChonSong"):
            assert _is_authorized("randomuser", payload) is False


class TestHandleCheckboxToggle:
    """Test handle_checkbox_toggle handler."""

    def _make_payload(self, **overrides):
        payload = {
            "action": "edited",
            "comment": {
                "id": 123,
                "body": "- [x] 🔍 Trigger review\n",
                "user": {"login": "testuser", "type": "User"},
                "performed_via_github_app": None,
            },
            "issue": {
                "number": 42,
                "pull_request": {"url": "..."},
                "user": {"login": "testuser"},
            },
            "changes": {"body": {"from": "- [ ] 🔍 Trigger review\n"}},
            "repository": {
                "full_name": "test/repo",
                "owner": {"login": "test"},
            },
            "installation": {"id": 12345},
        }
        payload.update(overrides)
        return payload

    def test_successful_review_dispatch(self):
        payload = self._make_payload()
        with patch("riptide.checkbox_handler._get_state_store") as mock_state, \
             patch("riptide.checkbox_handler._spawn_deepthink") as mock_review, \
             patch("riptide.checkbox_handler._reset_checkboxes_in_comment"):
            mock_state.return_value.get_last_checkbox_trigger.return_value = None
            resp = handle_checkbox_toggle(payload, "delivery-1", 123)
            assert resp.status_code == 200
            mock_review.assert_called_once()

    def test_skip_bot_comment(self):
        payload = self._make_payload()
        payload["comment"]["performed_via_github_app"] = {"id": 4262983}
        with patch.dict(os.environ, {"GITHUB_APP_ID": "4262983"}):
            resp = handle_checkbox_toggle(payload, "delivery-1", 123)
            assert resp.status_code == 200

    def test_unauthorized_user_skipped(self):
        payload = self._make_payload()
        payload["comment"]["user"]["login"] = "unauthorized_user"
        payload["issue"]["user"]["login"] = "author"
        payload["repository"]["owner"]["login"] = "owner"
        resp = handle_checkbox_toggle(payload, "delivery-1", 123)
        assert resp.status_code == 200

    def test_dedup_within_window(self):
        payload = self._make_payload()
        with patch("riptide.checkbox_handler._get_state_store") as mock_state, \
             patch("riptide.checkbox_handler.time.time") as mock_time:
            mock_time.return_value = 1000.0
            mock_state.return_value.get_last_checkbox_trigger.return_value = 998.0
            resp = handle_checkbox_toggle(payload, "delivery-1", 123)
            assert resp.status_code == 200

    def test_no_toggles_returns_early(self):
        payload = self._make_payload()
        payload["comment"]["body"] = "- [ ] 🔍 Trigger review\n"
        payload["changes"]["body"]["from"] = "- [ ] 🔍 Trigger review\n"
        with patch("riptide.checkbox_handler._get_state_store") as mock_state:
            mock_state.return_value.get_last_checkbox_trigger.return_value = None
            resp = handle_checkbox_toggle(payload, "delivery-1", 123)
            assert resp.status_code == 200

    def test_non_edited_action_returns_early(self):
        payload = self._make_payload(action="created")
        resp = handle_checkbox_toggle(payload, "delivery-1", 123)
        assert resp.status_code == 200

    def test_dispatch_multiple_actions(self):
        payload = self._make_payload()
        payload["comment"]["body"] = (
            "- [x] 🔍 Trigger review\n- [x] 🛠 Fix issues\n"
        )
        payload["changes"]["body"]["from"] = (
            "- [ ] 🔍 Trigger review\n- [ ] 🛠 Fix issues\n"
        )
        with patch("riptide.checkbox_handler._get_state_store") as mock_state, \
             patch("riptide.checkbox_handler._spawn_deepthink") as mock_review, \
             patch("riptide.checkbox_handler._spawn_fix") as mock_fix, \
             patch("riptide.checkbox_handler._reset_checkboxes_in_comment"):
            mock_state.return_value.get_last_checkbox_trigger.return_value = None
            resp = handle_checkbox_toggle(payload, "delivery-1", 123)
            assert resp.status_code == 200
            mock_review.assert_called_once()
            mock_fix.assert_called_once()


class TestDispatchActions:
    """Test action dispatch functions."""

    def test_dispatch_review(self):
        from riptide.checkbox_handler import _dispatch_action
        with patch("riptide.checkbox_handler._spawn_deepthink") as mock:
            _dispatch_action("review", "owner", "repo", 42, "user", "d1", 12345)
            mock.assert_called_once()

    def test_dispatch_fix(self):
        from riptide.checkbox_handler import _dispatch_action
        with patch("riptide.checkbox_handler._spawn_fix") as mock:
            _dispatch_action("fix", "owner", "repo", 42, "user", "d1", 12345)
            mock.assert_called_once()

    def test_dispatch_visual(self):
        from riptide.checkbox_handler import _dispatch_action
        with patch("riptide.checkbox_handler._spawn_proofshot") as mock:
            _dispatch_action("visual", "owner", "repo", 42, "user", "d1", 12345)
            mock.assert_called_once()

    def test_dispatch_relabel(self):
        from riptide.checkbox_handler import _dispatch_action
        with patch("riptide.checkbox_handler._run_relabel") as mock:
            _dispatch_action("relabel", "owner", "repo", 42, "user", "d1", 12345)
            mock.assert_called_once()

    def test_dispatch_unknown_action(self):
        from riptide.checkbox_handler import _dispatch_action
        # Should not raise
        _dispatch_action("unknown", "owner", "repo", 42, "user", "d1", 12345)