#!/usr/bin/env python3
"""Tests for riptide/test_oracle.py."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the workspace is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from riptide.test_oracle import (  # noqa: E402
    FILE_TEST_MAP,
    find_missing_tests,
    generate_test_report,
    map_files_to_tests,
    run_tests,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_repo(tmp: Path, src_files: list[str], test_files: list[str]) -> Path:
    """Create a temporary repo structure."""
    for f in src_files:
        p = tmp / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# src")
    for f in test_files:
        p = tmp / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("def test_stub():\n    assert True\n")
    return tmp


# ---------------------------------------------------------------------------
# FILE_TEST_MAP
# ---------------------------------------------------------------------------

class TestFileTestMap:
    def test_not_empty(self):
        assert len(FILE_TEST_MAP) > 0

    def test_values_are_lists(self):
        for k, v in FILE_TEST_MAP.items():
            assert isinstance(v, list), f"value for {k} must be a list"
            assert all(isinstance(p, str) for p in v)

    def test_keys_are_riptide_paths(self):
        for k in FILE_TEST_MAP:
            assert k.startswith("riptide/"), f"key {k} must start with riptide/"


# ---------------------------------------------------------------------------
# map_files_to_tests
# ---------------------------------------------------------------------------

class TestMapFilesToTests:
    def test_direct_match(self, tmp_path):
        root = _make_repo(tmp_path,
                         src_files=["riptide/webhook.py"],
                         test_files=["tests/test_webhook.py"])
        result = map_files_to_tests(["riptide/webhook.py"], root=str(root))
        assert result == ["tests/test_webhook.py"]

    def test_direct_match_multiple_tests(self, tmp_path):
        root = _make_repo(tmp_path,
                         src_files=["riptide/state.py"],
                         test_files=["tests/test_state.py", "tests/test_work_state.py"])
        result = map_files_to_tests(["riptide/state.py"], root=str(root))
        assert "tests/test_state.py" in result
        assert "tests/test_work_state.py" in result

    def test_no_match_returns_empty(self, tmp_path):
        root = _make_repo(tmp_path,
                         src_files=["riptide/unknown_module.py"],
                         test_files=[])
        result = map_files_to_tests(["riptide/unknown_module.py"], root=str(root))
        # Falls back to convention-based lookup → finds nothing.
        assert result == []

    def test_convention_fallback(self, tmp_path):
        """A file not in FILE_TEST_MAP should still find tests via naming convention."""
        root = _make_repo(tmp_path,
                         src_files=["riptide/my_new_module.py"],
                         test_files=["tests/test_my_new_module.py"])
        result = map_files_to_tests(["riptide/my_new_module.py"], root=str(root))
        assert "tests/test_my_new_module.py" in result

    def test_multiple_files(self, tmp_path):
        root = _make_repo(tmp_path,
                         src_files=["riptide/webhook.py", "riptide/state.py"],
                         test_files=["tests/test_webhook.py", "tests/test_state.py"])
        result = map_files_to_tests(
            ["riptide/webhook.py", "riptide/state.py"], root=str(root)
        )
        assert "tests/test_webhook.py" in result
        assert "tests/test_state.py" in result

    def test_deduplication(self, tmp_path):
        """A file mapped by multiple patterns should yield unique test paths."""
        root = _make_repo(tmp_path,
                         src_files=["riptide/state.py"],
                         test_files=["tests/test_state.py"])
        # state.py → tests/test_state*.py AND tests/test_work_state.py
        result = map_files_to_tests(["riptide/state.py"], root=str(root))
        assert len(result) == len(set(result))

    def test_empty_input(self):
        assert map_files_to_tests([]) == []


# ---------------------------------------------------------------------------
# find_missing_tests
# ---------------------------------------------------------------------------

class TestFindMissingTests:
    def test_no_missing(self, tmp_path):
        root = _make_repo(tmp_path,
                         src_files=["riptide/webhook.py"],
                         test_files=["tests/test_webhook.py"])
        result = find_missing_tests(["riptide/webhook.py"], root=str(root))
        assert result == []

    def test_missing_detected(self, tmp_path):
        root = _make_repo(tmp_path,
                         src_files=["riptide/webhook.py"],
                         test_files=[])
        result = find_missing_tests(["riptide/webhook.py"], root=str(root))
        assert "riptide/webhook.py" in result

    def test_config_files_skipped(self, tmp_path):
        root = _make_repo(tmp_path,
                         src_files=[],  # don't create actual files
                         test_files=[])
        # Config files should not show up as missing
        result = find_missing_tests(["riptide/AGENTS.md"], root=str(root))
        assert result == []

    def test_test_files_skipped(self, tmp_path):
        root = _make_repo(tmp_path,
                         src_files=[],
                         test_files=["tests/test_fake.py"])
        result = find_missing_tests(["tests/test_fake.py"], root=str(root))
        assert result == []

    def test_partial_missing(self, tmp_path):
        root = _make_repo(tmp_path,
                         src_files=["riptide/webhook.py", "riptide/state.py"],
                         test_files=["tests/test_webhook.py"])
        result = find_missing_tests(
            ["riptide/webhook.py", "riptide/state.py"], root=str(root)
        )
        assert "riptide/state.py" in result
        assert "riptide/webhook.py" not in result

    def test_empty_input(self):
        assert find_missing_tests([]) == []


# ---------------------------------------------------------------------------
# run_tests
# ---------------------------------------------------------------------------

class TestRunTests:
    def test_empty_list(self):
        result = run_tests([], cwd=".")
        assert result["tests_run"] == 0
        assert result["passed"] == 0
        assert result["failed"] == 0
        assert result["duration_s"] == 0.0

    @patch("riptide.test_oracle.subprocess.run")
    def test_all_pass(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="3 passed in 0.12s\n",
            stderr="",
        )
        result = run_tests(["tests/test_a.py", "tests/test_b.py"], cwd=".")
        assert result["passed"] == 3
        assert result["failed"] == 0
        assert result["tests_run"] == 3

    @patch("riptide.test_oracle.subprocess.run")
    def test_some_fail(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="2 passed, 1 failed in 0.5s\n",
            stderr="",
        )
        result = run_tests(["tests/test_a.py"], cwd=".")
        assert result["passed"] == 2
        assert result["failed"] == 1
        assert result["tests_run"] == 3

    @patch("riptide.test_oracle.subprocess.run")
    def test_no_output_fallback(self, mock_run):
        """If pytest exits non-zero with no parseable output, assume all fail."""
        mock_run.return_value = MagicMock(
            returncode=2,
            stdout="",
            stderr="error",
        )
        result = run_tests(["tests/test_a.py"], cwd=".")
        assert result["failed"] == 1
        assert result["passed"] == 0


# ---------------------------------------------------------------------------
# generate_test_report
# ---------------------------------------------------------------------------

class TestGenerateTestReport:
    @patch("riptide.test_oracle.subprocess.run")
    def test_pass_status(self, mock_run, tmp_path):
        root = _make_repo(tmp_path,
                         src_files=["riptide/webhook.py"],
                         test_files=["tests/test_webhook.py"])
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="5 passed in 0.1s\n",
            stderr="",
        )
        report = generate_test_report(
            "ChonSong", "riptide", 42,
            ["riptide/webhook.py"],
            root=str(root),
        )
        assert report["status"] == "pass"
        assert report["passed"] == 5
        assert report["failed"] == 0
        assert report["owner"] == "ChonSong"
        assert report["repo"] == "riptide"
        assert report["pr_number"] == 42

    @patch("riptide.test_oracle.subprocess.run")
    def test_fail_status(self, mock_run, tmp_path):
        root = _make_repo(tmp_path,
                         src_files=["riptide/webhook.py"],
                         test_files=["tests/test_webhook.py"])
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="1 passed, 2 failed in 0.1s\n",
            stderr="",
        )
        report = generate_test_report(
            "ChonSong", "riptide", 42,
            ["riptide/webhook.py"],
            root=str(root),
        )
        assert report["status"] == "fail"
        assert report["failed"] == 2

    def test_skip_status(self, tmp_path):
        """If no test files are found, status is 'skip'."""
        root = _make_repo(tmp_path,
                         src_files=["riptide/webhook.py"],
                         test_files=[])
        report = generate_test_report(
            "ChonSong", "riptide", 42,
            ["riptide/webhook.py"],
            root=str(root),
        )
        assert report["status"] == "skip"
        assert report["tests_run"] == 0

    @patch("riptide.test_oracle.subprocess.run")
    def test_missing_tests_in_report(self, mock_run, tmp_path):
        root = _make_repo(tmp_path,
                         src_files=["riptide/webhook.py"],
                         test_files=[])  # no tests → missing
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )
        report = generate_test_report(
            "ChonSong", "riptide", 42,
            ["riptide/webhook.py"],
            root=str(root),
        )
        assert "riptide/webhook.py" in report["missing_tests"]


# ---------------------------------------------------------------------------
# Integration-style tests (slower, no mocks for subprocess)
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_real_passing_tests(self, tmp_path):
        """Run real pytest on real passing tests."""
        root = _make_repo(tmp_path,
                         src_files=["riptide/webhook.py"],
                         test_files=["tests/test_webhook.py"])
        result = run_tests(["tests/test_webhook.py"], cwd=str(root))
        assert result["tests_run"] >= 1
        assert result["passed"] >= 1
        assert result["failed"] == 0

    def test_real_failing_tests(self, tmp_path):
        """Run real pytest on a deliberately-failing test."""
        root = tmp_path
        (root / "tests").mkdir(parents=True, exist_ok=True)
        (root / "riptide").mkdir(parents=True, exist_ok=True)
        (root / "riptide" / "__init__.py").write_text("")
        (root / "riptide" / "webhook.py").write_text("# src\n")
        (root / "tests" / "test_broken.py").write_text(
            "def test_fail():\n    assert 1 == 2\n"
        )
        result = run_tests(["tests/test_broken.py"], cwd=str(root))
        assert result["failed"] >= 1
        assert result["passed"] == 0