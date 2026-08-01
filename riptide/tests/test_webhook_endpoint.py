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
