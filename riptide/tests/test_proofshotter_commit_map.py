"""Tests for riptide/proofshotter.py — per-commit file mapping and fail-open fix.

Covers the N+1 per-commit `gh api` loop:
- success path: commits parsed, files fetched, ui_files computed
- transient failure: retry succeeds
- persistent failure: commit flagged `error` instead of silently "no UI files"
"""

import json
from unittest.mock import patch, MagicMock

from riptide.proofshotter import _get_commit_file_map


def _gh_result(returncode=0, stdout=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = "boom" if returncode else ""
    return r


def _commits_stdout():
    commits = [
        {"sha": "a" * 40, "message": "feat: first"},
        {"sha": "b" * 40, "message": "fix: second"},
        {"sha": "c" * 40, "message": "chore: third"},
    ]
    return "\n".join(json.dumps(c) for c in commits)


class TestGetCommitFileMap:
    def test_parses_commits_and_computes_ui_files(self):
        """Success path: files fetched, ui_files computed, no error flags."""
        with patch("riptide.proofshotter.subprocess.run") as mock_run:
            # Call 1: commit list; Calls 2-4: per-commit file lists
            mock_run.side_effect = [
                _gh_result(stdout=_commits_stdout()),
                _gh_result(stdout="page.html\napp.jsx\n"),
                _gh_result(stdout="server.py\n"),
                _gh_result(stdout="README.md\n"),
            ]
            result = _get_commit_file_map("ChonSong", "riptide", 46)

        assert len(result) == 3
        assert result[0]["sha"] == "a" * 40
        assert result[0]["ui_files"] == ["page.html", "app.jsx"]
        assert result[1]["ui_files"] == []  # server.py is not UI
        assert result[2]["ui_files"] == []
        assert all(c["error"] is None for c in result)
        assert mock_run.call_count == 4  # 1 commits + 3 per-commit

    def test_transient_failure_retries_and_succeeds(self):
        """A transient gh api failure should be retried, not marked error."""
        with (
            patch("riptide.proofshotter.subprocess.run") as mock_run,
            patch("riptide.proofshotter.time.sleep") as mock_sleep,
        ):
            mock_run.side_effect = [
                _gh_result(stdout=_commits_stdout()),
                _gh_result(returncode=1),  # commit a fails first
                _gh_result(stdout="page.html\n"),  # retry succeeds
                _gh_result(stdout="server.py\n"),
                _gh_result(stdout="README.md\n"),
            ]
            result = _get_commit_file_map("ChonSong", "riptide", 46)

        assert result[0]["error"] is None
        assert result[0]["ui_files"] == ["page.html"]
        mock_sleep.assert_called_once_with(1)

    def test_persistent_failure_flags_error_not_no_ui(self):
        """Persistent per-commit failure must flag error, not silently drop to no-UI."""
        with (
            patch("riptide.proofshotter.subprocess.run") as mock_run,
            patch("riptide.proofshotter.time.sleep"),
        ):
            # Call 1: commit list; commit a fails BOTH attempts (2 calls);
            # commit b succeeds; commit c succeeds.
            mock_run.side_effect = [
                _gh_result(stdout=_commits_stdout()),
                _gh_result(returncode=1),
                _gh_result(returncode=1),
                _gh_result(stdout="server.py\n"),
                _gh_result(stdout="README.md\n"),
            ]
            result = _get_commit_file_map("ChonSong", "riptide", 46)

        # The failed commit is NOT silently treated as "no UI files"
        assert result[0]["error"] == "boom"
        assert result[0]["ui_files"] == []
        assert result[1]["error"] is None
        assert result[2]["error"] is None

    def test_commits_list_failure_returns_none(self):
        """Commits-list failure returns None (caller routes to skipped_error)."""
        with patch("riptide.proofshotter.subprocess.run") as mock_run:
            mock_run.return_value = _gh_result(returncode=1)
            result = _get_commit_file_map("ChonSong", "riptide", 46)
        assert result is None
