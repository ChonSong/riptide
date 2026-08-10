"""Tests for riptide/poller.py — @riptide-bot fix polling loop."""
import os
import sqlite3
import tempfile
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


# ── _search_fix_comments ────────────────────────────────────────────────────


def _fake_search_result(number, owner="ChonSong", repo="riptide", title="PR"):
    """Build one gh search prs JSON item."""
    return {
        "number": number,
        "title": title,
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
    result.stdout = __import__("json").dumps(items)
    result.stderr = ""
    return result


class TestSearchFixComments:
    def test_search_restricts_to_comments_field(self, poller_mod):
        """The gh search must pass --match comments so PRs whose *body*
        contains the phrase (but no comment does) are not matched."""
        with patch("riptide.poller.subprocess.run") as mock_run, \
             patch("riptide.poller._get_pr_comments", return_value=[]):
            mock_run.return_value = _search_subprocess_return([])
            poller_mod._search_fix_comments()
        cmd = mock_run.call_args.args[0]
        assert "--match" in cmd
        assert cmd[cmd.index("--match") + 1] == "comments"

    def test_search_uses_raised_limit(self, poller_mod):
        """SEARCH_LIMIT must be passed to gh so results are not capped at
        the old hard-coded 20 (gh auto-paginates internally up to --limit)."""
        assert poller_mod.SEARCH_LIMIT > 20
        with patch("riptide.poller.subprocess.run") as mock_run, \
             patch("riptide.poller._get_pr_comments", return_value=[]):
            mock_run.return_value = _search_subprocess_return([])
            poller_mod._search_fix_comments()
        cmd = mock_run.call_args.args[0]
        assert "--limit" in cmd
        assert cmd[cmd.index("--limit") + 1] == str(poller_mod.SEARCH_LIMIT)

    def test_returns_matching_comment_details(self, poller_mod):
        """Only comments whose body matches FIX_RE are returned, with the
        comment ID and PR metadata used downstream by _handle_fix."""
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
        assert matches[0]["pr_title"] == "PR"

    def test_search_failure_returns_empty(self, poller_mod):
        """A failing gh search must not raise; the poll cycle just skips."""
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        result.stderr = "rate limit exceeded"
        with patch("riptide.poller.subprocess.run", return_value=result), \
             patch("riptide.poller._get_pr_comments") as mock_comments:
            matches = poller_mod._search_fix_comments()
        assert matches == []
        mock_comments.assert_not_called()


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
    def test_no_spawned_returns_false(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        poller_mod._mark_processed(conn, 1, '{"result":"not-spawned","pr_key":"o/r#1"}')
        assert poller_mod._has_pending_fix(conn, "o/r#1") is False
        conn.close()

    def test_spawned_returns_true(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        poller_mod._mark_processed(conn, 1, '{"result":"spawned","pr_key":"o/r#1"}')
        assert poller_mod._has_pending_fix(conn, "o/r#1") is True
        conn.close()

    def test_spawned_does_not_block_different_pr(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        poller_mod._mark_processed(conn, 1, '{"result":"spawned","pr_key":"o/r#1"}')
        assert poller_mod._has_pending_fix(conn, "o/r#2") is False
        conn.close()

    def test_not_spawned_does_not_block_same_pr(self, poller_mod):
        """Lockout regression: error result must NOT block future retries."""
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        poller_mod._mark_processed(conn, 1, '{"result":"not-spawned","pr_key":"o/r#1"}')
        assert poller_mod._has_pending_fix(conn, "o/r#1") is False
        conn.close()

    def test_post_failed_does_not_block(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        poller_mod._mark_processed(conn, 1, "post-failed: boom")
        assert poller_mod._has_pending_fix(conn, "o/r#1") is False
        conn.close()


# ── _handle_fix ──────────────────────────────────────────────────────────────


class TestHandleFix:
    @pytest.fixture(autouse=True)
    def no_webhook_pending(self):
        """Patch StateStore so _handle_fix sees no webhook-claimed pending job by default.

        _handle_fix now consults StateStore().has_pending_job() to stay silent when
        the GitHub App webhook already claimed the fix job (installed repos). A
        bare MagicMock would be truthy and incorrectly short-circuit every test, so
        default it to False and let specific tests override.
        """
        with patch("riptide.orchestrator.StateStore") as mock_store:
            mock_store.return_value.has_pending_job.return_value = False
            yield mock_store

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

    def test_skips_pr_already_pending(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        # Mark a different comment on same PR as spawned
        poller_mod._mark_processed(conn, 99, '{"result":"spawned","pr_key":"ChonSong/riptide#1"}')
        client = MagicMock()
        with patch("riptide.fixer.handle_fix_command") as mock_handler:
            poller_mod._handle_fix(client, self._match(comment_id=100), conn)
            mock_handler.assert_not_called()
        conn.close()

    def test_stays_silent_when_webhook_claimed_fix(self, poller_mod, no_webhook_pending):
        """Cross-channel dedup: if StateStore has a pending fix job for this PR
        (the GitHub App webhook already claimed it on an installed repo), the
        poller must NOT call handle_fix_command and must NOT post a duplicate
        "Could not schedule" comment."""
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        no_webhook_pending.return_value.has_pending_job.return_value = True
        client = MagicMock()
        with patch("riptide.fixer.handle_fix_command") as mock_handler:
            poller_mod._handle_fix(client, self._match(comment_id=100), conn)
            mock_handler.assert_not_called()
        # No duplicate comment posted
        client.post_pr_comment.assert_not_called()
        # Marked processed so the poller doesn't re-hit the same comment
        assert poller_mod._is_processed(conn, 100) is True
        conn.close()

    def test_webhook_pending_check_uses_fix_prefix(self, poller_mod, no_webhook_pending):
        """The StateStore prefix must match fixer._spawn_fix's reservation prefix
        (riptide-fix-{owner}-{repo}-{pr_number}) so webhook-claimed jobs are found."""
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        client = MagicMock()
        no_webhook_pending.return_value.has_pending_job.return_value = False
        success_msg = "🛠 **Riptide Fix triggered for #1!**"
        with patch("riptide.fixer.handle_fix_command", return_value=success_msg):
            poller_mod._handle_fix(client, self._match(), conn)
        no_webhook_pending.return_value.has_pending_job.assert_called_once_with(
            "riptide-fix-ChonSong-riptide-1"
        )
        conn.close()

    def test_successful_spawn_marks_spawned(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        client = MagicMock()
        success_msg = "🛠 **Riptide Fix triggered for #1!**"
        with patch("riptide.fixer.handle_fix_command", return_value=success_msg):
            poller_mod._handle_fix(client, self._match(), conn)
        # Verify marked as spawned
        assert poller_mod._has_pending_fix(conn, "ChonSong/riptide#1") is True
        # Verify comment was posted
        client.post_pr_comment.assert_called_once()
        conn.close()

    def test_error_result_marks_not_spawned(self, poller_mod):
        """Lockout regression: error result must NOT mark as spawned."""
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        client = MagicMock()
        error_msg = "🚫 **Not authorized.** Only the PR author..."
        with patch("riptide.fixer.handle_fix_command", return_value=error_msg):
            poller_mod._handle_fix(client, self._match(), conn)
        # Verify NOT marked as spawned — future retries allowed
        assert poller_mod._has_pending_fix(conn, "ChonSong/riptide#1") is False
        # Verify comment was still posted (the error message)
        client.post_pr_comment.assert_called_once()
        conn.close()

    def test_none_result_marks_no_result(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        client = MagicMock()
        with patch("riptide.fixer.handle_fix_command", return_value=None):
            poller_mod._handle_fix(client, self._match(), conn)
        assert poller_mod._has_pending_fix(conn, "ChonSong/riptide#1") is False
        client.post_pr_comment.assert_not_called()
        conn.close()

    def test_post_failure_does_not_block(self, poller_mod):
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        client = MagicMock()
        client.post_pr_comment.side_effect = Exception("API down")
        success_msg = "🛠 **Riptide Fix triggered for #1!**"
        with patch("riptide.fixer.handle_fix_command", return_value=success_msg):
            poller_mod._handle_fix(client, self._match(), conn)
        # post-failed is not "spawned" — future retries allowed
        assert poller_mod._has_pending_fix(conn, "ChonSong/riptide#1") is False
        conn.close()
