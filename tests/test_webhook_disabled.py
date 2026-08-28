#!/usr/bin/env python3
"""Tests for webhook processing disable functionality."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestWebhookDisabled:
    """Test that webhook processing is disabled (returns 200 for all deliveries)."""

    def test_webhook_returns_200_for_pull_request(self, tmp_path):
        """Webhook should return 200 for pull_request events."""
        import os
        os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"
        os.environ["RIPTIDE_DATA_DIR"] = str(tmp_path)
        os.environ["RIPTIDE_STATE_DB"] = str(tmp_path / "state.db")

        from importlib import reload
        import riptide.webhook
        reload(riptide.webhook)
        from riptide.webhook import app

        client = TestClient(app)
        response = client.post(
            "/webhook/github",
            json={"action": "opened", "pull_request": {"number": 1}},
            headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "test-delivery-pr"},
        )
        assert response.status_code == 200

    def test_webhook_returns_200_for_issue_comment(self, tmp_path):
        """Webhook should return 200 for issue_comment events."""
        import os
        os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"
        os.environ["RIPTIDE_DATA_DIR"] = str(tmp_path)
        os.environ["RIPTIDE_STATE_DB"] = str(tmp_path / "state.db")

        from importlib import reload
        import riptide.webhook
        reload(riptide.webhook)
        from riptide.webhook import app

        client = TestClient(app)
        response = client.post(
            "/webhook/github",
            json={"action": "created", "issue": {"number": 1}, "comment": {"body": "test"}},
            headers={"X-GitHub-Event": "issue_comment", "X-GitHub-Delivery": "test-delivery-comment"},
        )
        assert response.status_code == 200

    def test_webhook_returns_200_for_ping(self, tmp_path):
        """Webhook should return 200 for ping events."""
        import os
        os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"
        os.environ["RIPTIDE_DATA_DIR"] = str(tmp_path)
        os.environ["RIPTIDE_STATE_DB"] = str(tmp_path / "state.db")

        from importlib import reload
        import riptide.webhook
        reload(riptide.webhook)
        from riptide.webhook import app

        client = TestClient(app)
        response = client.post(
            "/webhook/github",
            json={"zen": "Keep it logically awesome."},
            headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "test-delivery-ping"},
        )
        assert response.status_code == 200

    def test_webhook_skips_signature_verification(self, tmp_path):
        """Webhook should skip signature verification (cron poller is source of truth)."""
        import os
        os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"
        os.environ["RIPTIDE_DATA_DIR"] = str(tmp_path)
        os.environ["RIPTIDE_STATE_DB"] = str(tmp_path / "state.db")

        from importlib import reload
        import riptide.webhook
        reload(riptide.webhook)
        from riptide.webhook import app

        client = TestClient(app)
        # Send with invalid signature - should still return 200
        response = client.post(
            "/webhook/github",
            json={"action": "opened", "pull_request": {"number": 1}},
            headers={
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-sig",
                "X-Hub-Signature-256": "sha256=invalid_signature",
            },
        )
        assert response.status_code == 200
