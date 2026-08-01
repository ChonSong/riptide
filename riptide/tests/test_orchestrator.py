# riptide/tests/test_orchestrator.py
"""
T0 Orchestrator: task classification, tier dispatch, result validation.
"""

import os
import pytest
import tempfile
import sqlite3
from unittest.mock import patch, MagicMock
from riptide.orchestrator import (
    TaskClassifier,
    TaskProfile,
    ResultValidator,
    StateStore,
    T0Orchestrator,
)


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


class TestResultValidator:
    """Test result validation logic."""

    def setup_method(self):
        self.validator = ResultValidator()

    def test_validate_good_result(self):
        result = {"body": "This is a detailed analysis with many words.", "cited_files": ["foo.py"]}
        report = self.validator.validate(result)
        assert report.valid is True
        assert report.confidence == 1.0
        assert len(report.issues) == 0

    def test_validate_short_output_lowers_confidence(self):
        result = {"body": "Hi", "cited_files": ["foo.py"]}
        report = self.validator.validate(result)
        assert report.confidence < 1.0
        assert "suspiciously short" in report.issues[0]

    def test_validate_no_citations_lowers_confidence(self):
        result = {"body": "This is a long enough response with no citations."}
        report = self.validator.validate(result)
        assert report.confidence == 0.7
        assert "No source citations" in report.issues[0]

    def test_validate_truncated_output(self):
        result = {"body": "Truncated but valid output here.", "truncated": True, "cited_files": ["foo.py"]}
        report = self.validator.validate(result)
        assert report.confidence == 0.8
        assert "truncated" in report.issues[0]

    def test_validate_empty_result(self):
        report = self.validator.validate({})
        assert report.valid is False
        assert report.confidence < 0.5

    def test_validate_none_result(self):
        report = self.validator.validate(None)
        assert report.valid is False
        assert report.confidence == 0.0


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

    def test_serial_review_small_pr_stops_at_t2(self):
        """Small PR in serial mode should use T2 and stop."""
        mock_companion = MagicMock()
        mock_companion.classify_pr_mood.return_value = "✨"
        mock_companion.select_gif.return_value = "http://example.com/gif.gif"
        mock_companion._get_graph_context.return_value = {"nodes": 0}
        mock_companion._generate_tldr.return_value = "Quick summary"
        mock_companion._get_bot2_status.return_value = None
        orch = T0Orchestrator(companion=mock_companion, state_store=self.store)
        profile = self._make_profile(4, [{"filename": "a.py"}], 30)
        with patch.object(orch, "_dispatch_t1") as mock_t1:
            result = orch.review_pr(profile, mode="serial")
            mock_t1.assert_not_called()
            assert result["status"] == "complete"

    def test_serial_review_large_pr_escalates(self):
        """Large PR in serial mode should escalate past T2."""
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
            result = orch.review_pr(profile, mode="serial")
            mock_t1.assert_called_once()
            assert result["status"] == "complete"
