# riptide/tests/test_webhook_endpoint.py
"""
Webhook endpoint: routing, signature validation, idempotency.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


class TestWebhookEndpoint:
    """Test webhook endpoint routing and signature validation."""

    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_invalid_signature_returns_401(self, client, invalid_signature, webhook_body):
        resp = client.post(
            "/webhook/github",
            content=webhook_body,
            headers={
                "X-Hub-Signature-256": invalid_signature,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "delivery-invalid-sig",
            },
        )
        assert resp.status_code == 401

    def test_valid_signature_routes_to_handler(self, client, valid_signature, webhook_body):
        with patch("riptide.webhook.handle_pull_request") as mock_handler:
            mock_handler.return_value = MagicMock(status_code=200)
            resp = client.post(
                "/webhook/github",
                content=webhook_body,
                headers={
                    "X-Hub-Signature-256": valid_signature,
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": "delivery-valid-route-fresh",
                },
            )
            assert resp.status_code == 200
            mock_handler.assert_called_once()

    def test_unhandled_event_returns_200(self, client, valid_signature, webhook_body):
        resp = client.post(
            "/webhook/github",
            content=webhook_body,
            headers={
                "X-Hub-Signature-256": valid_signature,
                "X-GitHub-Event": "status",
                "X-GitHub-Delivery": "delivery-unhandled-fresh",
            },
        )
        assert resp.status_code == 200

    def test_missing_event_header(self, client, valid_signature, webhook_body):
        resp = client.post(
            "/webhook/github",
            content=webhook_body,
            headers={
                "X-Hub-Signature-256": valid_signature,
                "X-GitHub-Delivery": "delivery-missing-event-fresh",
            },
        )
        # Should handle gracefully (either 200 or 400, not 500)
        assert resp.status_code in (200, 400)

    def test_idempotent_delivery_drops_duplicate(self, client, valid_signature, webhook_body):
        """Same X-GitHub-Delivery must not trigger twice."""
        with patch("riptide.webhook.handle_pull_request") as mock_handler:
            mock_handler.return_value = MagicMock(status_code=200)

            # First delivery — should be processed
            resp1 = client.post(
                "/webhook/github",
                content=webhook_body,
                headers={
                    "X-Hub-Signature-256": valid_signature,
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": "delivery-dup-test-1",
                },
            )

            # Same delivery ID again — should be dropped by dedup
            resp2 = client.post(
                "/webhook/github",
                content=webhook_body,
                headers={
                    "X-Hub-Signature-256": valid_signature,
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": "delivery-dup-test-1",
                },
            )

            # Handler called only once — second delivery dropped by dedup
            assert mock_handler.call_count == 1
            assert resp1.status_code == 200
            assert resp2.status_code == 200


class TestPullRequestRunsCompanionFlow:
    """Stage 3: PR events must run the deterministic companion pipeline,
    not the legacy T0 dispatcher."""

    PR_BODY = {
        "action": "opened",
        "installation": {"id": 4242},
        "repository": {"full_name": "owner/repo", "name": "repo"},
        "pull_request": {
            "number": 7,
            "title": "feat: auth",
            "user": {"login": "author"},
        },
    }

    def _post(self, client, signature_factory, delivery):
        import json
        body = json.dumps(self.PR_BODY).encode()
        sig = signature_factory(body)
        return client.post(
            "/webhook/github",
            content=body,
            headers={
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": delivery,
            },
        )

    def test_pr_event_invokes_companion_run_for_pr(self, client, webhook_secret, monkeypatch):
        """Default (no RIPTIDE_T0_FALLBACK): PR path calls companion.run_for_pr."""
        import time
        import json
        from unittest.mock import patch, MagicMock

        # Enable the github-client branch so changed_files are fetched.
        # GITHUB_PRIVATE_KEY_PATH is captured at module import, so patch the
        # module constant directly rather than the env var.
        monkeypatch.setattr("riptide.webhook.GITHUB_PRIVATE_KEY_PATH", "/tmp/test-key.pem")

        called = {}

        def fake_run_for_pr(*args, **kwargs):
            called["args"] = args
            called["kwargs"] = kwargs

        mock_companion = MagicMock()
        mock_companion.is_active_for.return_value = True
        mock_companion.run_for_pr.side_effect = fake_run_for_pr

        mock_github = MagicMock()
        mock_github.get_pr_files.return_value = [{"filename": "a.py", "patch": "+x", "additions": 1, "deletions": 0, "status": "modified"}]
        mock_github.get_pr_details.return_value = {"head": {"sha": "abc123"}}

        with patch("riptide.webhook.get_companion", return_value=mock_companion), \
             patch("riptide.webhook.github_client", return_value=mock_github), \
             patch("riptide.webhook.get_labeler", return_value=None):
            body = json.dumps(self.PR_BODY).encode()
            sig = _sign(body, webhook_secret)
            resp = client.post(
                "/webhook/github",
                content=body,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": "delivery-stage3-run",
                },
            )
            # The companion flow runs on a daemon thread — give it a beat.
            deadline = time.time() + 3
            while time.time() < deadline and not called:
                time.sleep(0.05)

        assert resp.status_code == 200
        assert called, "companion.run_for_pr was never invoked"
        args = called["args"]
        # (installation_id, owner, repo, pr_number, title, author, changed_files)
        assert args[0] == 4242
        assert args[1] == "owner"
        assert args[2] == "repo"
        assert args[3] == 7
        assert args[4] == "feat: auth"
        assert args[5] == "author"
        assert args[6][0]["filename"] == "a.py"
        # Legacy dispatcher must not have been used
        mock_companion.review_pr.assert_not_called()

    def test_t0_fallback_env_routes_to_legacy(self, client, webhook_secret, monkeypatch):
        """RIPTIDE_T0_FALLBACK=1 preserves the legacy dispatcher path."""
        import time
        import json
        from unittest.mock import patch, MagicMock

        monkeypatch.setenv("RIPTIDE_T0_FALLBACK", "1")

        mock_companion = MagicMock()
        mock_companion.is_active_for.return_value = True

        mock_github = MagicMock()
        mock_github.get_pr_files.return_value = []
        mock_github.get_pr_details.return_value = {"head": {"sha": "abc123"}}

        with patch("riptide.webhook.get_companion", return_value=mock_companion), \
             patch("riptide.webhook.github_client", return_value=mock_github), \
             patch("riptide.webhook.get_labeler", return_value=None), \
             patch("riptide.webhook.T0Orchestrator") as mock_t0:
            body = json.dumps(self.PR_BODY).encode()
            sig = _sign(body, webhook_secret)
            resp = client.post(
                "/webhook/github",
                content=body,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": "delivery-stage3-t0",
                },
            )
            # T0 orchestrator runs on a daemon thread.
            deadline = time.time() + 3
            while time.time() < deadline and not mock_t0.return_value.review_pr.called:
                time.sleep(0.05)

        assert resp.status_code == 200
        mock_t0.return_value.review_pr.assert_called_once()
        mock_companion.run_for_pr.assert_not_called()


def _sign(body: bytes, secret: str) -> str:
    """Build a valid X-Hub-Signature-256 for a payload."""
    import hmac
    import hashlib
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
