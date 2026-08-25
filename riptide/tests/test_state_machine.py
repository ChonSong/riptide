"""
State machine tests for the optimistic job lifecycle.

Verifies that:
1. Jobs transition from pending → complete (not stuck in pending)
2. Completed jobs don't block future spawns for the same PR
3. cleanup_stale_pending correctly marks old jobs as failed
"""

import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from riptide.state import StateStore


@pytest.fixture
def store():
    """Create a fresh StateStore for each test."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "state.db")
        yield StateStore(db_path)


class TestOptimisticJobLifecycle:
    """Test the pending → complete transition.

    Note: name_prefix must be a prefix of job_id for the LIKE-based
    has_pending_job / reserve_job to work correctly.
    """

    def test_reserve_then_complete(self, store):
        """A job can be reserved and then marked complete."""
        job_id = "test-prefix-job-1"
        assert store.reserve_job(job_id, 42, "t1", "test-prefix")
        assert store.has_pending_job("test-prefix")

        store.mark_complete(job_id)
        assert not store.has_pending_job("test-prefix")

    def test_complete_job_allows_new_reservation(self, store):
        """After completing a job, a new one can be reserved for the same prefix."""
        job_id1 = "test-prefix-job-1"
        assert store.reserve_job(job_id1, 42, "t1", "test-prefix")
        store.mark_complete(job_id1)

        # New job with the same prefix should succeed
        job_id2 = "test-prefix-job-2"
        assert store.reserve_job(job_id2, 42, "t1", "test-prefix")
        store.mark_complete(job_id2)

    def test_pending_job_blocks_duplicate(self, store):
        """A pending job blocks duplicate reservations with the same prefix."""
        job_id1 = "test-prefix-job-1"
        assert store.reserve_job(job_id1, 42, "t1", "test-prefix")

        # Duplicate with the same prefix should fail
        job_id2 = "test-prefix-job-2"
        assert not store.reserve_job(job_id2, 42, "t1", "test-prefix")

        # After completion, new job succeeds
        store.mark_complete(job_id1)
        assert store.reserve_job(job_id2, 42, "t1", "test-prefix")

    def test_failed_job_allows_retry(self, store):
        """A failed job allows retry."""
        job_id1 = "test-prefix-job-1"
        assert store.reserve_job(job_id1, 42, "t1", "test-prefix")
        store.mark_failed(job_id1)

        # Failed job doesn't block
        job_id2 = "test-prefix-job-2"
        assert store.reserve_job(job_id2, 42, "t1", "test-prefix")


class TestCleanupStalePending:
    """Test cleanup of stale pending jobs."""

    def test_cleanup_marks_old_jobs_failed(self, store):
        """Jobs older than max_age are marked failed."""
        job_id = "test-prefix-stale-job"
        assert store.reserve_job(job_id, 42, "t1", "test-prefix")
        assert store.has_pending_job("test-prefix")

        # Cleanup with 0-second TTL marks everything as failed
        store.cleanup_stale_pending(max_age_seconds=0)
        assert not store.has_pending_job("test-prefix")

    def test_cleanup_preserves_recent_jobs(self, store):
        """Recent jobs are not affected by cleanup."""
        job_id = "test-prefix-fresh-job"
        assert store.reserve_job(job_id, 42, "t1", "test-prefix")

        # Cleanup with 1-hour TTL preserves recent jobs
        store.cleanup_stale_pending(max_age_seconds=3600)
        assert store.has_pending_job("test-prefix")

    def test_cleanup_allows_reservation_after_stall(self, store):
        """After cleanup, a new job can be reserved (recovery path)."""
        # Simulate a stuck job
        job_id1 = "test-prefix-stuck-job"
        assert store.reserve_job(job_id1, 42, "t1", "test-prefix")

        # Cleanup recovers
        store.cleanup_stale_pending(max_age_seconds=0)

        # New job can be reserved
        job_id2 = "test-prefix-recovery-job"
        assert store.reserve_job(job_id2, 42, "t1", "test-prefix")
        store.mark_complete(job_id2)

    def test_cleanup_only_affects_pending(self, store):
        """cleanup_stale_pending only touches pending jobs."""
        job_id = "test-prefix-complete-job"
        assert store.reserve_job(job_id, 42, "t1", "test-prefix")
        store.mark_complete(job_id)

        store.cleanup_stale_pending(max_age_seconds=0)

        # Completed job remains completed (not affected by cleanup)
        conn = store._get_conn()
        row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row[0] == "complete"

    def test_cleanup_retries_on_lock_contention(self, store):
        """cleanup_stale_pending retries up to 3 times on SQLite lock contention."""
        # Create a stale job so cleanup has work to do
        job_id = "test-prefix-lock-retry"
        assert store.reserve_job(job_id, 42, "t1", "test-prefix")
        assert store.has_pending_job("test-prefix")

        # Mock _get_conn to return a connection that raises lock errors
        # on the first 2 calls, then succeeds on the 3rd.
        call_count = 0
        original_get_conn = store._get_conn

        def mock_get_conn():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                import sqlite3
                raise sqlite3.OperationalError("database is locked")
            return original_get_conn()

        with patch.object(store, "_get_conn", side_effect=mock_get_conn):
            with patch("time.sleep"):  # Skip actual sleep in retries
                store.cleanup_stale_pending(max_age_seconds=0)

        # After retries, the stale job should be cleaned up
        assert not store.has_pending_job("test-prefix")
        assert call_count == 3  # 2 failures + 1 success

    def test_cleanup_raises_after_max_lock_retries(self, store):
        """cleanup_stale_pending raises after 3 consecutive lock failures."""
        job_id = "test-prefix-lock-fail"
        assert store.reserve_job(job_id, 42, "t1", "test-prefix")

        # Mock _get_conn to always raise lock error
        def mock_get_conn():
            import sqlite3
            raise sqlite3.OperationalError("database is locked")

        with patch.object(store, "_get_conn", side_effect=mock_get_conn):
            with patch("time.sleep"):
                with pytest.raises(Exception):
                    store.cleanup_stale_pending(max_age_seconds=0)


class TestDeliveryStateMachine:
    """Test the processing → done | failed lifecycle for webhook deliveries."""

    def test_reserve_delivery_succeeds(self, store):
        """A new delivery can be reserved."""
        assert store.reserve_delivery("delivery-1")
        conn = store._get_conn()
        row = conn.execute("SELECT status FROM deliveries WHERE delivery_id=?", ("delivery-1",)).fetchone()
        assert row[0] == "processing"

    def test_duplicate_delivery_dropped(self, store):
        """A duplicate delivery is dropped."""
        assert store.reserve_delivery("delivery-1")
        assert not store.reserve_delivery("delivery-1")

    def test_mark_delivery_done(self, store):
        """A delivery can be marked done."""
        store.reserve_delivery("delivery-1")
        store.mark_delivery_done("delivery-1")
        conn = store._get_conn()
        row = conn.execute("SELECT status FROM deliveries WHERE delivery_id=?", ("delivery-1",)).fetchone()
        assert row[0] == "done"

    def test_mark_delivery_failed(self, store):
        """A delivery can be marked failed."""
        store.reserve_delivery("delivery-1")
        store.mark_delivery_failed("delivery-1")
        conn = store._get_conn()
        row = conn.execute("SELECT status FROM deliveries WHERE delivery_id=?", ("delivery-1",)).fetchone()
        assert row[0] == "failed"

    def test_stale_delivery_rereservable(self, store):
        """A delivery stuck in 'processing' becomes re-reservable after TTL."""
        # Reserve with a received_at in the past
        conn = store._get_conn()
        old_time = time.time() - 600  # 10 minutes ago
        conn.execute(
            "INSERT INTO deliveries (delivery_id, received_at, status) VALUES (?, ?, 'processing')",
            ("delivery-stale", old_time),
        )
        conn.commit()

        # Should be re-reservable
        assert store.reserve_delivery("delivery-stale")

    def test_fresh_processing_delivery_not_rereservable(self, store):
        """A fresh 'processing' delivery is NOT re-reservable."""
        store.reserve_delivery("delivery-fresh")
        assert not store.reserve_delivery("delivery-fresh")

    def test_done_delivery_not_rereservable(self, store):
        """A 'done' delivery is NOT re-reservable."""
        store.reserve_delivery("delivery-done")
        store.mark_delivery_done("delivery-done")
        assert not store.reserve_delivery("delivery-done")

    def test_cleanup_old_deliveries(self, store):
        """Old completed/failed deliveries are cleaned up."""
        # Create deliveries with different statuses
        store.reserve_delivery("delivery-old-done")
        store.mark_delivery_done("delivery-old-done")
        store.reserve_delivery("delivery-old-failed")
        store.mark_delivery_failed("delivery-old-failed")
        store.reserve_delivery("delivery-new-done")
        store.mark_delivery_done("delivery-new-done")
        store.reserve_delivery("delivery-processing")
        # Leave delivery-processing in processing state

        # Cleanup with 0-second TTL removes old done/failed
        store.cleanup_old_deliveries(max_age_seconds=0)

        conn = store._get_conn()
        remaining = {row[0] for row in conn.execute("SELECT delivery_id FROM deliveries").fetchall()}
        assert "delivery-old-done" not in remaining
        assert "delivery-old-failed" not in remaining
        assert "delivery-new-done" not in remaining  # Also old (just created, but TTL=0)
        assert "delivery-processing" in remaining  # Processing preserved
