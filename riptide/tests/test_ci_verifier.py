#!/usr/bin/env python3
"""Tests for riptide.pipeline.ci_verifier — CI status verification stage."""

from __future__ import annotations

import json
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from riptide.pipeline.ci_verifier import (
    CIVerifier,
    FIXABLE_CHECKS,
    NON_FIXABLE_CHECKS,
    POLL_INTERVAL,
    POLL_TIMEOUT,
)


class TestCIVerifierInit:
    """Test CIVerifier initialization."""

    def test_init_stores_params(self):
        v = CIVerifier("ChonSong", "riptide", 123)
        assert v.owner == "ChonSong"
        assert v.repo == "riptide"
        assert v.pr_number == 123


class TestIsFixable:
    """Test check classification logic."""

    def setup_method(self):
        self.verifier = CIVerifier("ChonSong", "riptide", 1)

    def test_fixable_test_required(self):
        assert self.verifier._is_fixable({"name": "test-required"}) is True

    def test_fixable_agentlint(self):
        assert self.verifier._is_fixable({"name": "agentlint"}) is True

    def test_fixable_continous_integration(self):
        assert self.verifier._is_fixable({"name": "continous-integration"}) is True

    def test_non_fixable_coderabbit(self):
        assert self.verifier._is_fixable({"name": "CodeRabbit"}) is False

    def test_non_fixable_review_required(self):
        assert self.verifier._is_fixable({"name": "riptide-review-required"}) is False

    def test_non_fixable_gitguardian(self):
        assert self.verifier._is_fixable({"name": "GitGuardian"}) is False

    def test_non_fixable_codeql(self):
        assert self.verifier._is_fixable({"name": "CodeQL"}) is False

    def test_unknown_check_is_non_fixable(self):
        """Unknown checks should default to non-fixable (escalate to human)."""
        assert self.verifier._is_fixable({"name": "some-random-check"}) is False

    def test_case_insensitive_matching(self):
        assert self.verifier._is_fixable({"name": "TEst-REQUIRED"}) is True
        assert self.verifier._is_fixable({"name": "coderabbit-ai"}) is False


class TestFetchChecks:
    """Test _fetch_checks via mocked subprocess."""

    def setup_method(self):
        self.verifier = CIVerifier("ChonSong", "riptide", 42)

    def test_fetch_checks_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps([
            {"name": "test-required", "state": "success"},
            {"name": "agentlint", "state": "success"},
        ])
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            checks = self.verifier._fetch_checks()
            assert checks is not None
            assert len(checks) == 2
            assert checks[0]["name"] == "test-required"
            mock_run.assert_called_once()
            # Verify correct gh command
            call_args = mock_run.call_args[0][0]
            assert "gh" in call_args
            assert "pr" in call_args
            assert "checks" in call_args
            assert "42" in call_args

    def test_fetch_checks_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Not Found"
        with patch("subprocess.run", return_value=mock_result):
            assert self.verifier._fetch_checks() is None

    def test_fetch_checks_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30)):
            assert self.verifier._fetch_checks() is None

    def test_fetch_checks_invalid_json(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not json"
        with patch("subprocess.run", return_value=mock_result):
            assert self.verifier._fetch_checks() is None


class TestPoll:
    """Test the poll loop with mocked _fetch_checks."""

    def setup_method(self):
        self.verifier = CIVerifier("ChonSong", "riptide", 1)

    def test_poll_all_success(self):
        """All checks pass on first poll."""
        with patch.object(self.verifier, "_fetch_checks", return_value=[
            {"name": "test-required", "state": "success"},
            {"name": "agentlint", "state": "success"},
        ]):
            result = self.verifier.poll(timeout=5, interval=1)

        assert result["status"] == "success"
        assert len(result["passed"]) == 2
        assert len(result["failed"]) == 0
        assert result["poll_count"] == 1

    def test_poll_all_failure(self):
        """All checks fail on first poll."""
        with patch.object(self.verifier, "_fetch_checks", return_value=[
            {"name": "test-required", "state": "failure"},
            {"name": "agentlint", "state": "failure"},
        ]):
            result = self.verifier.poll(timeout=5, interval=1)

        assert result["status"] == "failure"
        assert len(result["failed"]) == 2
        assert len(result["fixable"]) == 2
        assert len(result["non_fixable"]) == 0

    def test_poll_mixed_fixable_non_fixable(self):
        """Mix of fixable and non-fixable failures."""
        with patch.object(self.verifier, "_fetch_checks", return_value=[
            {"name": "test-required", "state": "failure"},
            {"name": "CodeRabbit", "state": "failure"},
            {"name": "GitGuardian", "state": "success"},
        ]):
            result = self.verifier.poll(timeout=5, interval=1)

        assert result["status"] == "failure"
        assert len(result["failed"]) == 2
        assert len(result["fixable"]) == 1
        assert result["fixable"][0]["name"] == "test-required"
        assert len(result["non_fixable"]) == 1
        assert result["non_fixable"][0]["name"] == "CodeRabbit"

    def test_poll_waits_for_pending(self):
        """Poll should wait for pending checks to complete."""
        call_count = 0

        def mock_fetch():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return [
                    {"name": "test-required", "state": "pending"},
                    {"name": "agentlint", "state": "success"},
                ]
            return [
                {"name": "test-required", "state": "success"},
                {"name": "agentlint", "state": "success"},
            ]

        with patch.object(self.verifier, "_fetch_checks", side_effect=mock_fetch):
            result = self.verifier.poll(timeout=10, interval=1)

        assert result["status"] == "success"
        assert result["poll_count"] == 3

    def test_poll_timeout(self):
        """Poll should timeout if checks never complete."""
        with patch.object(self.verifier, "_fetch_checks", return_value=[
            {"name": "test-required", "state": "pending"},
        ]):
            result = self.verifier.poll(timeout=2, interval=1)

        assert result["status"] == "timeout"
        assert len(result["pending"]) == 1

    def test_poll_fetch_error(self):
        """Poll should return error status if fetch fails."""
        with patch.object(self.verifier, "_fetch_checks", return_value=None):
            result = self.verifier.poll(timeout=2, interval=1)

        assert result["status"] == "error"


class TestFormatReport:
    """Test human-readable report formatting."""

    def setup_method(self):
        self.verifier = CIVerifier("ChonSong", "riptide", 1)

    def test_format_success(self):
        result = {
            "status": "success",
            "passed": [{"name": "test-required"}],
            "failed": [],
            "fixable": [],
            "non_fixable": [],
        }
        report = self.verifier.format_report(result)
        assert "✅" in report
        assert "All CI checks passed" in report

    def test_format_failure(self):
        result = {
            "status": "failure",
            "passed": [],
            "failed": [
                {"name": "test-required"},
                {"name": "CodeRabbit"},
            ],
            "fixable": [{"name": "test-required"}],
            "non_fixable": [{"name": "CodeRabbit"}],
        }
        report = self.verifier.format_report(result)
        assert "❌" in report
        assert "test-required" in report
        assert "CodeRabbit" in report
        assert "fixable" in report

    def test_format_timeout(self):
        result = {
            "status": "timeout",
            "pending": [{"name": "test-required"}],
            "failed": [],
            "fixable": [],
            "non_fixable": [],
        }
        report = self.verifier.format_report(result)
        assert "⏰" in report
        assert "timeout" in report

    def test_format_error(self):
        result = {"status": "error"}
        report = self.verifier.format_report(result)
        assert "⚠️" in report


class TestConductorIntegration:
    """Test that the Conductor can dispatch ci_verifier role."""

    def test_ci_verifier_in_roles(self):
        from riptide.pipeline.roles import ROLES
        assert "ci_verifier" in ROLES
        assert ROLES["ci_verifier"]["output_format"] == "json"

    def test_conductor_dispatch_ci_verifier(self):
        from riptide.pipeline.conductor import Conductor
        from riptide.pipeline.roles import WorkerBrief

        # Create a conductor-like object to test dispatch
        # We can't fully instantiate without a track, but we can test the method exists
        assert hasattr(Conductor, "_run_ci_verifier")

    def test_create_fix_pipeline(self):
        """Test that create_fix_pipeline function exists and has correct signature."""
        from riptide.pipeline.conductor import create_fix_pipeline
        import inspect

        sig = inspect.signature(create_fix_pipeline)
        params = list(sig.parameters.keys())
        assert "owner" in params
        assert "repo" in params
        assert "pr_number" in params
        assert "pr_details" in params
        assert "files" in params
        assert "description" in params
        assert "push_eligible" in params

        # Verify the function has a docstring mentioning CI
        doc = create_fix_pipeline.__doc__ or ""
        assert "ci_verifier" in doc or "CI" in doc
