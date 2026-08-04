"""Tests for riptide/gh_cli_client.py — `gh` CLI GitHub API client."""
import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from riptide.gh_cli_client import GhCliClient, make_gh_cli_client


# ── Factory ──────────────────────────────────────────────────────────────────


class TestFactory:
    def test_returns_none_when_gh_missing(self):
        with patch("shutil.which", return_value=None):
            client = make_gh_cli_client()
            assert client is None

    def test_returns_client_when_gh_available(self):
        with patch("shutil.which", return_value="/usr/bin/gh"):
            client = make_gh_cli_client()
            assert isinstance(client, GhCliClient)


# ── _gh_api ──────────────────────────────────────────────────────────────────


class TestGhApi:
    def _mock_run(self, stdout, returncode=0, stderr=""):
        m = MagicMock()
        m.stdout = stdout
        m.stderr = stderr
        m.returncode = returncode
        return m

    def test_get_returns_json(self):
        client = GhCliClient()
        payload = {"number": 42, "title": "test pr"}
        with patch("shutil.which", return_value="/usr/bin/gh"):
            with patch("subprocess.run", return_value=self._mock_run(json.dumps(payload))):
                result = client._gh_api("repos/o/r/pulls/42")
                assert result["number"] == 42
                assert result["title"] == "test pr"

    def test_empty_response_returns_empty_dict(self):
        client = GhCliClient()
        with patch("shutil.which", return_value="/usr/bin/gh"):
            with patch("subprocess.run", return_value=self._mock_run("", returncode=0)):
                result = client._gh_api("repos/o/r/check-runs")
                assert result == {}

    def test_nonzero_exit_raises(self):
        client = GhCliClient()
        with patch("shutil.which", return_value="/usr/bin/gh"):
            with patch("subprocess.run", return_value=self._mock_run("", returncode=1, stderr="Not found")):
                with pytest.raises(RuntimeError, match="gh api failed"):
                    client._gh_api("repos/o/r/pulls/9999")

    def test_timeout_raises(self):
        client = GhCliClient()
        with patch("shutil.which", return_value="/usr/bin/gh"):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30)):
                with pytest.raises(RuntimeError, match="timeout"):
                    client._gh_api("repos/o/r/pulls/1")

    def test_gh_not_available_raises(self):
        client = GhCliClient()
        with patch.object(client, "_check_gh", return_value=False):
            with pytest.raises(RuntimeError, match="gh CLI not available"):
                client._gh_api("repos/o/r/pulls/1")


# ── PR operations ────────────────────────────────────────────────────────────


class TestPrOperations:
    def test_get_pr_details(self):
        client = GhCliClient()
        pr = {"number": 1, "title": "feat: thing", "user": {"login": "alice"}, "additions": 10, "deletions": 5}
        with patch.object(client, "_gh_api", return_value=pr) as m:
            result = client.get_pr_details(None, "ChonSong", "riptide", 1)
            m.assert_called_once_with("repos/ChonSong/riptide/pulls/1")
            assert result["user"]["login"] == "alice"

    def test_get_pr_files_paginates(self):
        client = GhCliClient()
        page1 = [{"filename": "a.py"}] * 100
        page2 = [{"filename": "b.py"}]
        with patch.object(client, "_gh_api", side_effect=[page1, page2]) as m:
            files = client.get_pr_files(None, "o", "r", 1)
            assert len(files) == 101
            assert m.call_count == 2
            # Second call includes page=2 as a param
            second_call_params = m.call_args_list[1][1].get("params", {})
            assert second_call_params.get("page") == "2"

    def test_post_pr_comment(self):
        client = GhCliClient()
        with patch.object(client, "_gh_api", return_value={"id": 99}) as m:
            client.post_pr_comment(None, "o", "r", 1, "Looks good!")
            m.assert_called_once_with(
                "repos/o/r/issues/1/comments",
                method="POST",
                body={"body": "Looks good!"},
            )

    def test_post_inline_comment(self):
        client = GhCliClient()
        with patch.object(client, "_gh_api", return_value={"id": 100}) as m:
            client.post_inline_comment(None, "o", "r", 1, "inline", "deadbeef", "file.py", 42)
            m.assert_called_once_with(
                "repos/o/r/pulls/1/comments",
                method="POST",
                body={
                    "body": "inline",
                    "commit_id": "deadbeef",
                    "path": "file.py",
                    "line": 42,
                    "side": "RIGHT",
                },
            )

    def test_compare_commits(self):
        client = GhCliClient()
        raw = {"files": [{"a": 1}], "commits": [{"sha": "x"}], "total_commits": 1, "ahead_by": 1}
        with patch.object(client, "_gh_api", return_value=raw) as m:
            result = client.compare_commits(None, "o", "r", "main", "feature")
            m.assert_called_once_with("repos/o/r/compare/main...feature")
            assert result["total_commits"] == 1
