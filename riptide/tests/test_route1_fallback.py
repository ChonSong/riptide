#!/usr/bin/env python3
"""Tests for webhook Route 1 → Route 2 fallback behavior.

Covers the fix for companion crash blocking @riptide-bot review commands:
- When companion.handle_comment raises, Route 2 must still execute.
- When companion.handle_comment returns a skip/resume response, Route 2 must NOT execute.
"""
import json
import hmac
import hashlib
from unittest.mock import patch, MagicMock, call

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_state():
    """Mock state store to avoid pre-existing mark_delivery_done/failed bug on main."""
    with patch("riptide.webhook._get_state_store") as mock_get:
        store = MagicMock()
        store.mark_delivery_done = MagicMock()
        store.mark_delivery_failed = MagicMock()
        mock_get.return_value = store
        yield store


def make_signature(body: bytes, secret: str) -> str:
    """Generate a valid X-Hub-Signature-256 for a body."""
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def review_comment_payload(installation_id=4262983, number=185):
    """Payload for '@riptide-bot review' comment on a PR."""
    inst = {"id": installation_id} if installation_id is not None else {}
    return {
        "action": "created",
        "comment": {
            "id": 9999,
            "body": "@riptide-bot review",
            "user": {"login": "ChonSong", "type": "User"},
        },
        "issue": {
            "number": number,
            "pull_request": {"url": f"https://api.github.com/repos/ChonSong/riptide/pulls/{number}"},
        },
        "repository": {"full_name": "ChonSong/riptide", "name": "riptide"},
        "installation": inst,
    }


def skip_comment_payload(installation_id=4262983, number=185):
    """Payload for '@riptide-bot companion skip' comment on a PR."""
    return {
        "action": "created",
        "comment": {
            "id": 9998,
            "body": "@riptide-bot companion skip",
            "user": {"login": "ChonSong", "type": "User"},
        },
        "issue": {
            "number": number,
            "pull_request": {"url": f"https://api.github.com/repos/ChonSong/riptide/pulls/{number}"},
        },
        "repository": {"full_name": "ChonSong/riptide", "name": "riptide"},
        "installation": {"id": installation_id},
    }


class TestRoute1FallbackToRoute2:
    """When companion crashes, Route 2 (@riptide-bot review) must still run."""

    def test_companion_crash_falls_through_to_route2(self, client, webhook_secret, mock_state):
        """handle_comment raising must not block handle_command."""
        delivery_id = "test-route1-crash-fresh"
        payload = review_comment_payload()
        body = json.dumps(payload).encode()
        sig = make_signature(body, webhook_secret)

        mock_companion = MagicMock()
        mock_companion.handle_comment.side_effect = RuntimeError("UnboundLocalError simulated")

        with patch("riptide.webhook.get_companion", return_value=mock_companion):
            with patch("riptide.interaction_handler.handle_command", return_value="Review done") as mock_cmd:
                with patch("riptide.webhook.github_client") as mock_gh:
                    mock_gh.return_value.post_pr_comment.return_value = True
                    resp = client.post(
                        "/webhook/github",
                        content=body,
                        headers={
                            "X-Hub-Signature-256": sig,
                            "X-GitHub-Event": "issue_comment",
                            "X-GitHub-Delivery": delivery_id,
                        },
                    )

        assert resp.status_code == 200
        mock_cmd.assert_called_once()
        # Response posted exactly once
        mock_gh.return_value.post_pr_comment.assert_called_once()

    def test_companion_crash_response_posted_exactly_once(self, client, webhook_secret, mock_state):
        """handle_command response must be posted exactly once after companion crash."""
        delivery_id = "test-route1-once-fresh"
        payload = review_comment_payload()
        body = json.dumps(payload).encode()
        sig = make_signature(body, webhook_secret)

        mock_companion = MagicMock()
        mock_companion.handle_comment.side_effect = Exception("boom")

        with patch("riptide.webhook.get_companion", return_value=mock_companion):
            with patch("riptide.interaction_handler.handle_command", return_value="📋 Review") as mock_cmd:
                with patch("riptide.webhook.github_client") as mock_gh:
                    mock_gh.return_value.post_pr_comment.return_value = True
                    resp = client.post(
                        "/webhook/github",
                        content=body,
                        headers={
                            "X-Hub-Signature-256": sig,
                            "X-GitHub-Event": "issue_comment",
                            "X-GitHub-Delivery": delivery_id,
                        },
                    )

        assert resp.status_code == 200
        assert mock_gh.return_value.post_pr_comment.call_count == 1
        # Verify the call was for the review response, not a companion reply
        call_args = mock_gh.return_value.post_pr_comment.call_args
        assert "Review" in call_args[0][4] or "Review" in str(call_args)


class TestRoute1SkipsRoute2:
    """When companion handles a skip/resume, Route 2 must NOT run."""

    def test_skip_response_blocks_route2(self, client, webhook_secret, mock_state):
        """Companion skip response must return early — Route 2 not invoked."""
        delivery_id = "test-skip-blocks-fresh"
        payload = skip_comment_payload()
        body = json.dumps(payload).encode()
        sig = make_signature(body, webhook_secret)

        mock_companion = MagicMock()
        mock_companion.handle_comment.return_value = "🤖 Companion will **skip** this PR."

        with patch("riptide.webhook.get_companion", return_value=mock_companion):
            with patch("riptide.interaction_handler.handle_command") as mock_cmd:
                with patch("riptide.webhook.github_client") as mock_gh:
                    mock_gh.return_value.post_pr_comment.return_value = True
                    resp = client.post(
                        "/webhook/github",
                        content=body,
                        headers={
                            "X-Hub-Signature-256": sig,
                            "X-GitHub-Event": "issue_comment",
                            "X-GitHub-Delivery": delivery_id,
                        },
                    )

        assert resp.status_code == 200
        mock_cmd.assert_not_called()

    def test_resume_response_blocks_route2(self, client, webhook_secret, mock_state):
        """Companion resume response must return early — Route 2 not invoked."""
        delivery_id = "test-resume-blocks-fresh"
        payload = {
            "action": "created",
            "comment": {
                "id": 9997,
                "body": "@riptide-bot companion resume",
                "user": {"login": "ChonSong", "type": "User"},
            },
            "issue": {
                "number": 185,
                "pull_request": {"url": "https://api.github.com/repos/ChonSong/riptide/pulls/185"},
            },
            "repository": {"full_name": "ChonSong/riptide", "name": "riptide"},
            "installation": {"id": 4262983},
        }
        body = json.dumps(payload).encode()
        sig = make_signature(body, webhook_secret)

        mock_companion = MagicMock()
        mock_companion.handle_comment.return_value = "🤖 Companion **resumed** for this PR."

        with patch("riptide.webhook.get_companion", return_value=mock_companion):
            with patch("riptide.interaction_handler.handle_command") as mock_cmd:
                with patch("riptide.webhook.github_client") as mock_gh:
                    mock_gh.return_value.post_pr_comment.return_value = True
                    resp = client.post(
                        "/webhook/github",
                        content=body,
                        headers={
                            "X-Hub-Signature-256": sig,
                            "X-GitHub-Event": "issue_comment",
                            "X-GitHub-Delivery": delivery_id,
                        },
                    )

        assert resp.status_code == 200
        mock_cmd.assert_not_called()


class TestRoute1NoInstallation:
    """When no installation_id, Route 1 still runs but doesn't post."""

    def test_companion_crash_no_installation_still_returns_200(self, client, webhook_secret, mock_state):
        """No installation + companion crash → 200, no command attempted."""
        delivery_id = "test-no-install-crash-fresh"
        payload = review_comment_payload(installation_id=None)
        body = json.dumps(payload).encode()
        sig = make_signature(body, webhook_secret)

        mock_companion = MagicMock()
        mock_companion.handle_comment.side_effect = RuntimeError("crash")

        with patch("riptide.webhook.get_companion", return_value=mock_companion):
            with patch("riptide.interaction_handler.handle_command") as mock_cmd:
                resp = client.post(
                    "/webhook/github",
                    content=body,
                    headers={
                        "X-Hub-Signature-256": sig,
                        "X-GitHub-Event": "issue_comment",
                        "X-GitHub-Delivery": delivery_id,
                    },
                )

        assert resp.status_code == 200
        # Route 2 requires installation_id — should not be called
        mock_cmd.assert_not_called()


# ── _build_tier1_body fix: ui_files parameter ────────────────────────────────

class TestBuildTier1Body:
    """Tests for _build_tier1_body ui_files parameter fix."""

    def _make_companion(self):
        """Create a minimal Companion instance for testing."""
        from riptide.companion import Companion
        with patch.object(Companion, '__init__', lambda self, *a, **kw: None):
            c = Companion.__new__(Companion)
            c.enable_deterministic = True
        return c

    def _make_report(self, verdict="review", findings=None):
        """Create a mock deterministic report."""
        from riptide.diff_analyzer import DiffReport, Finding
        return DiffReport(
            verdict=verdict,
            findings=findings or [Finding(category="structure", severity="warning", message="Test finding")],
        )

    def test_build_tier1_body_no_ui_files(self):
        """_build_tier1_body works when ui_files is None."""
        c = self._make_companion()
        report = self._make_report()
        body = c._build_tier1_body(
            emoji="🤖", author="testuser", tldr="Test TLDR",
            deterministic_report=report, ui_files=None,
        )
        assert "Review" in body
        assert "testuser" in body

    def test_build_tier1_body_with_ui_files(self):
        """_build_tier1_body works when ui_files is provided."""
        c = self._make_companion()
        report = self._make_report()
        ui_files = [{"filename": "src/components/Button.tsx"}]
        body = c._build_tier1_body(
            emoji="🤖", author="testuser", tldr="Test TLDR",
            deterministic_report=report, ui_files=ui_files,
        )
        assert "Review" in body
        assert "testuser" in body

    def test_build_tier1_body_trivial_depth(self):
        """_build_tier1_body with trivial depth doesn't promise enrichment."""
        c = self._make_companion()
        report = self._make_report(verdict="pass")
        body = c._build_tier1_body(
            emoji="🤖", author="testuser", tldr="Test TLDR",
            deterministic_report=report, depth="trivial", ui_files=None,
        )
        assert "trivial" in body
        # Trivial footer says "no LLM enrichment needed" — that's expected
        assert "enrichment in progress" not in body
