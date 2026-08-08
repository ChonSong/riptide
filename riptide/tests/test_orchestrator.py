# riptide/tests/test_orchestrator.py
"""
T0 Orchestrator: task classification, tier dispatch, result validation.
"""

import os
import time
import pytest
import tempfile
import sqlite3
from unittest.mock import patch, MagicMock
from riptide.orchestrator import (
    TaskClassifier,
    TaskProfile,
    T0Orchestrator,
)
from riptide.state import StateStore


class TestTaskClassifier:
    """Test task classification for tier dispatch."""

    def setup_method(self):
        self.classifier = TaskClassifier()

    def test_classify_small_pr_no_ui(self):
        files = [
            {"filename": "src/utils/helper.py"},
            {"filename": "tests/test_helper.py"},
        ]
        profile = self.classifier.classify(1, "test", "repo", "fix: small change", "user", files, 50)
        assert profile.pr_number == 1
        assert profile.needs_t1 is False
        assert profile.needs_t3_visual is False

    def test_classify_large_pr_triggers_t1(self):
        files = [{"filename": f"src/module{i}/file.py"} for i in range(5)]
        profile = self.classifier.classify(2, "test", "repo", "feat: big refactor", "user", files, 500)
        assert profile.needs_t1 is True
        assert profile.needs_t3_visual is False

    def test_classify_pr_with_ui_triggers_t3_visual(self):
        files = [
            {"filename": "src/components/Button.tsx"},
            {"filename": "src/utils/helper.py"},
        ]
        profile = self.classifier.classify(3, "test", "repo", "feat: add button", "user", files, 80)
        assert profile.needs_t3_visual is True
        assert len(profile.ui_files) == 1

    def test_classify_pr_with_arch_triggers_t3_arch(self):
        files = [
            {"filename": "riptide/server.py"},
            {"filename": "riptide/webhook.py"},
        ]
        profile = self.classifier.classify(4, "test", "repo", "refactor: restructure", "user", files, 300)
        assert profile.needs_t3_arch is True

    def test_detect_ui_files_by_extension(self):
        files = [
            {"filename": "Button.tsx"},
            {"filename": "App.vue"},
            {"filename": "styles.css"},
            {"filename": "index.html"},
            {"filename": "icon.svg"},
            {"filename": "helper.py"},
        ]
        ui = self.classifier._detect_ui_files(files)
        assert len(ui) == 5

    def test_detect_ui_files_ignores_non_ui(self):
        files = [
            {"filename": "server.py"},
            {"filename": "config.json"},
            {"filename": "README.md"},
        ]
        assert self.classifier._detect_ui_files(files) == []


class TestStateStore:
    """Test SQLite state store for job tracking and dedup."""

    def setup_method(self):
        self.db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        self.store = StateStore(db_path=self.db_path)

    def test_reserve_delivery_unique(self):
        assert self.store.reserve_delivery("del-1") is True

    def test_reserve_delivery_duplicate(self):
        self.store.reserve_delivery("del-1")
        assert self.store.reserve_delivery("del-1") is False

    def test_create_and_complete_job(self):
        self.store.create_job("job-1", 42, "t1")
        self.store.mark_complete("job-1")
        # Job no longer pending
        conn = sqlite3.connect(self.db_path)
        pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
        assert pending == 0
        conn.close()

    def test_create_and_fail_job(self):
        self.store.create_job("job-1", 42, "t3_visual")
        self.store.mark_failed("job-1")
        conn = sqlite3.connect(self.db_path)
        failed = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='failed'").fetchone()[0]
        assert failed == 1
        conn.close()

    def test_create_job_duplicate_id_no_crash(self):
        """Duplicate job_id must not crash — second call is a no-op."""
        self.store.create_job("job-dup", 42, "t1")
        self.store.create_job("job-dup", 42, "t1")  # must not raise
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM jobs WHERE id='job-dup'").fetchone()[0]
        assert count == 1
        conn.close()

    def test_has_pending_job_returns_true_when_pending(self):
        self.store.create_job("riptide-review-ChonSong-riptide-42-abc123-1234567890", 42, "t1")
        assert self.store.has_pending_job("riptide-review-ChonSong-riptide-42") is True

    def test_has_pending_job_returns_false_when_complete(self):
        self.store.create_job("riptide-review-ChonSong-riptide-42-abc123-1234567890", 42, "t1")
        self.store.mark_complete("riptide-review-ChonSong-riptide-42-abc123-1234567890")
        assert self.store.has_pending_job("riptide-review-ChonSong-riptide-42") is False

    def test_has_pending_job_returns_false_when_no_job(self):
        assert self.store.has_pending_job("riptide-review-ChonSong-riptide-99") is False

    def test_has_pending_job_escapes_like_wildcards(self):
        """Underscores in owner/repo names must be escaped in LIKE query."""
        self.store.create_job("riptide-review-ChonSong_my-repo-42-abc-123", 42, "t1")
        assert self.store.has_pending_job("riptide-review-ChonSong_my-repo-42") is True
        # Should NOT match a different prefix (underscore is literal, not wildcard)
        assert self.store.has_pending_job("riptide-review-ChonSongXmy-repo-42") is False

    def test_has_pending_job_no_pr_number_collision(self):
        """PR #42 must not match PR #420 (hyphen delimiter prevents prefix collision)."""
        self.store.create_job("riptide-review-ChonSong-riptide-420-abc123-1234567890", 420, "t1")
        assert self.store.has_pending_job("riptide-review-ChonSong-riptide-42") is False
        assert self.store.has_pending_job("riptide-review-ChonSong-riptide-420") is True

    def test_reserve_job_concurrent_only_one_wins(self):
        """Concurrent reserve_job calls from two threads: exactly one succeeds."""
        import threading

        prefix = "riptide-review-ChonSong-riptide-42"
        results = []
        barrier = threading.Barrier(2)

        def reserve(job_id):
            # Wait for both threads to be ready, then race
            barrier.wait()
            results.append(self.store.reserve_job(f"{prefix}-{job_id}", 42, "t1", prefix))

        t1 = threading.Thread(target=reserve, args=("a",))
        t2 = threading.Thread(target=reserve, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one reservation should succeed
        assert results.count(True) == 1, f"Expected exactly 1 True, got {results}"
        assert results.count(False) == 1, f"Expected exactly 1 False, got {results}"

        # Verify only one pending row exists for this prefix
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
        conn.close()
        assert count == 1

    def test_has_pending_job_returns_false_when_stale(self):
        """Pending jobs older than 2h are ignored (TTL)."""
        # Create a job with a created_at timestamp 3 hours ago
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO jobs (id, pr_number, tier, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            ("riptide-review-ChonSong-riptide-42-abc123-1234567890", 42, "t1", time.time() - 10800),  # 3h ago
        )
        conn.commit()
        conn.close()
        assert self.store.has_pending_job("riptide-review-ChonSong-riptide-42") is False

    def test_cleanup_stale_pending_marks_old_jobs_failed(self):
        """Stale pending jobs are marked failed after cleanup."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO jobs (id, pr_number, tier, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            ("riptide-review-ChonSong-riptide-42-abc123-1234567890", 42, "t1", time.time() - 10800),
        )
        conn.commit()
        conn.close()
        self.store.cleanup_stale_pending()
        assert self.store.has_pending_job("riptide-review-ChonSong-riptide-42") is False
        # Verify it's marked failed, not deleted
        conn = sqlite3.connect(self.db_path, timeout=30)
        failed = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='failed'").fetchone()[0]
        conn.close()
        assert failed == 1

    def test_get_job_status_returns_latest(self):
        self.store.create_job("job-old", 42, "t1")
        self.store.mark_complete("job-old")
        self.store.create_job("job-new", 42, "t1")
        status = self.store.get_job_status(42)
        assert status is not None
        assert status["id"] == "job-new"
        assert status["status"] == "pending"

    def test_get_job_status_returns_none_when_no_jobs(self):
        assert self.store.get_job_status(99) is None


class TestT0Orchestrator:
    """Test T0 orchestrator with both modes."""

    def setup_method(self):
        self.db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        self.store = StateStore(db_path=self.db_path)
        # Mock semaphore for tests (avoid blocking)
        self._sem_patcher = patch("riptide.orchestrator._T0_SEMAPHORE")
        self._mock_sem = self._sem_patcher.start()
        self._mock_sem.acquire.return_value = True

    def teardown_method(self):
        self._sem_patcher.stop()

    def _make_profile(self, pr_number, files, total_loc, title="feat: test", ui_files=None):
        return TaskProfile(
            pr_number=pr_number, owner="test", repo="repo",
            title=title, author="user", files=files, ui_files=ui_files or [],
            total_loc=total_loc,
        )

    def test_parallel_review_small_pr_no_dispatch(self):
        """Small PR with no UI should not dispatch any tier."""
        orch = T0Orchestrator(state_store=self.store)
        profile = self._make_profile(1, [{"filename": "a.py"}], 20)
        with patch.object(orch, "_dispatch_t1") as mock_t1:
            with patch.object(orch, "_dispatch_t3_visual") as mock_t3v:
                result = orch.review_pr(profile, mode="parallel")
                mock_t1.assert_not_called()
                mock_t3v.assert_not_called()
                assert result["status"] == "complete"

    def test_parallel_review_large_pr_dispatches_t1(self):
        """Large PR should dispatch T1."""
        orch = T0Orchestrator(state_store=self.store)
        with patch.object(orch, "_dispatch_t1", return_value={"body": "t1 done"}) as mock_t1:
            files = [{"filename": f"f{i}.py"} for i in range(5)]
            profile = self._make_profile(2, files, 500)
            result = orch.review_pr(profile, mode="parallel")
            mock_t1.assert_called_once()
            assert "t1" in result["tiers_used"]

    def test_parallel_review_ui_pr_dispatches_t3_visual(self):
        """UI PR should dispatch T3 visual."""
        orch = T0Orchestrator(state_store=self.store)
        with patch.object(orch, "_dispatch_t3_visual", return_value={"body": "t3v done"}) as mock_t3v:
            files = [{"filename": "Button.tsx"}]
            profile = self._make_profile(3, files, 80, ui_files=files)
            result = orch.review_pr(profile, mode="parallel")
            mock_t3v.assert_called_once()
            assert "t3_visual" in result["tiers_used"]

    def test_small_pr_no_dispatch(self):
        """Small PR with no UI should not dispatch any tier."""
        mock_companion = MagicMock()
        mock_companion.classify_pr_mood.return_value = "✨"
        mock_companion.select_gif.return_value = "http://example.com/gif.gif"
        mock_companion._get_graph_context.return_value = {"nodes": 0}
        mock_companion._generate_tldr.return_value = "Quick summary"
        mock_companion._get_bot2_status.return_value = None
        orch = T0Orchestrator(companion=mock_companion, state_store=self.store)
        profile = self._make_profile(4, [{"filename": "a.py"}], 30)
        with patch.object(orch, "_dispatch_t1") as mock_t1:
            result = orch.review_pr(profile, mode="parallel")
            mock_t1.assert_not_called()
            assert result["status"] == "complete"

    def test_large_pr_dispatches_t1(self):
        """Large PR should dispatch T1."""
        mock_companion = MagicMock()
        mock_companion.classify_pr_mood.return_value = "✨"
        mock_companion.select_gif.return_value = "http://example.com/gif.gif"
        mock_companion._get_graph_context.return_value = {"nodes": 0}
        mock_companion._generate_tldr.return_value = "Quick summary"
        mock_companion._get_bot2_status.return_value = None
        orch = T0Orchestrator(companion=mock_companion, state_store=self.store)
        with patch.object(orch, "_dispatch_t1", return_value={"body": "t1 done"}) as mock_t1:
            files = [{"filename": f"f{i}.py"} for i in range(5)]
            profile = self._make_profile(5, files, 300)
            result = orch.review_pr(profile, mode="parallel")
            mock_t1.assert_called_once()
            assert result["status"] == "complete"
