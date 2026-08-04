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

    def test_post_failure_persists_pending_response_and_retries(self, poller_mod):
        """When post_pr_comment fails, persist the response as pending and retry
        on subsequent polls without calling handle_fix_command again."""
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        client = MagicMock()
        success_msg = "🛠 **Riptide Fix triggered for #1!**"

        # First attempt: handle_fix_command succeeds, but post fails
        client.post_pr_comment.side_effect = Exception("API down")
        with patch("riptide.fixer.handle_fix_command", return_value=success_msg) as mock_handler:
            poller_mod._handle_fix(client, self._match(comment_id=1), conn)
            # handle_fix_command was called once
            assert mock_handler.call_count == 1

        # Verify pending response is stored
        pending = poller_mod._get_pending_response(conn, 1)
        assert pending == success_msg
        # Not marked as spawned yet
        assert poller_mod._has_pending_fix(conn, "ChonSong/riptide#1") is False

        # Second attempt: retry posting (API recovered)
        client.post_pr_comment.side_effect = None
        client.post_pr_comment.return_value = {"id": 999}
        # The first attempt's failed call still counts on the mock; reset so
        # assert_called_once below only sees the retry's successful post.
        client.post_pr_comment.reset_mock()
        with patch("riptide.fixer.handle_fix_command", return_value=success_msg) as mock_handler:
            poller_mod._handle_fix(client, self._match(comment_id=1), conn)
            # handle_fix_command was NOT called again (pending response path)
            mock_handler.assert_not_called()

        # Verify response was posted
        client.post_pr_comment.assert_called_once()
        # Now marked as spawned
        assert poller_mod._has_pending_fix(conn, "ChonSong/riptide#1") is True
        # Pending response cleared
        assert poller_mod._get_pending_response(conn, 1) is None
        conn.close()


# ── _search_fix_comments ─────────────────────────────────────────────────────


class TestSearchFixComments:
    def test_ignores_stale_command_on_recently_updated_pr(self, poller_mod):
        """Regression: old command comments should be ignored even if the PR was
        recently updated, by comparing comment updated_at/created_at against cutoff."""
        from datetime import datetime, timezone

        # Simulate a PR updated yesterday
        recent_pr = {
            "repository_url": "https://api.github.com/repos/o/r",
            "number": 1,
            "title": "Test PR",
            "updated_at": "2026-08-03T00:00:00Z",
        }

        # Old comment from 10 days ago (outside LOOKBACK_DAYS default of 3)
        old_comment = {
            "id": 100,
            "user": {"login": "alice"},
            "body": "@riptide-bot fix the bug",
            "created_at": "2026-07-25T00:00:00Z",
            "updated_at": "2026-07-25T00:00:00Z",
        }

        # Recent comment from today
        recent_comment = {
            "id": 200,
            "user": {"login": "bob"},
            "body": "@riptide-bot fix the other bug",
            "created_at": "2026-08-04T00:00:00Z",
            "updated_at": "2026-08-04T00:00:00Z",
        }

        with patch("riptide.poller.requests") as mock_requests:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"items": [recent_pr]}
            mock_requests.get.return_value = mock_response

            with patch("riptide.poller._get_pr_comments", return_value=[old_comment, recent_comment]):
                with patch("riptide.poller._get_gh_token", return_value="fake-token"):
                    matches = poller_mod._search_fix_comments(lookback_days=3)

        # Only the recent comment should be included
        assert len(matches) == 1
        assert matches[0]["comment_id"] == 200
        assert matches[0]["commenter"] == "bob"
