"""Tests for riptide/webhook.py — webhook handler and auto-deploy."""
import json
import os
from unittest.mock import patch, MagicMock

import pytest


class TestWebhookHealth:
    """Verify health check endpoint."""

    def test_health_returns_ok(self):
        from riptide.webhook import app
        from starlette.testclient import TestClient
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "app": "riptide"}


class TestAutoDeployInvocation:
    """Verify auto-deploy triggers systemd-run with correct script."""

    @patch("riptide.webhook.subprocess.Popen")
    @patch("riptide.webhook.Path.exists", return_value=True)
    @patch("os.access", return_value=True)
    def test_merge_into_default_triggers_deploy(self, mock_access, mock_exists, mock_popen):
        """Merging into default branch triggers exactly one systemd-run invocation."""
        from riptide.webhook import handle_pull_request

        payload = {
            "action": "closed",
            "pull_request": {
                "number": 42,
                "merged": True,
                "base": {"ref": "main"},
            },
            "repository": {"full_name": "ChonSong/riptide"},
            "installation": {"id": 12345},
        }

        import asyncio
        result = asyncio.run(handle_pull_request(payload, "test-delivery-id"))

        # systemd-run must be called exactly once (not twice — was a bug)
        assert mock_popen.call_count == 1, f"Expected 1 call, got {mock_popen.call_count}"
        cmd = mock_popen.call_args[0][0]
        assert "systemd-run" in cmd
        assert "--user" in cmd
        assert "--scope" in cmd
        assert "--property=KillMode=process" in cmd

    @patch("riptide.webhook.subprocess.Popen")
    @patch("riptide.webhook.Path.exists", return_value=True)
    @patch("os.access", return_value=True)
    def test_merge_into_non_default_no_deploy(self, mock_access, mock_exists, mock_popen):
        """Merging into a non-default branch does NOT trigger deploy."""
        from riptide.webhook import handle_pull_request

        payload = {
            "action": "closed",
            "pull_request": {
                "number": 42,
                "merged": True,
                "base": {"ref": "feature/not-default"},
            },
            "repository": {"full_name": "ChonSong/riptide"},
            "installation": {"id": 12345},
        }

        import asyncio
        result = asyncio.run(handle_pull_request(payload, "test-delivery-id"))

        mock_popen.assert_not_called()

    @patch("riptide.webhook.subprocess.Popen")
    @patch("pathlib.Path.exists", return_value=False)
    def test_deploy_script_not_found_no_deploy(self, mock_exists, mock_popen):
        """If deploy script doesn't exist, skip gracefully."""
        from riptide.webhook import handle_pull_request

        payload = {
            "action": "closed",
            "pull_request": {
                "number": 42,
                "merged": True,
                "base": {"ref": "main"},
            },
            "repository": {"full_name": "ChonSong/riptide"},
            "installation": {"id": 12345},
        }

        import asyncio
        result = asyncio.run(handle_pull_request(payload, "test-delivery-id"))

        mock_popen.assert_not_called()

    @patch("riptide.webhook.subprocess.Popen")
    @patch("riptide.webhook.Path.exists", return_value=True)
    @patch("os.access", return_value=False)
    def test_deploy_script_not_executable_no_deploy(self, mock_access, mock_exists, mock_popen):
        """If deploy script exists but isn't executable, skip gracefully."""
        from riptide.webhook import handle_pull_request

        payload = {
            "action": "closed",
            "pull_request": {
                "number": 42,
                "merged": True,
                "base": {"ref": "main"},
            },
            "repository": {"full_name": "ChonSong/riptide"},
            "installation": {"id": 12345},
        }

        import asyncio
        result = asyncio.run(handle_pull_request(payload, "test-delivery-id"))

        mock_popen.assert_not_called()

    @patch("riptide.webhook.subprocess.Popen", side_effect=FileNotFoundError("systemd-run not found"))
    @patch("riptide.webhook.Path.exists", return_value=True)
    @patch("os.access", return_value=True)
    def test_systemd_run_not_found_no_crash(self, mock_access, mock_exists, mock_popen):
        """If systemd-run binary is missing, handle gracefully (no crash)."""
        from riptide.webhook import handle_pull_request

        payload = {
            "action": "closed",
            "pull_request": {
                "number": 42,
                "merged": True,
                "base": {"ref": "main"},
            },
            "repository": {"full_name": "ChonSong/riptide"},
            "installation": {"id": 12345},
        }

        import asyncio
        result = asyncio.run(handle_pull_request(payload, "test-delivery-id"))

        assert result.status_code == 200
