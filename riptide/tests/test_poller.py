"""Tests for riptide/poller.py — @riptide-bot fix polling loop + review discovery."""
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary metadata.db for poller tests."""
    db_path = tmp_path / "metadata.db"
    with patch("riptide.poller.DB_PATH", db_path), \
         patch("riptide.poller.DATA_DIR", tmp_path):
        from riptide import poller
        conn = sqlite3.connect(str(db_path))
        poller._init_db(conn)
        conn.close()
        yield db_path


@pytest.fixture
def poller_mod(tmp_db):
    """Import poller with patched DB path."""
    from riptide import poller
    return poller


# ── _is_processed / _mark_processed ─────────────────────────────────────────


class TestDedupRoundTrip:
    def test_not_processed_initially(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        assert poller_mod._is_processed(conn, 12345) is False
        conn.close()

    def test_mark_and_check(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        poller_mod._mark_processed(conn, 12345, '{"result":"spawned","pr_key":"o/r#1"}')
        assert poller_mod._is_processed(conn, 12345) is True
        conn.close()

    def test_different_comment_ids_independent(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        poller_mod._mark_processed(conn, 111, '{"result":"spawned","pr_key":"o/r#1"}')
        assert poller_mod._is_processed(conn, 111) is True
        assert poller_mod._is_processed(conn, 222) is False
        conn.close()


# ── _has_pending_fix ────────────────────────────────────────────────────────

class TestHasPendingFix:
    """_has_pending_fix queries the shared jobs table for recent fix sessions."""

    def test_no_job_returns_false(self, poller_mod):
        """No job in the jobs table → no pending fix."""
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        # Seed only processed_comments (old path) — should NOT trigger
        poller_mod._mark_processed(conn, 1, '{"result":"spawned","pr_key":"o/r#1"}')
        assert poller_mod._has_pending_fix(conn, "o/r#1") is False
        conn.close()

    def test_pending_job_blocks(self, poller_mod, tmp_path):
        """A pending fix job for this PR → block (one is running)."""
        import riptide.state as state_mod
        test_db = str(tmp_path / "test_state.db")
        store = state_mod.StateStore(db_path=test_db)
        store.create_job("riptide-fix-ChonSong-riptide-1-abc123", 1, "fix")
        with patch.object(state_mod, "StateStore", return_value=store):
            conn = sqlite3.connect(str(poller_mod.DB_PATH))
            assert poller_mod._has_pending_fix(conn, "ChonSong/riptide#1") is True
            conn.close()

    def test_recently_completed_job_blocks(self, poller_mod, tmp_path):
        """A completed job within the cooldown → block (just finished)."""
        import riptide.state as state_mod
        import time
        test_db = str(tmp_path / "test_state.db")
        store = state_mod.StateStore(db_path=test_db)
        store.create_job("riptide-fix-ChonSong-riptide-1-abc123", 1, "fix")
        store.mark_complete("riptide-fix-ChonSong-riptide-1-abc123")
        with patch.object(state_mod, "StateStore", return_value=store):
            conn = sqlite3.connect(str(poller_mod.DB_PATH))
            assert poller_mod._has_pending_fix(conn, "ChonSong/riptide#1") is True
            conn.close()

    def test_old_completed_job_allows(self, poller_mod, tmp_path):
        """A completed job outside the cooldown → allow (enough time passed)."""
        import riptide.state as state_mod
        import time
        test_db = str(tmp_path / "test_state.db")
        store = state_mod.StateStore(db_path=test_db)
        store.create_job("riptide-fix-ChonSong-riptide-1-abc123", 1, "fix")
        store.mark_complete("riptide-fix-ChonSong-riptide-1-abc123")
        # Set completed_at to 1 hour ago (outside 5-min cooldown)
        conn2 = store._get_conn()
        old_time = time.time() - 3600
        conn2.execute("UPDATE jobs SET completed_at = ? WHERE id LIKE '%riptide-fix%'", (old_time,))
        conn2.commit()
        with patch.object(state_mod, "StateStore", return_value=store):
            conn = sqlite3.connect(str(poller_mod.DB_PATH))
            assert poller_mod._has_pending_fix(conn, "ChonSong/riptide#1") is False
            conn.close()

    def test_failed_job_allows(self, poller_mod, tmp_path):
        """A failed job → allow (user can retry immediately)."""
        import riptide.state as state_mod
        import time
        test_db = str(tmp_path / "test_state.db")
        store = state_mod.StateStore(db_path=test_db)
        store.create_job("riptide-fix-ChonSong-riptide-1-abc123", 1, "fix")
        store.mark_failed("riptide-fix-ChonSong-riptide-1-abc123")
        with patch.object(state_mod, "StateStore", return_value=store):
            conn = sqlite3.connect(str(poller_mod.DB_PATH))
            assert poller_mod._has_pending_fix(conn, "ChonSong/riptide#1") is False
            conn.close()

    def test_different_pr_not_blocked(self, poller_mod, tmp_path):
        """A fix job for PR #1 should not block PR #2."""
        import riptide.state as state_mod
        test_db = str(tmp_path / "test_state.db")
        store = state_mod.StateStore(db_path=test_db)
        store.create_job("riptide-fix-ChonSong-riptide-1-abc123", 1, "fix")
        with patch.object(state_mod, "StateStore", return_value=store):
            conn = sqlite3.connect(str(poller_mod.DB_PATH))
            assert poller_mod._has_pending_fix(conn, "ChonSong/riptide#2") is False
            conn.close()

    def test_db_failure_returns_false(self, poller_mod):
        """If StateStore raises, fail open (return False)."""
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        with patch("riptide.state.StateStore", side_effect=Exception("DB down")):
            assert poller_mod._has_pending_fix(conn, "ChonSong/riptide#1") is False
        conn.close()


# ── _handle_fix ──────────────────────────────────────────────────────────────


class TestHandleFix:
    """Tests for _handle_fix dedup and execution."""

    def _match(self, comment_id=1, commenter="ChonSong", body="@riptide-bot fix",
               owner="ChonSong", repo="riptide", pr_number=1):
        return {
            "comment_id": comment_id,
            "commenter": commenter,
            "body": body,
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "pr_title": "Test",
            "created_at": "2026-08-01T00:00:00Z",
            "pr_key": f"{owner}/{repo}#{pr_number}",
        }

    def test_skips_already_processed(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        poller_mod._mark_processed(conn, 1, '{"result":"spawned","pr_key":"o/r#1"}')
        client = MagicMock()
        # Should return early, never call handle_fix_command
        with patch("riptide.fixer.handle_fix_command") as mock_handler:
            poller_mod._handle_fix(client, self._match(), conn)
            mock_handler.assert_not_called()
        conn.close()

    def test_skips_pr_with_recent_fix_job(self, poller_mod, tmp_path):
        """If a recent fix job exists for this PR, skip (cross-channel dedup)."""
        import riptide.state as state_mod
        test_db = str(tmp_path / "test_state.db")
        store = state_mod.StateStore(db_path=test_db)
        store.create_job("riptide-fix-ChonSong-riptide-1-abc123", 1, "fix")
        client = MagicMock()
        with patch.object(state_mod, "StateStore", return_value=store):
            with patch("riptide.fixer.handle_fix_command") as mock_handler:
                poller_mod._handle_fix(client, self._match(comment_id=1), conn=None)
                mock_handler.assert_not_called()

    def test_successful_spawn(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        client = MagicMock()
        success_msg = "🛠 **Riptide Fix triggered for #1!**"
        with patch("riptide.fixer.handle_fix_command", return_value=success_msg):
            poller_mod._handle_fix(client, self._match(), conn)
        # Verify comment was posted
        client.post_pr_comment.assert_called_once()
        conn.close()

    def test_error_result_posts_error(self, poller_mod):
        """Error result still posts the error comment."""
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        client = MagicMock()
        error_msg = "🚫 **Not authorized.** Only the PR author..."
        with patch("riptide.fixer.handle_fix_command", return_value=error_msg):
            poller_mod._handle_fix(client, self._match(), conn)
        client.post_pr_comment.assert_called_once()
        conn.close()

    def test_none_result_posts_nothing(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        client = MagicMock()
        with patch("riptide.fixer.handle_fix_command", return_value=None):
            poller_mod._handle_fix(client, self._match(), conn)
        client.post_pr_comment.assert_not_called()
        conn.close()

    def test_post_failure_does_not_block(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        client = MagicMock()
        client.post_pr_comment.side_effect = Exception("API down")
        success_msg = "🛠 **Riptide Fix triggered for #1!**"
        with patch("riptide.fixer.handle_fix_command", return_value=success_msg):
            poller_mod._handle_fix(client, self._match(), conn)
        # Should not raise; post failure handled gracefully
        conn.close()

    def _seed_pending(self, poller_mod, conn, comment_id, body):
        """Seed a comment with a pending_response (simulating a prior post failure)."""
        poller_mod._mark_processed(
            conn, comment_id,
            '{"result":"post-attempted","pr_key":"ChonSong/riptide#1"}',
            pending_response=body,
        )

    def test_retry_posts_and_clears_pending(self, poller_mod):
        """Retry path posts the pending response and clears the marker."""
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        body = "🛠 **Riptide Fix triggered for #1!**"
        self._seed_pending(poller_mod, conn, 50, body)
        assert poller_mod._get_pending_response(conn, 50) == body
        client = MagicMock()
        with patch("riptide.fixer.handle_fix_command") as mock_handler:
            poller_mod._handle_fix(client, self._match(comment_id=50), conn)
            mock_handler.assert_not_called()
        # Comment was posted exactly once
        client.post_pr_comment.assert_called_once()
        # Pending marker cleared
        assert poller_mod._get_pending_response(conn, 50) is None
        conn.close()

    def test_retry_post_failure_restores_pending(self, poller_mod):
        """If retry post fails, restore the pending marker."""
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        body = "🛠 **Riptide Fix triggered for #1!**"
        self._seed_pending(poller_mod, conn, 51, body)
        client = MagicMock()
        client.post_pr_comment.side_effect = Exception("API down")
        with patch("riptide.fixer.handle_fix_command") as mock_handler:
            poller_mod._handle_fix(client, self._match(comment_id=51), conn)
            mock_handler.assert_not_called()
        client.post_pr_comment.assert_called_once()
        # Pending marker restored for next retry
        assert poller_mod._get_pending_response(conn, 51) == body
        conn.close()

    def test_retry_split_brain_no_duplicate(self, poller_mod):
        """Split-brain: post succeeds but terminal DB write fails."""
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        body = "🛠 **Riptide Fix triggered for #1!**"
        self._seed_pending(poller_mod, conn, 52, body)
        client = MagicMock()
        real_mark = poller_mod._mark_processed
        call_count = {"n": 0}

        def flaky_mark(conn2, cid, result="", pending_response=""):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise RuntimeError("DB disk full")
            return real_mark(conn2, cid, result, pending_response)

        with patch("riptide.poller._mark_processed", side_effect=flaky_mark):
            with patch("riptide.fixer.handle_fix_command") as mock_handler:
                poller_mod._handle_fix(client, self._match(comment_id=52), conn)
                mock_handler.assert_not_called()
        client.post_pr_comment.assert_called_once()
        # Marker cleared pre-post -> no re-post next poll
        assert poller_mod._get_pending_response(conn, 52) is None
        assert poller_mod._is_processed(conn, 52) is True
        conn.close()


class TestPollerReviewDiscovery:
    """Tests for the poller's companion review discovery (external PR support)."""

    def test_discover_prs_no_repos(self, poller_mod):
        """No repos configured — returns empty."""
        with patch.object(poller_mod, "POLLER_REPOS", []):
            result = poller_mod._discover_prs()
            assert result == []

    def test_discover_prs_invalid_format(self, poller_mod, caplog):
        """Invalid repo format is skipped gracefully."""
        with patch.object(poller_mod, "POLLER_REPOS", ["no-slash"]):
            with patch("subprocess.run", side_effect=Exception("should not run")):
                result = poller_mod._discover_prs()
                assert result == []
                assert "Invalid repo" in caplog.text

    def test_discover_prs_filters_old(self, poller_mod):
        """PRs older than LOOKBACK_DAYS are excluded."""
        old_date = "2024-01-01T00:00:00Z"
        pr_data = json.dumps([{
            "number": 5, "title": "old", "author": {"login": "x"},
            "headRefName": "branch", "headRefOid": "abc",
            "createdAt": old_date, "updatedAt": old_date,
        }])
        with patch.object(poller_mod, "POLLER_REPOS", ["owner/repo"]):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=pr_data, stderr="")
                result = poller_mod._discover_prs()
                assert len(result) == 0

    def test_discover_prs_success(self, poller_mod):
        """Valid PRs are discovered with correct structure."""
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pr_data = json.dumps([{
            "number": 76, "title": "feat: test", "author": {"login": "ChonSong"},
            "headRefName": "feat/system-monitor", "headRefOid": "abc123def456",
            "createdAt": recent, "updatedAt": recent,
        }])
        with patch.object(poller_mod, "POLLER_REPOS", ["owner/repo"]):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=pr_data, stderr="")
                result = poller_mod._discover_prs()
                assert len(result) == 1
                pr = result[0]
                assert pr["owner"] == "owner"
                assert pr["repo"] == "repo"
                assert pr["pr_number"] == 76
                assert pr["head_sha"] == "abc123def456"
                assert pr["pr_key"] == "owner/repo#76"

    def test_is_reviewed_false_when_not_reviewed(self, poller_mod):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        assert poller_mod._is_reviewed(conn, "owner/repo#1", "abc") is False

    def test_is_reviewed_true_when_same_sha(self, poller_mod):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = ("abc",)
        assert poller_mod._is_reviewed(conn, "owner/repo#1", "abc") is True

    def test_is_reviewed_false_when_different_sha(self, poller_mod):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = ("old_sha",)
        assert poller_mod._is_reviewed(conn, "owner/repo#1", "new_sha") is False

    def test_handle_review_skips_if_already_reviewed(self, poller_mod, caplog):
        """_handle_review should skip PRs already reviewed at this SHA."""
        pr = {"pr_key": "owner/repo#1", "head_sha": "abc123", "title": "test",
              "owner": "owner", "repo": "repo", "pr_number": 1, "author": "x"}
        client = MagicMock()
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = ("abc123",)
        poller_mod._handle_review(client, pr, conn)
        assert "Already reviewed" in caplog.text
        # Companion should NOT be instantiated
        client.get_pr_files.assert_not_called()

    def test_handle_review_skips_if_no_files(self, poller_mod, caplog):
        """_handle_review should skip when no files are fetched."""
        pr = {"pr_key": "owner/repo#1", "head_sha": "abc123", "title": "test",
              "owner": "owner", "repo": "repo", "pr_number": 1, "author": "x"}
        client = MagicMock()
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None  # not reviewed
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
            poller_mod._handle_review(client, pr, conn)
        assert "No files fetched" in caplog.text

    def test_handle_review_runs_companion(self, poller_mod, caplog):
        """_handle_review should call companion.run_for_pr with GhCliClient."""
        pr = {"pr_key": "owner/repo#1", "head_sha": "abc123", "title": "test",
              "owner": "owner", "repo": "repo", "pr_number": 1, "author": "ChonSong"}
        client = MagicMock()
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None  # not reviewed
        files_data = json.dumps([{"filename": "test.js", "additions": 10}])
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=files_data, stderr="")
            with patch("riptide.companion.Companion") as MockCompanion:
                companion = MockCompanion.return_value
                companion.enable_deterministic = True
                companion.enable_graphify = False
                companion.run_for_pr = MagicMock()
                poller_mod._handle_review(client, pr, conn)
                MockCompanion.assert_called_once_with(github_client=None)
                companion.run_for_pr.assert_called_once_with(
                    installation_id=None,
                    owner="owner", repo="repo", pr_number=1,
                    title="test", author="ChonSong",
                    changed_files=[{"filename": "test.js", "additions": 10}],
                    client=client,
                )
        assert "Companion review triggered" in caplog.text


class TestSearchFixComments:
    def test_search_restricts_to_comments_field(self, poller_mod):
        """The gh search must pass --match comments."""
        with patch("riptide.poller.subprocess.run") as mock_run, \
             patch("riptide.poller._get_pr_comments", return_value=[]):
            mock_run.return_value = _search_subprocess_return([])
            poller_mod._search_fix_comments()
        cmd = mock_run.call_args.args[0]
        assert "--match" in cmd
        assert cmd[cmd.index("--match") + 1] == "comments"

    def test_search_uses_raised_limit(self, poller_mod):
        """SEARCH_LIMIT must be passed to gh."""
        assert poller_mod.SEARCH_LIMIT > 20
        with patch("riptide.poller.subprocess.run") as mock_run, \
             patch("riptide.poller._get_pr_comments", return_value=[]):
            mock_run.return_value = _search_subprocess_return([])
            poller_mod._search_fix_comments()
        cmd = mock_run.call_args.args[0]
        assert "--limit" in cmd
        assert cmd[cmd.index("--limit") + 1] == str(poller_mod.SEARCH_LIMIT)

    def test_search_trims_unused_json_fields(self, poller_mod):
        """Only request the JSON fields the poller actually uses."""
        with patch("riptide.poller.subprocess.run") as mock_run, \
             patch("riptide.poller._get_pr_comments", return_value=[]):
            mock_run.return_value = _search_subprocess_return([])
            poller_mod._search_fix_comments()
        cmd = mock_run.call_args.args[0]
        json_idx = cmd.index("--json")
        fields = cmd[json_idx + 1]
        assert "number" in fields
        assert "title" in fields
        assert "repository" in fields
        assert "createdAt" not in fields
        assert "body" not in fields
        assert "author" not in fields
        assert "commentsCount" not in fields

    def test_returns_matching_comment_details(self, poller_mod):
        """Only comments whose body matches FIX_RE are returned."""
        item = _fake_search_result(42)
        comments = [
            {"id": 9001, "user": {"login": "alice"}, "body": "@riptide-bot fix the poller", "created_at": "2026-08-02T00:00:00Z"},
            {"id": 9002, "user": {"login": "bob"}, "body": "unrelated comment", "created_at": "2026-08-02T00:00:00Z"},
        ]
        with patch("riptide.poller.subprocess.run") as mock_run, \
             patch("riptide.poller._get_pr_comments", return_value=comments) as mock_comments:
            mock_run.return_value = _search_subprocess_return([item])
            matches = poller_mod._search_fix_comments()

        mock_comments.assert_called_once_with("ChonSong", "riptide", 42)
        assert len(matches) == 1
        assert matches[0]["comment_id"] == 9001
        assert matches[0]["commenter"] == "alice"
        assert matches[0]["pr_key"] == "ChonSong/riptide#42"

    def test_search_failure_returns_empty(self, poller_mod):
        """A failing gh search must not raise."""
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        result.stderr = "rate limit exceeded"
        with patch("riptide.poller.subprocess.run", return_value=result), \
             patch("riptide.poller._get_pr_comments") as mock_comments:
            matches = poller_mod._search_fix_comments()
        assert matches == []
        mock_comments.assert_not_called()


def _fake_search_result(number=42, owner="ChonSong", repo="riptide"):
    """Create a fake GitHub search result item."""
    return {
        "number": number,
        "title": "PR",
        "repository": {"owner": {"login": owner}, "name": repo},
        "createdAt": "2026-08-01T00:00:00Z",
        "body": "",
        "author": {"login": owner},
        "commentsCount": 1,
    }


def _search_subprocess_return(items):
    """Wrap items as subprocess.run return value with JSON stdout."""
    result = MagicMock()
    result.returncode = 0
    result.stdout = json.dumps(items)
    result.stderr = ""
    return result
