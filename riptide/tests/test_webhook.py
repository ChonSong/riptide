"""Tests for riptide/webhook.py — webhook handler and auto-deploy."""
import asyncio
import json
import os
import shutil
from unittest.mock import patch, MagicMock

import pytest


def _make_payload(pr_number=42, merged=True, base_ref="main"):
    """Create a test payload with all required fields."""
    return {
        "action": "closed",
        "pull_request": {
            "number": pr_number,
            "merged": merged,
            "base": {"ref": base_ref},
        },
        "repository": {
            "full_name": "ChonSong/riptide",
            "default_branch": "main",
        },
        "installation": {"id": 12345},
    }


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
    @patch("shutil.which", return_value="/usr/bin/systemd-run")
    def test_merge_into_default_triggers_deploy(self, mock_which, mock_access, mock_exists, mock_popen):
        """Merging into default branch triggers exactly one systemd-run invocation."""
        from riptide.webhook import handle_pull_request

        result = asyncio.run(handle_pull_request(_make_payload(), "test-delivery-id"))

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
    @patch("shutil.which", return_value="/usr/bin/systemd-run")
    def test_merge_into_non_default_no_deploy(self, mock_which, mock_access, mock_exists, mock_popen):
        """Merging into a non-default branch does NOT trigger deploy."""
        from riptide.webhook import handle_pull_request

        result = asyncio.run(handle_pull_request(_make_payload(base_ref="feature/not-default"), "test-delivery-id"))

        mock_popen.assert_not_called()

    @patch("riptide.webhook.subprocess.Popen")
    @patch("riptide.webhook.Path.exists", return_value=False)
    @patch("shutil.which", return_value="/usr/bin/systemd-run")
    def test_deploy_script_not_found_no_deploy(self, mock_which, mock_exists, mock_popen):
        """If deploy script doesn't exist, skip gracefully."""
        from riptide.webhook import handle_pull_request

        result = asyncio.run(handle_pull_request(_make_payload(), "test-delivery-id"))

        mock_popen.assert_not_called()

    @patch("riptide.webhook.subprocess.Popen")
    @patch("riptide.webhook.Path.exists", return_value=True)
    @patch("os.access", return_value=False)
    @patch("shutil.which", return_value="/usr/bin/systemd-run")
    def test_deploy_script_not_executable_no_deploy(self, mock_which, mock_access, mock_exists, mock_popen):
        """If deploy script exists but isn't executable, skip gracefully."""
        from riptide.webhook import handle_pull_request

        result = asyncio.run(handle_pull_request(_make_payload(), "test-delivery-id"))

        mock_popen.assert_not_called()

    @patch("riptide.webhook.subprocess.Popen", side_effect=FileNotFoundError("systemd-run not found"))
    @patch("riptide.webhook.Path.exists", return_value=True)
    @patch("os.access", return_value=True)
    @patch("shutil.which", return_value="/usr/bin/systemd-run")
    def test_systemd_run_not_found_no_crash(self, mock_which, mock_access, mock_exists, mock_popen):
        """If systemd-run binary is missing, handle gracefully (no crash)."""
        from riptide.webhook import handle_pull_request

        result = asyncio.run(handle_pull_request(_make_payload(), "test-delivery-id"))

        assert result.status_code == 200


class TestAutoDeployConcurrency:
    """Verify the dedup mechanism that prevents duplicate deploys."""

    @patch("riptide.webhook.subprocess.Popen")
    @patch("riptide.webhook.Path.exists", return_value=True)
    @patch("os.access", return_value=True)
    @patch("shutil.which", return_value="/usr/bin/systemd-run")
    def test_different_prs_both_deploy(self, mock_which, mock_access, mock_exists, mock_popen):
        """Two different PRs with different delivery_ids should both trigger deploy."""
        from riptide.webhook import handle_pull_request

        payload1 = _make_payload(pr_number=42)
        payload2 = _make_payload(pr_number=43)

        async def run_both():
            results = await asyncio.gather(
                handle_pull_request(payload1, "delivery-1"),
                handle_pull_request(payload2, "delivery-2"),
            )
            return results

        results = asyncio.run(run_both())
        assert results[0].status_code == 200
        assert results[1].status_code == 200
        # Both should deploy since they're different PRs with different delivery_ids
        assert mock_popen.call_count == 2, f"Expected 2 calls, got {mock_popen.call_count}"

    @patch("riptide.webhook.subprocess.Popen")
    @patch("riptide.webhook.Path.exists", return_value=True)
    @patch("os.access", return_value=True)
    @patch("shutil.which", return_value="/usr/bin/systemd-run")
    def test_single_pr_single_deploy(self, mock_which, mock_access, mock_exists, mock_popen):
        """Single PR merge triggers exactly one deploy (regression test)."""
        from riptide.webhook import handle_pull_request

        result = asyncio.run(handle_pull_request(_make_payload(), "delivery-1"))
        assert result.status_code == 200
        assert mock_popen.call_count == 1, f"Expected 1 call, got {mock_popen.call_count}"


class TestDefaultBranch:
    """Verify repo.default_branch is used as authoritative source."""

    @patch("riptide.webhook.subprocess.Popen")
    @patch("riptide.webhook.Path.exists", return_value=True)
    @patch("os.access", return_value=True)
    @patch("shutil.which", return_value="/usr/bin/systemd-run")
    def test_deploy_when_base_matches_repo_default_branch(self, mock_which, mock_access, mock_exists, mock_popen):
        """Deploy triggers when base.ref matches repo.default_branch."""
        from riptide.webhook import handle_pull_request

        payload = _make_payload(base_ref="develop")
        payload["repository"]["default_branch"] = "develop"

        result = asyncio.run(handle_pull_request(payload, "test-delivery-id"))

        assert mock_popen.call_count == 1

    @patch("riptide.webhook.subprocess.Popen")
    @patch("riptide.webhook.Path.exists", return_value=True)
    @patch("os.access", return_value=True)
    @patch("shutil.which", return_value="/usr/bin/systemd-run")
    def test_no_deploy_when_base_differs_from_repo_default_branch(self, mock_which, mock_access, mock_exists, mock_popen):
        """No deploy when base.ref differs from repo.default_branch."""
        from riptide.webhook import handle_pull_request

        payload = _make_payload(base_ref="feature/x")
        payload["repository"]["default_branch"] = "main"

        result = asyncio.run(handle_pull_request(payload, "test-delivery-id"))

        mock_popen.assert_not_called()
