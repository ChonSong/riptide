"""Tests for riptide/poller.py — @riptide-bot fix polling loop + review discovery."""
import json
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
        with patch("riptide.state.StateStore") as mock_store:
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

    def _seed_pending(self, poller_mod, conn, comment_id, body):
        """Seed a comment with a pending_response (simulating a prior post failure)."""
        poller_mod._mark_processed(
            conn, comment_id,
            '{"result":"post-attempted","pr_key":"ChonSong/riptide#1"}',
            pending_response=body,
        )

    def test_retry_posts_and_clears_pending(self, poller_mod):
        """Retry path posts the pending response and clears the marker so the
        next poll does NOT re-post it (idempotency)."""
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
        # Pending marker cleared -> next poll will not re-post a duplicate
        assert poller_mod._get_pending_response(conn, 50) is None
        conn.close()

    def test_retry_post_failure_restores_pending(self, poller_mod):
        """If the retry post fails, restore the pending marker so the next poll retries."""
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        body = "🛠 **Riptide Fix triggered for #1!**"
        self._seed_pending(poller_mod, conn, 51, body)
        client = MagicMock()
        client.post_pr_comment.side_effect = Exception("API down")
        with patch("riptide.fixer.handle_fix_command") as mock_handler:
            poller_mod._handle_fix(client, self._match(comment_id=51), conn)
            mock_handler.assert_not_called()
        client.post_pr_comment.assert_called_once()
        # Pending marker restored -> next poll retries instead of dropping the reply
        assert poller_mod._get_pending_response(conn, 51) == body
        conn.close()

    def test_retry_split_brain_no_duplicate(self, poller_mod):
        """Split-brain: post succeeds but the terminal DB write fails. The pending
        marker was already cleared BEFORE posting, so the next poll cannot re-post
        a duplicate comment (at-least-once delivery safe)."""
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        body = "🛠 **Riptide Fix triggered for #1!**"
        self._seed_pending(poller_mod, conn, 52, body)
        client = MagicMock()
        real_mark = poller_mod._mark_processed
        call_count = {"n": 0}

        def flaky_mark(conn2, cid, result="", pending_response=""):
            call_count["n"] += 1
            if call_count["n"] >= 2:  # the terminal status write fails
                raise RuntimeError("DB disk full")
            return real_mark(conn2, cid, result, pending_response)

        with patch("riptide.poller._mark_processed", side_effect=flaky_mark):
            with patch("riptide.fixer.handle_fix_command") as mock_handler:
                poller_mod._handle_fix(client, self._match(comment_id=52), conn)
                mock_handler.assert_not_called()
        # The comment WAS posted (post succeeded)
        client.post_pr_comment.assert_called_once()
        # Marker cleared pre-post -> no re-post next poll
        assert poller_mod._get_pending_response(conn, 52) is None
        # Comment still recorded as processed (post-attempted row) -> poller skips it
        assert poller_mod._is_processed(conn, 52) is True
        conn.close()

    def test_dedup_check_failure_fails_closed(self, poller_mod, no_webhook_pending):
        """If the StateStore cross-channel dedup check raises, fail closed: mark
        dedup-check-failed and skip — NEVER post a redundant 'Could not schedule'
        comment on top of the webhook's confirmation."""
        conn = sqlite3.connect(str(poller_mod.DB_PATH))
        no_webhook_pending.return_value.has_pending_job.side_effect = Exception("StateStore down")
        client = MagicMock()
        with patch("riptide.fixer.handle_fix_command") as mock_handler:
            poller_mod._handle_fix(client, self._match(comment_id=200), conn)
            mock_handler.assert_not_called()
        # No comment posted
        client.post_pr_comment.assert_not_called()
        # Marked processed with dedup-check-failed so the poller won't re-hit it
        row = conn.execute(
            f"SELECT result FROM {poller_mod.PROCESSED_TABLE} WHERE comment_id = 200"
        ).fetchone()
        assert row is not None and "dedup-check-failed" in row[0]
        assert poller_mod._is_processed(conn, 200) is True
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
        pr_data = json.dumps([{
            "number": 76, "title": "feat: test", "author": {"login": "ChonSong"},
            "headRefName": "feat/system-monitor", "headRefOid": "abc123def456",
            "createdAt": "2026-08-07T07:00:00Z", "updatedAt": "2026-08-07T08:00:00Z",
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
