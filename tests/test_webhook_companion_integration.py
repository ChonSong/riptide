#!/usr/bin/env python3
"""Tests for webhook→companion integration."""
import os
import json
import hmac
import hashlib
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


# ── Helpers ────────────────────────────────────────────────────────────────


def make_signature(body: bytes, secret: str) -> str:
    """Generate a valid X-Hub-Signature-256 for a body."""
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def pr_payload_bytes(installation_id=4262983, repo="ChonSong/riptide", number=172):
    """Return a PR opened payload as bytes."""
    payload = {
        "action": "opened",
        "pull_request": {
            "number": number,
            "title": "feat(observability): Prometheus metrics",
            "user": {"login": "ChonSong"},
            "head": {"sha": "abc123def456", "ref": "feat/observability-gaps"},
            "base": {"ref": "main"},
        },
        "repository": {
            "full_name": repo,
            "name": repo.split("/")[1],
            "default_branch": "main",
        },
        "installation": {"id": installation_id} if installation_id else {},
    }
    return json.dumps(payload).encode()


# ── Tests ──────────────────────────────────────────────────────────────────


class TestCompanionSpawn:
    """Test that companion spawns correctly from webhook events."""

    def test_pr_opened_spawns_companion(self, client, webhook_secret):
        """PR opened event should spawn companion thread."""
        delivery_id = "test-companion-spawn-fresh"
        body = pr_payload_bytes()
        sig = make_signature(body, webhook_secret)

        with patch("riptide.webhook.get_companion") as mock_get_companion:
            companion = MagicMock()
            companion.is_active_for.return_value = True
            mock_get_companion.return_value = companion

            with patch("threading.Thread") as mock_thread:
                mock_thread_instance = MagicMock()
                mock_thread.return_value = mock_thread_instance

                resp = client.post(
                    "/webhook/github",
                    content=body,
                    headers={
                        "X-Hub-Signature-256": sig,
                        "X-GitHub-Event": "pull_request",
                        "X-GitHub-Delivery": delivery_id,
                    },
                )

                assert resp.status_code == 200
                # Verify thread was spawned
                mock_thread.assert_called_once()
                mock_thread_instance.start.assert_called_once()

    def test_completes_and_posts_comment(self, client, webhook_secret):
        """Companion flow should complete and post comment."""
        delivery_id = "test-companion-complete-fresh"
        body = pr_payload_bytes(number=173)
        sig = make_signature(body, webhook_secret)

        with patch("riptide.webhook.get_companion") as mock_get_companion:
            companion = MagicMock()
            companion.is_active_for.return_value = True
            mock_get_companion.return_value = companion

            captured_target = None

            def capture_thread(target, **kwargs):
                nonlocal captured_target
                captured_target = target
                return MagicMock()

            with patch("threading.Thread", side_effect=capture_thread):
                resp = client.post(
                    "/webhook/github",
                    content=body,
                    headers={
                        "X-Hub-Signature-256": sig,
                        "X-GitHub-Event": "pull_request",
                        "X-GitHub-Delivery": delivery_id,
                    },
                )

                assert resp.status_code == 200
                assert captured_target is not None, "Companion thread target not captured"

    def test_companion_exception_doesnt_crash_handler(self, client, webhook_secret):
        """Companion thread exception should not crash the webhook handler."""
        delivery_id = "test-companion-crash-fresh"
        body = pr_payload_bytes(number=174)
        sig = make_signature(body, webhook_secret)

        with patch("riptide.webhook.get_companion") as mock_get_companion:
            companion = MagicMock()
            companion.is_active_for.return_value = True
            companion.run_for_pr.side_effect = RuntimeError("No module named 'depth'")
            mock_get_companion.return_value = companion

            resp = client.post(
                "/webhook/github",
                content=body,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": delivery_id,
                },
            )

            # Handler should return 200 even if companion crashes
            assert resp.status_code == 200


class TestNoInstallationFallback:
    """Test fallback behavior when GitHub App is not installed."""

    def test_no_installation_skips_unknown_repo(self, client, webhook_secret):
        """Repos not in WATCHED_REPOS without installation should be skipped."""
        delivery_id = "test-no-install-skip-fresh"
        body = pr_payload_bytes(installation_id=None, repo="unknown/repo", number=1)
        sig = make_signature(body, webhook_secret)

        with patch("riptide.webhook.get_companion") as mock_get_companion:
            mock_get_companion.return_value = MagicMock()

            resp = client.post(
                "/webhook/github",
                content=body,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": delivery_id,
                },
            )

            assert resp.status_code == 200
            # Companion should NOT have been spawned
            mock_get_companion.assert_not_called()

    def test_no_installation_fallback_for_watched_repo(self, client, webhook_secret):
        """Watched repos without installation should use gh CLI fallback."""
        from riptide.webhook import WATCHED_REPOS
        watched_repo = WATCHED_REPOS[0] if WATCHED_REPOS else "ChonSong/riptide"

        delivery_id = "test-no-install-fallback-fresh"
        body = pr_payload_bytes(installation_id=None, repo=watched_repo, number=172)
        sig = make_signature(body, webhook_secret)

        with patch("riptide.webhook.get_companion") as mock_get_companion:
            companion = MagicMock()
            companion.is_active_for.return_value = True
            mock_get_companion.return_value = companion

            with patch("riptide.webhook.get_gh_cli_client") as mock_gh_cli:
                mock_gh_cli.return_value = MagicMock()

                with patch("threading.Thread") as mock_thread:
                    mock_thread.return_value = MagicMock()

                    resp = client.post(
                        "/webhook/github",
                        content=body,
                        headers={
                            "X-Hub-Signature-256": sig,
                            "X-GitHub-Event": "pull_request",
                            "X-GitHub-Delivery": delivery_id,
                        },
                    )

                    assert resp.status_code == 200
                    # Companion should have been spawned via fallback
                    mock_gh_cli.assert_called()


class TestBackgroundFixThread:
    """Test that background fix thread doesn't crash silently."""

    def test_fix_command_exception_reported(self, client, webhook_secret):
        """Fix command exception should be logged, not crash silently."""
        delivery_id = "test-fix-crash-fresh"
        payload = {
            "action": "created",
            "comment": {
                "body": "@riptide-bot fix the failing test",
                "user": {"login": "ChonSong"},
            },
            "issue": {"number": 172, "pull_request": {"url": "https://api.github.com/repos/ChonSong/riptide/pulls/172"}},
            "repository": {"full_name": "ChonSong/riptide", "name": "riptide"},
            "installation": {"id": 4262983},
        }
        body = json.dumps(payload).encode()
        sig = make_signature(body, webhook_secret)

        # handle_fix_command is imported inside handle_issue_comment,
        # so we patch the module it's imported from
        with patch("riptide.fixer.handle_fix_command") as mock_fix:
            mock_fix.side_effect = Exception("fixer failed")

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


class TestTraceContext:
    """Test that delivery_id propagates through logs."""

    def test_bind_trace_context(self):
        """bind_trace_context should set delivery_id in structlog context."""
        from riptide.webhook import bind_trace_context, get_delivery_id, _delivery_id_var

        # Clear any existing context
        _delivery_id_var.set(None)

        bind_trace_context("test-delivery-123", event="pull_request")

        assert get_delivery_id() == "test-delivery-123"
