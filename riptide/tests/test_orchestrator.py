# riptide/tests/test_orchestrator.py
"""
T0 Orchestrator: task classification, tier dispatch, result validation.
"""

import os
import pytest
import tempfile
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
        profile = self.classifier.classify(1, "test", "repo", "fix: small change", files, 50)
        assert profile.pr_number == 1
        assert profile.needs_t1 is False
        assert profile.needs_t3_visual is False

    def test_classify_large_pr_triggers_t1(self):
        files = [{"filename": f"src/module{i}/file.py"} for i in range(5)]
        profile = self.classifier.classify(2, "test", "repo", "feat: big refactor", files, 500)
        assert profile.needs_t1 is True
        assert profile.needs_t3_visual is False

    def test_classify_pr_with_ui_triggers_t3_visual(self):
        files = [
            {"filename": "src/components/Button.tsx"},
            {"filename": "src/utils/helper.py"},
        ]
        profile = self.classifier.classify(3, "test", "repo", "feat: add button", files, 80)
        assert profile.needs_t3_visual is True
        assert len(profile.ui_files) == 1

    def test_classify_pr_with_arch_triggers_t3_arch(self):
        files = [
            {"filename": "riptide/server.py"},
            {"filename": "riptide/webhook.py"},
        ]
        profile = self.classifier.classify(4, "test", "repo", "refactor: restructure", files, 300)
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
        assert len(self.store.pending_jobs(42)) == 1
        self.store.mark_complete("job-1")
        assert len(self.store.pending_jobs(42)) == 0

    def test_create_and_fail_job(self):
        self.store.create_job("job-1", 42, "t3_visual")
        self.store.mark_failed("job-1")
        assert len(self.store.pending_jobs(42)) == 0

    def test_wait_for_all_completes(self):
        self.store.create_job("job-1", 42, "t1")
        self.store.mark_complete("job-1")
        assert self.store.wait_for_all(42, timeout=5) is True

    def test_wait_for_all_timeout(self):
        self.store.create_job("job-1", 42, "t1")
        # Don't complete — should timeout
        assert self.store.wait_for_all(42, timeout=1) is False

    def test_get_results(self):
        self.store.create_job("job-1", 42, "t1")
        self.store.mark_complete("job-1")
        results = self.store.get_results(42)
        assert "t1" in results
        assert results["t1"]["status"] == "complete"


class TestT0Orchestrator:
    """Test T0 orchestrator with both modes."""

    def setup_method(self):
        self.db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        self.store = StateStore(db_path=self.db_path)

    def test_parallel_review_small_pr_no_dispatch(self):
        """Small PR with no UI should not dispatch any tier."""
        orch = T0Orchestrator(mode="parallel", state_store=self.store)
        with patch.object(orch, "_dispatch_t1") as mock_t1:
            with patch.object(orch, "_dispatch_t3_visual") as mock_t3v:
                result = orch.review_pr(1, "test", "repo", "fix: tiny", [{"filename": "a.py"}], 20)
                mock_t1.assert_not_called()
                mock_t3v.assert_not_called()
                assert result["status"] == "complete"

    def test_parallel_review_large_pr_dispatches_t1(self):
        """Large PR should dispatch T1."""
        orch = T0Orchestrator(mode="parallel", state_store=self.store)
        with patch.object(orch, "_dispatch_t1", return_value={"body": "t1 done"}) as mock_t1:
            files = [{"filename": f"f{i}.py"} for i in range(5)]
            result = orch.review_pr(2, "test", "repo", "feat: big", files, 500)
            mock_t1.assert_called_once()
            assert "t1" in result["tiers_used"]

    def test_parallel_review_ui_pr_dispatches_t3_visual(self):
        """UI PR should dispatch T3 visual."""
        orch = T0Orchestrator(mode="parallel", state_store=self.store)
        with patch.object(orch, "_dispatch_t3_visual", return_value={"body": "t3v done"}) as mock_t3v:
            files = [{"filename": "Button.tsx"}]
            result = orch.review_pr(3, "test", "repo", "feat: ui", files, 80)
            mock_t3v.assert_called_once()
            assert "t3_visual" in result["tiers_used"]

    def test_serial_review_small_pr_stops_at_t2(self):
        """Small PR in serial mode should use T2 and stop."""
        orch = T0Orchestrator(mode="serial", state_store=self.store)
        with patch.object(orch, "_dispatch_t2", return_value={"body": "t2 result", "cited_files": []}) as mock_t2:
            with patch.object(orch, "_dispatch_t1") as mock_t1:
                result = orch.review_pr(4, "test", "repo", "fix: small", [{"filename": "a.py"}], 30)
                mock_t2.assert_called_once()
                mock_t1.assert_not_called()
                assert result["status"] == "complete"

    def test_serial_review_large_pr_escalates(self):
        """Large PR in serial mode should escalate past T2."""
        orch = T0Orchestrator(mode="serial", state_store=self.store)
        with patch.object(orch, "_dispatch_t2", return_value={"body": "t2 done"}):
            with patch.object(orch, "_dispatch_t1", return_value={"body": "t1 done"}) as mock_t1:
                files = [{"filename": f"f{i}.py"} for i in range(5)]
                result = orch.review_pr(5, "test", "repo", "feat: large", files, 300)
                mock_t1.assert_called_once()
                assert result["status"] == "complete"
