#!/usr/bin/env python3
# riptide/tests/test_dispatch_ordering.py — Verify dispatch order correctness.

import pytest
from unittest.mock import patch

from riptide.checkbox import (
    parse_checkbox_toggles,
    CHECKBOX_ACTIONS,
)
from riptide.checkbox_handler import (
    handle_checkbox_toggle,
    _dispatch_action,
)


class TestDispatchOrdering:
    """Test that dispatch ordering is correct."""

    def test_toggles_preserve_document_order(self):
        """Toggles should be reported in document order."""
        old = (
            "- [ ] 🛠 Fix issues\n"
            "- [ ] 🔍 Trigger review\n"
        )
        new = (
            "- [x] 🛠 Fix issues\n"
            "- [x] 🔍 Trigger review\n"
        )
        result = parse_checkbox_toggles(old, new)
        # Fix appears first in document
        assert result == ["🛠 Fix issues", "🔍 Trigger review"]

    def test_toggles_reverse_order(self):
        """Toggles in reverse document order."""
        old = (
            "- [ ] 🔍 Trigger review\n"
            "- [ ] 🛠 Fix issues\n"
        )
        new = (
            "- [x] 🔍 Trigger review\n"
            "- [x] 🛠 Fix issues\n"
        )
        result = parse_checkbox_toggles(old, new)
        assert result == ["🔍 Trigger review", "🛠 Fix issues"]

    def test_mixed_toggles_and_untoggles(self):
        """Only check-toggles should be reported, not unchecks."""
        old = (
            "- [x] 🔍 Trigger review\n"
            "- [ ] 🛠 Fix issues\n"
        )
        new = (
            "- [ ] 🔍 Trigger review\n"
            "- [x] 🛠 Fix issues\n"
        )
        result = parse_checkbox_toggles(old, new)
        assert result == ["🛠 Fix issues"]

    def test_handler_dispatches_in_toggle_order(self):
        """Handler should dispatch actions in the order they appear in the comment."""
        payload = {
            "action": "edited",
            "comment": {
                "id": 100,
                "body": (
                    "- [x] 🛠 Fix issues\n"
                    "- [x] 🔍 Trigger review\n"
                ),
                "user": {"login": "testuser", "type": "User"},
                "performed_via_github_app": None,
            },
            "issue": {
                "number": 42,
                "pull_request": {"url": "..."},
                "user": {"login": "testuser"},
            },
            "changes": {"body": {"from": (
                "- [ ] 🛠 Fix issues\n"
                "- [ ] 🔍 Trigger review\n"
            )}},
            "repository": {
                "full_name": "test/repo",
                "owner": {"login": "test"},
            },
            "installation": {"id": 12345},
        }

        call_order = []

        def mock_dispatch(*args, **kwargs):
            call_order.append(kwargs.get("action", args[0] if args else None))

        with patch("riptide.checkbox_handler._get_state_store") as mock_state, \
             patch("riptide.checkbox_handler._dispatch_action", side_effect=mock_dispatch), \
             patch("riptide.checkbox_handler._reset_checkboxes_in_comment"):
            mock_state.return_value.get_last_checkbox_trigger.return_value = None
            resp = handle_checkbox_toggle(payload, "d1", 100)
            assert resp.status_code == 200

        # Fix should be dispatched before review (document order)
        assert call_order == ["fix", "review"]

    def test_single_checkbox_dispatch_order(self):
        """Single checkbox should dispatch once."""
        payload = {
            "action": "edited",
            "comment": {
                "id": 100,
                "body": "- [x] 📸 ProofShot\n",
                "user": {"login": "testuser", "type": "User"},
                "performed_via_github_app": None,
            },
            "issue": {
                "number": 42,
                "pull_request": {"url": "..."},
                "user": {"login": "testuser"},
            },
            "changes": {"body": {"from": "- [ ] 📸 ProofShot\n"}},
            "repository": {
                "full_name": "test/repo",
                "owner": {"login": "test"},
            },
            "installation": {"id": 12345},
        }

        with patch("riptide.checkbox_handler._get_state_store") as mock_state, \
             patch("riptide.checkbox_handler._dispatch_action") as mock_dispatch, \
             patch("riptide.checkbox_handler._reset_checkboxes_in_comment"):
            mock_state.return_value.get_last_checkbox_trigger.return_value = None
            resp = handle_checkbox_toggle(payload, "d1", 100)
            assert resp.status_code == 200
            mock_dispatch.assert_called_once()
            # Check keyword arg
            assert mock_dispatch.call_args.kwargs["action"] == "visual"

    def test_all_four_actions_dispatch_order(self):
        """All four actions dispatched in document order."""
        payload = {
            "action": "edited",
            "comment": {
                "id": 100,
                "body": (
                    "- [x] 🔍 Trigger review\n"
                    "- [x] 🛠 Fix issues\n"
                    "- [x] 📸 ProofShot\n"
                    "- [x] 🏷️ Relabel\n"
                ),
                "user": {"login": "testuser", "type": "User"},
                "performed_via_github_app": None,
            },
            "issue": {
                "number": 42,
                "pull_request": {"url": "..."},
                "user": {"login": "testuser"},
            },
            "changes": {"body": {"from": (
                "- [ ] 🔍 Trigger review\n"
                "- [ ] 🛠 Fix issues\n"
                "- [ ] 📸 ProofShot\n"
                "- [ ] 🏷️ Relabel\n"
            )}},
            "repository": {
                "full_name": "test/repo",
                "owner": {"login": "test"},
            },
            "installation": {"id": 12345},
        }

        with patch("riptide.checkbox_handler._get_state_store") as mock_state, \
             patch("riptide.checkbox_handler._dispatch_action") as mock_dispatch, \
             patch("riptide.checkbox_handler._reset_checkboxes_in_comment"):
            mock_state.return_value.get_last_checkbox_trigger.return_value = None
            resp = handle_checkbox_toggle(payload, "d1", 100)
            assert resp.status_code == 200
            call_order = [c.kwargs["action"] for c in mock_dispatch.call_args_list]
            assert call_order == ["review", "fix", "visual", "relabel"]

    def test_dispatch_action_calls_correct_function(self):
        """Verify dispatch_action routes to the correct handler."""
        with patch("riptide.checkbox_handler._spawn_deepthink") as mock_deepthink, \
             patch("riptide.checkbox_handler._spawn_fix") as mock_fix, \
             patch("riptide.checkbox_handler._spawn_proofshot") as mock_visual, \
             patch("riptide.checkbox_handler._run_relabel") as mock_relabel:
            _dispatch_action("review", "o", "r", 1, "u", "d", 100)
            assert mock_deepthink.called
            assert not mock_fix.called

            _dispatch_action("fix", "o", "r", 1, "u", "d", 100)
            assert mock_fix.called

            _dispatch_action("visual", "o", "r", 1, "u", "d", 100)
            assert mock_visual.called

            _dispatch_action("relabel", "o", "r", 1, "u", "d", 100)
            assert mock_relabel.called


class TestDedupOrdering:
    """Test dedup logic ordering."""

    def test_dedup_allows_after_window(self):
        """After the dedup window, trigger should be allowed."""
        payload = {
            "action": "edited",
            "comment": {
                "id": 100,
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

        import time
        with patch("riptide.checkbox_handler._get_state_store") as mock_state, \
             patch("riptide.checkbox_handler._dispatch_action") as mock_dispatch, \
             patch("riptide.checkbox_handler._reset_checkboxes_in_comment"):
            # Trigger was 10 seconds ago (> 5s window)
            mock_state.return_value.get_last_checkbox_trigger.return_value = (
                time.time() - 10.0
            )
            resp = handle_checkbox_toggle(payload, "d1", 100)
            assert resp.status_code == 200
            mock_dispatch.assert_called_once()

    def test_dedup_blocks_within_window(self):
        """Within the dedup window, trigger should be blocked."""
        payload = {
            "action": "edited",
            "comment": {
                "id": 100,
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

        import time
        with patch("riptide.checkbox_handler._get_state_store") as mock_state, \
             patch("riptide.checkbox_handler._dispatch_action") as mock_dispatch:
            # Trigger was 2 seconds ago (< 5s window)
            mock_state.return_value.get_last_checkbox_trigger.return_value = (
                time.time() - 2.0
            )
            resp = handle_checkbox_toggle(payload, "d1", 100)
            assert resp.status_code == 200
            mock_dispatch.assert_not_called()