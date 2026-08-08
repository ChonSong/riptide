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
