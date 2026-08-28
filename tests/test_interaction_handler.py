#!/usr/bin/env python3
"""
Tests for riptide/interaction_handler.py — unified @riptide-bot command router.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def base_payload():
    """Minimal issue_comment payload."""
    return {
        "comment": {
            "id": 999,
            "body": "",
            "user": {"login": "testuser", "type": "User"},
            "performed_via_github_app": None,
        },
        "issue": {
            "number": 42,
            "pull_request": {"url": "https://api.github.com/repos/owner/repo/pulls/42"},
        },
        "repository": {"full_name": "owner/repo", "name": "repo"},
        "installation": {"id": 12345},
    }


def make_payload(body, commenter="testuser", pr_author="testuser"):
    """Create a payload with given body and commenter."""
    return {
        "comment": {
            "id": 999,
            "body": body,
            "user": {"login": commenter, "type": "User"},
            "performed_via_github_app": None,
        },
        "issue": {
            "number": 42,
            "pull_request": {
                "url": "https://api.github.com/repos/owner/repo/pulls/42",
                "user": {"login": pr_author},
            },
        },
        "repository": {"full_name": "owner/repo", "name": "repo"},
        "installation": {"id": 12345},
    }


# ── handle_command() — Basic routing ─────────────────────────────────────────


class TestHandleCommandBasic:
    """Test basic command routing: returns None for non-commands."""

    def test_returns_none_for_empty_body(self):
        from riptide.interaction_handler import handle_command

        result = handle_command(
            payload=make_payload(""),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="",
            commenter="user",
        )
        assert result is None

    def test_returns_none_for_no_mention(self):
        from riptide.interaction_handler import handle_command

        result = handle_command(
            payload=make_payload("Just a normal comment"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="Just a normal comment",
            commenter="user",
        )
        assert result is None

    def test_returns_none_for_unrecognized_command(self):
        from riptide.interaction_handler import handle_command

        result = handle_command(
            payload=make_payload("@riptide-bot somethingrandom"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot somethingrandom",
            commenter="user",
        )
        assert result is None


# ── Help command ──────────────────────────────────────────────────────────────


class TestHelpCommand:
    """Test @riptide-bot help returns the help card."""

    def test_help_returns_text(self):
        from riptide.interaction_handler import handle_command

        result = handle_command(
            payload=make_payload("@riptide-bot help"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot help",
            commenter="user",
        )
        assert result is not None
        assert "Riptide Bot Commands" in result
        assert "`@riptide-bot review`" in result
        assert "fix" in result
        assert "proofshot" in result

    def test_help_case_insensitive(self):
        from riptide.interaction_handler import handle_command

        result = handle_command(
            payload=make_payload("@riptide-bot HELP"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot HELP",
            commenter="user",
        )
        assert result is not None
        assert "Commands" in result


# ── Status command ────────────────────────────────────────────────────────────


class TestStatusCommand:
    """Test @riptide-bot status returns status info."""

    def test_status_returns_text(self):
        from riptide.interaction_handler import handle_command

        result = handle_command(
            payload=make_payload("@riptide-bot status"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot status",
            commenter="user",
        )
        assert result is not None
        assert "Riptide Status" in result

    def test_status_mentions_pr_key(self):
        from riptide.interaction_handler import handle_command

        result = handle_command(
            payload=make_payload("@riptide-bot status"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot status",
            commenter="user",
        )
        assert "owner/repo#42" in result


# ── Review command ────────────────────────────────────────────────────────────


class TestReviewCommand:
    """Test @riptide-bot review routing to deepthink handler."""

    @patch("riptide.webhook.github_client")
    @patch("riptide.deepthink.handle_review_command")
    def test_review_routes_to_handler(self, mock_handle, mock_gc):
        from riptide.interaction_handler import handle_command

        mock_handle.return_value = "Review triggered!"

        result = handle_command(
            payload=make_payload("@riptide-bot review"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot review",
            commenter="testuser",
        )
        assert result == "Review triggered!"
        mock_handle.assert_called_once()

    @patch("riptide.webhook.github_client")
    @patch("riptide.deepthink.handle_review_command")
    def test_deepthink_alias(self, mock_handle, mock_gc):
        from riptide.interaction_handler import handle_command

        mock_handle.return_value = "Review triggered!"

        result = handle_command(
            payload=make_payload("@riptide-bot deepthink"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot deepthink",
            commenter="testuser",
        )
        assert result == "Review triggered!"

    @patch("riptide.webhook.github_client")
    @patch("riptide.deepthink.handle_review_command")
    def test_full_review_alias(self, mock_handle, mock_gc):
        from riptide.interaction_handler import handle_command

        mock_handle.return_value = "Review triggered!"

        result = handle_command(
            payload=make_payload("@riptide-bot full review"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot full review",
            commenter="testuser",
        )
        assert result == "Review triggered!"

    @patch("riptide.webhook.github_client")
    @patch("riptide.deepthink.handle_review_command")
    def test_review_unauthorized_returns_auth_error(self, mock_handle, mock_gc):
        """Auth failure comes from deepthink handler."""
        from riptide.interaction_handler import handle_command

        mock_handle.return_value = "🚫 **Not authorized.**"

        result = handle_command(
            payload=make_payload("@riptide-bot review", commenter="randomuser"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot review",
            commenter="randomuser",
        )
        assert result == "🚫 **Not authorized.**"


# ── Fix command ──────────────────────────────────────────────────────────────


class TestFixCommand:
    """Test @riptide-bot fix routing and authorization."""

    @patch("riptide.webhook.github_client")
    @patch("riptide.fixer.handle_fix_command")
    def test_fix_authorized(self, mock_handle, mock_gc):
        from riptide.interaction_handler import handle_command

        mock_handle.return_value = "Fix triggered!"

        result = handle_command(
            payload=make_payload("@riptide-bot fix", commenter="owner"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot fix",
            commenter="owner",
        )
        assert result == "Fix triggered!"
        mock_handle.assert_called_once()

    def test_fix_unauthorized(self):
        from riptide.interaction_handler import handle_command

        result = handle_command(
            payload=make_payload("@riptide-bot fix", commenter="randomuser"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot fix",
            commenter="randomuser",
        )
        assert result is not None
        assert "Not authorized" in result

    @patch("riptide.webhook.github_client")
    @patch("riptide.fixer.handle_fix_command")
    def test_fix_with_description(self, mock_handle, mock_gc):
        from riptide.interaction_handler import handle_command

        mock_handle.return_value = "Fix triggered!"

        result = handle_command(
            payload=make_payload("@riptide-bot fix the bug in webhook.py", commenter="owner"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot fix the bug in webhook.py",
            commenter="owner",
        )
        assert result == "Fix triggered!"
        # Check description was passed
        call_args = mock_handle.call_args
        assert "the bug in webhook.py" in call_args[0] or "the bug in webhook.py" in str(call_args)


# ── Proofshot command ────────────────────────────────────────────────────────


class TestProofshotCommand:
    """Test @riptide-bot proofshot routing and authorization."""

    @patch("riptide.webhook.github_client")
    @patch("riptide.visual.handle_visual_command")
    def test_proofshot_authorized(self, mock_handle, mock_gc):
        from riptide.interaction_handler import handle_command

        mock_handle.return_value = "Visual triggered!"

        result = handle_command(
            payload=make_payload("@riptide-bot proofshot", commenter="owner"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot proofshot",
            commenter="owner",
        )
        assert result == "Visual triggered!"
        mock_handle.assert_called_once()

    def test_proofshot_unauthorized(self):
        from riptide.interaction_handler import handle_command

        result = handle_command(
            payload=make_payload("@riptide-bot proofshot", commenter="randomuser"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot proofshot",
            commenter="randomuser",
        )
        assert result is not None
        assert "Not authorized" in result

    @patch("riptide.webhook.github_client")
    @patch("riptide.visual.handle_visual_command")
    def test_visual_alias(self, mock_handle, mock_gc):
        from riptide.interaction_handler import handle_command

        mock_handle.return_value = "Visual triggered!"

        result = handle_command(
            payload=make_payload("@riptide-bot visual", commenter="owner"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot visual",
            commenter="owner",
        )
        assert result == "Visual triggered!"


# ── Relabel command ──────────────────────────────────────────────────────────


class TestRelabelCommand:
    """Test @riptide-bot relabel routing."""

    @patch("riptide.webhook.github_client")
    @patch("riptide.webhook.get_labeler")
    def test_relabel_routes_to_labeler(self, mock_get_labeler, mock_gc):
        from riptide.interaction_handler import handle_command

        mock_labeler = MagicMock()
        mock_labeler.classify_pr.return_value = ["type/bug", "priority/high"]
        mock_get_labeler.return_value = mock_labeler
        mock_gc.return_value = MagicMock()

        result = handle_command(
            payload=make_payload("@riptide-bot relabel"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot relabel",
            commenter="user",
        )
        assert result is not None
        assert "Labels re-applied" in result

    @patch("riptide.webhook.github_client")
    @patch("riptide.webhook.get_labeler")
    def test_relabel_unavailable_labeler(self, mock_get_labeler, mock_gc):
        from riptide.interaction_handler import handle_command

        mock_get_labeler.return_value = None

        result = handle_command(
            payload=make_payload("@riptide-bot relabel"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot relabel",
            commenter="user",
        )
        assert result is not None
        assert "Labeler not available" in result


# ── Explain command ──────────────────────────────────────────────────────────


class TestExplainCommand:
    """Test @riptide-bot explain <n> routing."""

    @patch("riptide.webhook.github_client")
    def test_explain_with_findings(self, mock_gc):
        from riptide.interaction_handler import handle_command

        mock_client = MagicMock()
        mock_client.get_issue_comments.return_value = [
            {
                "id": 100,
                "body": "@riptide-bot review\n\n## 🔍 Findings\n\n1. First finding details.\n2. Second finding details.",
                "created_at": "2024-01-01T00:00:00Z",
            }
        ]
        mock_gc.return_value = mock_client

        result = handle_command(
            payload=make_payload("@riptide-bot explain 1"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot explain 1",
            commenter="user",
        )
        assert result is not None
        assert "Finding #1" in result
        assert "First finding" in result

    @patch("riptide.webhook.github_client")
    def test_explain_out_of_range(self, mock_gc):
        from riptide.interaction_handler import handle_command

        mock_client = MagicMock()
        mock_client.get_issue_comments.return_value = [
            {
                "id": 100,
                "body": "@riptide-bot review\n\n## 🔍 Findings\n\n1. Only one finding.",
                "created_at": "2024-01-01T00:00:00Z",
            }
        ]
        mock_gc.return_value = mock_client

        result = handle_command(
            payload=make_payload("@riptide-bot explain 5"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot explain 5",
            commenter="user",
        )
        assert result is not None
        assert "not found" in result
        assert "1 finding" in result

    @patch("riptide.webhook.github_client")
    def test_explain_no_review_comment(self, mock_gc):
        from riptide.interaction_handler import handle_command

        mock_client = MagicMock()
        mock_client.get_issue_comments.return_value = []
        mock_gc.return_value = mock_client

        result = handle_command(
            payload=make_payload("@riptide-bot explain 1"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot explain 1",
            commenter="user",
        )
        assert result is not None
        assert "No @riptide-bot review comment found" in result


# ── _parse_findings() ────────────────────────────────────────────────────────


class TestParseFindings:
    """Test the _parse_findings() helper."""

    def test_numbered_format(self):
        from riptide.interaction_handler import _parse_findings

        body = "## 🔍 Findings\n\n1. First issue.\n2. Second issue.\n3. Third issue."
        results = _parse_findings(body)
        assert len(results) >= 2

    def test_header_format(self):
        from riptide.interaction_handler import _parse_findings

        body = "## Finding 1\nDetails here.\n## Finding 2\nMore details."
        results = _parse_findings(body)
        assert len(results) >= 2

    def test_bullet_format(self):
        from riptide.interaction_handler import _parse_findings

        body = "Findings:\n\n- Issue one.\n- Issue two."
        results = _parse_findings(body)
        assert len(results) >= 2

    def test_empty_body(self):
        from riptide.interaction_handler import _parse_findings

        results = _parse_findings("")
        assert results == []


# ── _is_authorized() ─────────────────────────────────────────────────────────


class TestIsAuthorized:
    """Test the _is_authorized() helper."""

    def test_author_authorized(self):
        from riptide.interaction_handler import _is_authorized

        assert _is_authorized("testuser", "testuser", "owner") is True

    def test_owner_authorized(self):
        from riptide.interaction_handler import _is_authorized

        assert _is_authorized("owner", "testuser", "owner") is True

    def test_our_bot_authorized(self):
        from riptide.interaction_handler import _is_authorized, OUR_USERNAME

        assert _is_authorized(OUR_USERNAME, "testuser", "owner") is True

    def test_random_user_not_authorized(self):
        from riptide.interaction_handler import _is_authorized

        assert _is_authorized("random", "testuser", "owner") is False


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases and misc behavior."""

    def test_none_body(self):
        from riptide.interaction_handler import handle_command

        result = handle_command(
            payload=make_payload(""),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body=None,  # type: ignore
            commenter="user",
        )
        assert result is None

    def test_mention_without_command(self):
        from riptide.interaction_handler import handle_command

        result = handle_command(
            payload=make_payload("Hey @riptide-bot how are you?"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="Hey @riptide-bot how are you?",
            commenter="user",
        )
        assert result is None

    def test_multiple_commands_first_wins(self):
        """When multiple commands present, first matching pattern wins."""
        from riptide.interaction_handler import handle_command

        # Help has highest priority, so even with review present, help wins
        result = handle_command(
            payload=make_payload("@riptide-bot review\n@riptide-bot help"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@riptide-bot review\n@riptide-bot help",
            commenter="user",
        )
        assert result is not None
        assert "Commands" in result  # help text

    def test_case_insensitive_mention(self):
        from riptide.interaction_handler import handle_command

        result = handle_command(
            payload=make_payload("@RIPTIDE-BOT help"),
            delivery_id="d1",
            comment_id=1,
            installation_id=12345,
            owner="owner",
            repo="repo",
            pr_number=42,
            body="@RIPTIDE-BOT help",
            commenter="user",
        )
        assert result is not None
        assert "Commands" in result