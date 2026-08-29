"""Tests for riptide/webhook.py — webhook handler and auto-deploy."""
import asyncio
import json
import os
import shutil
from unittest.mock import patch, MagicMock

import pytest


class FakeGhCliClient:
    """Fake gh CLI client that validates method signatures match GhCliClient."""

    def __init__(self):
        self.posted = []

    def post_pr_comment(self, installation_id, owner, repo, pr_number, body):
        self.posted.append(
            {
                "installation_id": installation_id,
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
                "body": body,
            }
        )
        return {"id": 1, "html_url": "https://example.com"}

    def post_inline_comment(
        self, installation_id, owner, repo, pr_number, body, commit_id, path, line, side="RIGHT"
    ):
        return {"id": 2}

    def get_installation_repos(self, installation_id):
        return []


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


class TestRoute2GhCliFallback:
    """Tests for Route 2 @riptide-bot command gh CLI fallback."""

    def _make_issue_comment_payload(self, body, installation_id=None):
        """Create a test issue_comment payload."""
        return {
            "action": "created",
            "comment": {
                "id": 123,
                "body": body,
                "user": {"login": "testuser", "type": "User"},
            },
            "issue": {
                "number": 185,
                "pull_request": {"href": "https://api.github.com/repos/ChonSong/riptide/pulls/185"},
            },
            "repository": {"full_name": "ChonSong/riptide", "name": "riptide"},
            "installation": {"id": installation_id} if installation_id else {},
            "sender": {"login": "testuser"},
        }

    @patch("riptide.interaction_handler.handle_command")
    @patch("riptide.webhook.get_gh_cli_client")
    def test_route2_gh_cli_fallback(self, mock_gh_cli, mock_handle):
        """Route 2 falls back to gh CLI when installation_id is None."""
        from riptide.webhook import handle_issue_comment

        mock_gh_cli.return_value = FakeGhCliClient()
        mock_handle.return_value = "🧠 Review triggered"

        payload = self._make_issue_comment_payload("@riptide-bot review")
        result = asyncio.run(handle_issue_comment(payload, "test-delivery-id"))

        mock_handle.assert_called_once()
        assert result.status_code == 200
        assert len(mock_gh_cli.return_value.posted) == 1
        assert mock_gh_cli.return_value.posted[0]["body"] == "🧠 Review triggered"

    @patch("riptide.interaction_handler.handle_command")
    def test_route2_skipped_no_installation_no_gh_cli(self, mock_handle):
        """Route 2 skipped when no installation and gh CLI unavailable."""
        from riptide.webhook import handle_issue_comment

        with patch("riptide.webhook.get_gh_cli_client", return_value=None):
            payload = self._make_issue_comment_payload("@riptide-bot review")
            result = asyncio.run(handle_issue_comment(payload, "test-delivery-id"))

        mock_handle.assert_not_called()
        assert result.status_code == 200

    @patch("riptide.webhook.get_gh_cli_client")
    @patch("riptide.webhook.github_client")
    def test_route1_app_api_failure_falls_back_to_gh_cli(self, mock_gh_api, mock_gh_cli):
        """Route 1 companion reply falls back to gh CLI when App API fails."""
        from riptide.webhook import handle_issue_comment

        fake_cli = FakeGhCliClient()
        mock_gh_cli.return_value = fake_cli
        mock_gh_api.return_value.post_pr_comment.side_effect = RuntimeError("App API 500")

        companion_mock = MagicMock()
        companion_mock.handle_comment.return_value = "🤖 Companion reply"
        with patch("riptide.webhook.get_companion", return_value=companion_mock):
            payload = self._make_issue_comment_payload(
                "@riptide-bot skip", installation_id=12345
            )
            result = asyncio.run(handle_issue_comment(payload, "test-delivery-id"))

        assert result.status_code == 200
        mock_gh_api.return_value.post_pr_comment.assert_called_once()
        assert len(fake_cli.posted) == 1
        assert fake_cli.posted[0]["body"] == "🤖 Companion reply"
