#!/usr/bin/env python3
"""
Tests for work_queue recovery and total_changes bugfix.

Covers the fixes in commit a4e9e7f:
- recover_pending_work() uses SELECT changes() instead of conn.total_changes
- recover_pending_work() marks old items as stale
- complete_work() accepts status IN ('pending', 'recovering')
- init_db() calls recover_pending_work() on startup
"""

import os
import sqlite3
import tempfile
import time

import pytest

from riptide.state import StateStore


class TestRecoverPendingWork:
    """Tests for recover_pending_work() atomic claim mechanism."""

    def test_uses_changes_not_total_changes(self):
        """recover_pending_work() must use SELECT changes() to detect claim success."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = StateStore(db_path)

            # Enqueue a recent item with different PID (simulate dead process)
            store.enqueue_work("work-1", "review", {"pr_number": 1})
            conn = store._get_conn()
            conn.execute("UPDATE work_queue SET pid = 999999 WHERE id = 'work-1'")
            conn.commit()

            # First recovery should claim it
            claimed = store.recover_pending_work()
            assert len(claimed) == 1
            assert claimed[0]["id"] == "work-1"

            # Second recovery should NOT re-claim (already 'recovering')
            claimed2 = store.recover_pending_work()
            assert len(claimed2) == 0  # No new claims

    def test_marks_old_items_as_stale(self):
        """Items older than 5 minutes are marked failed with error='startup_recovery'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = StateStore(db_path)

            # Insert an old item manually with different PID
            store.enqueue_work("old-work", "review", {"pr_number": 1})
            conn = store._get_conn()
            conn.execute("UPDATE work_queue SET created_at = ?, pid = 999999 WHERE id = 'old-work'",
                         (time.time() - 600,))
            conn.commit()

            # Recover should mark it as stale
            claimed = store.recover_pending_work()
            assert len(claimed) == 0

            # Verify it was marked as failed
            conn = store._get_conn()
            row = conn.execute(
                "SELECT status, error FROM work_queue WHERE id = ?",
                ("old-work",),
            ).fetchone()
            assert row[0] == "failed"
            assert row[1] == "startup_recovery"

    def test_returns_recent_items(self):
        """Items younger than 5 minutes are returned for recovery."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = StateStore(db_path)

            store.enqueue_work("recent-work", "review", {"pr_number": 2})
            conn = store._get_conn()
            conn.execute("UPDATE work_queue SET pid = 999999 WHERE id = 'recent-work'")
            conn.commit()

            claimed = store.recover_pending_work()
            assert len(claimed) == 1
            assert claimed[0]["id"] == "recent-work"
            assert claimed[0]["kind"] == "review"

    def test_complete_work_transitions_recovering(self):
        """complete_work() must transition 'recovering' items, not just 'pending'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = StateStore(db_path)

            # Enqueue and recover (transitions to 'recovering')
            store.enqueue_work("work-2", "fix", {"pr_number": 3})
            conn = store._get_conn()
            conn.execute("UPDATE work_queue SET pid = 999999 WHERE id = 'work-2'")
            conn.commit()

            claimed = store.recover_pending_work()
            assert len(claimed) == 1

            # complete_work should succeed on 'recovering' status
            result = store.complete_work("work-2")
            assert result is True

            # Verify it's completed
            conn = store._get_conn()
            row = conn.execute(
                "SELECT status FROM work_queue WHERE id = ?",
                ("work-2",),
            ).fetchone()
            assert row[0] == "completed"

    def test_complete_work_with_error_on_recovering(self):
        """complete_work() with error stores traceback on 'recovering' items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = StateStore(db_path)

            store.enqueue_work("work-3", "review", {"pr_number": 4})
            conn = store._get_conn()
            conn.execute("UPDATE work_queue SET pid = 999999 WHERE id = 'work-3'")
            conn.commit()

            store.recover_pending_work()  # transitions to 'recovering'

            error_msg = "Recovery failed"
            traceback_text = "Traceback..."
            store.complete_work("work-3", error=error_msg, traceback_str=traceback_text)

            conn = store._get_conn()
            row = conn.execute(
                "SELECT status, error, traceback FROM work_queue WHERE id = ?",
                ("work-3",),
            ).fetchone()
            assert row[0] == "failed"
            assert row[1] == error_msg
            assert row[2] == traceback_text


class TestInitDbRecovery:
    """Tests for recover_pending_work() being called on startup."""

    def test_init_db_calls_recover_pending_work(self):
        """init_db() must call recover_pending_work() to replay pending items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # Pre-populate with a pending work item
            conn = sqlite3.connect(db_path)
            conn.execute(
                """CREATE TABLE IF NOT EXISTS work_queue (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT DEFAULT 'pending', created_at REAL,
                    completed_at REAL, error TEXT, traceback TEXT,
                    pid INTEGER, attempts INTEGER NOT NULL DEFAULT 0
                )"""
            )
            conn.execute(
                "INSERT INTO work_queue (id, kind, payload, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                ("pending-review", "review", '{"pr_number": 99}', time.time()),
            )
            conn.commit()
            conn.close()

            # Creating a new StateStore should trigger recovery
            store = StateStore(db_path)

            # The pending item should now be 'recovering'
            conn = store._get_conn()
            row = conn.execute(
                "SELECT status FROM work_queue WHERE id = ?",
                ("pending-review",),
            ).fetchone()
            assert row[0] == "recovering"


class TestRecoveryPidProtection:
    """Tests for PID-based race condition protection."""

    def test_excludes_items_from_current_pid(self):
        """recover_pending_work() must not claim items owned by this process."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = StateStore(db_path)

            # Enqueue with current PID
            store.enqueue_work("work-self", "review", {"pr_number": 1})

            # Should NOT claim items from this PID
            claimed = store.recover_pending_work()
            assert len(claimed) == 0

    def test_claims_items_from_other_pid(self):
        """recover_pending_work() claims items from other (dead) processes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = StateStore(db_path)

            # Simulate item from dead PID
            store.enqueue_work("work-other", "review", {"pr_number": 2})
            conn = store._get_conn()
            conn.execute("UPDATE work_queue SET pid = 999999 WHERE id = 'work-other'")
            conn.commit()

            claimed = store.recover_pending_work()
            assert len(claimed) == 1
            assert claimed[0]["id"] == "work-other"

    def test_claims_items_with_null_pid(self):
        """recover_pending_work() claims items with NULL pid (legacy items)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = StateStore(db_path)

            # Insert legacy item with NULL pid
            conn = store._get_conn()
            conn.execute(
                """INSERT INTO work_queue (id, kind, payload, status, created_at, pid)
                   VALUES (?, ?, ?, 'pending', ?, NULL)""",
                ("legacy-work", "review", '{"pr_number": 3}', time.time()),
            )
            conn.commit()

            claimed = store.recover_pending_work()
            assert len(claimed) == 1
            assert claimed[0]["id"] == "legacy-work"


class TestRecoveryTunableThresholds:
    """Tests for env-var tunable recovery thresholds."""

    def test_custom_recovery_window(self):
        """RIPTIDE_RECOVERY_WINDOW_SECONDS controls the recovery window."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = StateStore(db_path)

            # Insert item 10 minutes old
            store.enqueue_work("old-work", "review", {"pr_number": 1})
            conn = store._get_conn()
            conn.execute("UPDATE work_queue SET created_at = ? WHERE id = 'old-work'",
                         (time.time() - 600,))
            conn.commit()

            # With default 5-min window, should be stale
            claimed = store.recover_pending_work()
            assert len(claimed) == 0

            # With 15-min window, should be recoverable
            os.environ["RIPTIDE_RECOVERY_WINDOW_SECONDS"] = "900"
            try:
                # Re-enqueue
                store.enqueue_work("old-work-2", "review", {"pr_number": 2})
                conn = store._get_conn()
                conn.execute("UPDATE work_queue SET created_at = ?, pid = 999999 WHERE id = 'old-work-2'",
                             (time.time() - 600,))
                conn.commit()

                claimed = store.recover_pending_work()
                assert len(claimed) == 1
                assert claimed[0]["id"] == "old-work-2"
            finally:
                del os.environ["RIPTIDE_RECOVERY_WINDOW_SECONDS"]

    def test_custom_stale_error(self):
        """RIPTIDE_RECOVERY_STALE_ERROR controls the error message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = StateStore(db_path)

            # Insert old item
            store.enqueue_work("stale-work", "review", {"pr_number": 1})
            conn = store._get_conn()
            conn.execute("UPDATE work_queue SET created_at = ? WHERE id = 'stale-work'",
                         (time.time() - 600,))
            conn.commit()

            os.environ["RIPTIDE_RECOVERY_STALE_ERROR"] = "custom_stale_error"
            try:
                store.recover_pending_work()

                row = conn.execute("SELECT error FROM work_queue WHERE id = 'stale-work'").fetchone()
                assert row[0] == "custom_stale_error"
            finally:
                del os.environ["RIPTIDE_RECOVERY_STALE_ERROR"]
