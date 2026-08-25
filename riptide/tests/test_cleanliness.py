#!/usr/bin/env python3
"""Tests for riptide.pipeline.cleanliness — PR cleanliness evaluation."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch, mock_open

import pytest

from riptide.pipeline.cleanliness import Cleanliness
from riptide.pipeline.probe import Probe


class TestCleanlinessEvaluate:
    """Test Cleanliness evaluation logic."""

    def _make_cleanliness(self, signals: dict) -> Cleanliness:
        return Cleanliness({"cleanliness": signals})

    def test_clean_pr_no_findings(self):
        """PR with no issues should have no findings."""
        c = self._make_cleanliness({
            "mergeable": {"mergeable": "MERGEABLE", "status": "CLEAN"},
            "related_prs": [],
            "test_coverage": {
                "source_files": ["foo.py"],
                "test_files": ["tests/test_foo.py"],
                "untested_source": [],
                "has_test_coverage": True,
            },
            "description_quality": {
                "has_body": True,
                "body_length": 200,
                "issue_refs": ["123"],
                "has_issue_link": True,
                "quality": "good",
            },
            "commit_hygiene": {
                "commits": ["fix: something"],
                "conventional_count": 1,
                "total": 1,
                "all_conventional": True,
            },
            "staleness": {"age_days": 5, "is_stale": False},
            "ci_precheck": {"status": "passing", "failing": []},
        })
        result = c.evaluate()
        assert result["findings"] == []
        assert result["score"] == 100
        assert result["summary"] == "PR is clean"

    def test_merge_conflict_critical(self):
        """Merge conflict should produce critical finding."""
        c = self._make_cleanliness({
            "mergeable": {"mergeable": "CONFLICTING", "status": "CONFLICTING"},
            "related_prs": [],
            "test_coverage": {"source_files": [], "test_files": [], "untested_source": []},
            "description_quality": {"quality": "good"},
            "commit_hygiene": {"commits": [], "total": 0},
            "staleness": {},
            "ci_precheck": {"status": "passing"},
        })
        result = c.evaluate()
        conflict_findings = [f for f in result["findings"] if f["category"] == "conflict"]
        assert len(conflict_findings) == 1
        assert conflict_findings[0]["severity"] == "critical"
        assert "conflict" in conflict_findings[0]["message"].lower()

    def test_related_prs_info(self):
        """Related PRs should produce info findings."""
        c = self._make_cleanliness({
            "mergeable": {"mergeable": "MERGEABLE"},
            "related_prs": [
                {"number": 42, "title": "Fix foo", "author": "alice", "overlap_files": ["foo.py"]},
            ],
            "test_coverage": {"source_files": [], "test_files": [], "untested_source": []},
            "description_quality": {"quality": "good"},
            "commit_hygiene": {"commits": [], "total": 0},
            "staleness": {},
            "ci_precheck": {"status": "passing"},
        })
        result = c.evaluate()
        related = [f for f in result["findings"] if f["category"] == "related"]
        assert len(related) == 1
        assert "#42" in related[0]["message"]
        assert "@alice" in related[0]["message"]

    def test_missing_test_coverage_warning(self):
        """Source-only changes should produce test coverage warning."""
        c = self._make_cleanliness({
            "mergeable": {"mergeable": "MERGEABLE"},
            "related_prs": [],
            "test_coverage": {
                "source_files": ["foo.py", "bar.py"],
                "test_files": [],
                "untested_source": ["foo.py", "bar.py"],
                "has_test_coverage": False,
            },
            "description_quality": {"quality": "good"},
            "commit_hygiene": {"commits": [], "total": 0},
            "staleness": {},
            "ci_precheck": {"status": "passing"},
        })
        result = c.evaluate()
        test_findings = [f for f in result["findings"] if f["category"] == "test_coverage"]
        assert len(test_findings) == 1
        assert test_findings[0]["severity"] == "warning"
        assert "foo.py" in test_findings[0]["message"]

    def test_missing_description_warning(self):
        """Missing description should produce warning."""
        c = self._make_cleanliness({
            "mergeable": {"mergeable": "MERGEABLE"},
            "related_prs": [],
            "test_coverage": {"source_files": [], "test_files": [], "untested_source": []},
            "description_quality": {
                "has_body": False,
                "body_length": 0,
                "issue_refs": [],
                "quality": "missing",
            },
            "commit_hygiene": {"commits": [], "total": 0},
            "staleness": {},
            "ci_precheck": {"status": "passing"},
        })
        result = c.evaluate()
        desc_findings = [f for f in result["findings"] if f["category"] == "description"]
        assert len(desc_findings) == 1
        assert desc_findings[0]["severity"] == "warning"

    def test_non_conventional_commits_info(self):
        """Non-conventional commits should produce info finding."""
        c = self._make_cleanliness({
            "mergeable": {"mergeable": "MERGEABLE"},
            "related_prs": [],
            "test_coverage": {"source_files": [], "test_files": [], "untested_source": []},
            "description_quality": {"quality": "good"},
            "commit_hygiene": {
                "commits": ["fix: good", "bad message"],
                "conventional_count": 1,
                "total": 2,
                "all_conventional": False,
            },
            "staleness": {},
            "ci_precheck": {"status": "passing"},
        })
        result = c.evaluate()
        commit_findings = [f for f in result["findings"] if f["category"] == "commit"]
        assert len(commit_findings) == 1
        assert "1/2" in commit_findings[0]["message"]

    def test_stale_pr_warning(self):
        """PR older than 30 days should produce stale warning."""
        c = self._make_cleanliness({
            "mergeable": {"mergeable": "MERGEABLE"},
            "related_prs": [],
            "test_coverage": {"source_files": [], "test_files": [], "untested_source": []},
            "description_quality": {"quality": "good"},
            "commit_hygiene": {"commits": [], "total": 0},
            "staleness": {"age_days": 45, "is_stale": True},
            "ci_precheck": {"status": "passing"},
        })
        result = c.evaluate()
        stale_findings = [f for f in result["findings"] if f["category"] == "staleness"]
        assert len(stale_findings) == 1
        assert stale_findings[0]["severity"] == "warning"
        assert "45" in stale_findings[0]["message"]

    def test_ci_failing_warning(self):
        """Existing CI failure should produce warning."""
        c = self._make_cleanliness({
            "mergeable": {"mergeable": "MERGEABLE"},
            "related_prs": [],
            "test_coverage": {"source_files": [], "test_files": [], "untested_source": []},
            "description_quality": {"quality": "good"},
            "commit_hygiene": {"commits": [], "total": 0},
            "staleness": {},
            "ci_precheck": {
                "status": "failing",
                "failing": [{"name": "test-required"}],
            },
        })
        result = c.evaluate()
        ci_findings = [f for f in result["findings"] if f["category"] == "ci"]
        assert len(ci_findings) == 1
        assert ci_findings[0]["severity"] == "warning"
        assert "test-required" in ci_findings[0]["message"]

    def test_score_calculation(self):
        """Score should decrease based on severity."""
        c = self._make_cleanliness({
            "mergeable": {"mergeable": "CONFLICTING", "status": "CONFLICTING"},
            "related_prs": [],
            "test_coverage": {
                "source_files": ["foo.py"],
                "test_files": [],
                "untested_source": ["foo.py"],
                "has_test_coverage": False,
            },
            "description_quality": {"quality": "missing", "has_body": False, "issue_refs": []},
            "commit_hygiene": {"commits": [], "total": 0},
            "staleness": {"age_days": 45, "is_stale": True},
            "ci_precheck": {"status": "failing", "failing": [{"name": "test-required"}]},
        })
        result = c.evaluate()
        # 100 - 30 (critical) - 15 (warning) - 15 (warning) - 15 (warning) - 15 (warning) = 10
        assert result["score"] == 10

    def test_summary_format(self):
        """Summary should list counts by severity."""
        c = self._make_cleanliness({
            "mergeable": {"mergeable": "CONFLICTING"},
            "related_prs": [
                {"number": 1, "title": "", "author": "", "overlap_files": ["x"]},
                {"number": 2, "title": "", "author": "", "overlap_files": ["y"]},
            ],
            "test_coverage": {"source_files": [], "test_files": [], "untested_source": []},
            "description_quality": {"quality": "good", "has_issue_link": True},
            "commit_hygiene": {"commits": [], "total": 0},
            "staleness": {},
            "ci_precheck": {"status": "passing"},
        })
        result = c.evaluate()
        assert "1 critical" in result["summary"]
        assert "2 info" in result["summary"]


class TestProbeCleanlinessSignals:
    """Test that Probe gathers cleanliness signals."""

    def test_probe_includes_cleanliness_key(self):
        """Probe.gather() should include cleanliness in output."""
        probe = Probe(123, "ChonSong", "riptide")

        # Mock all the internal methods
        with patch.object(probe, "_get_pr_data", return_value={
            "body": "Fixes #42\n\nGood description",
            "createdAt": "2026-08-01T00:00:00Z",
            "updatedAt": "2026-08-10T00:00:00Z",
        }):
            with patch.object(probe, "_get_pr_files", return_value=[
                {"filename": "foo.py", "additions": 10, "deletions": 0, "status": "modified", "patch": "+x = 1"},
                {"filename": "tests/test_foo.py", "additions": 5, "deletions": 0, "status": "added", "patch": "+def test(): pass"},
            ]):
                with patch.object(probe, "_run_diff_analyzer", return_value={"findings": [], "stats": {}, "verdict": "pass"}):
                    with patch.object(probe, "_run_context_bundle", return_value={"aggregate": {}}):
                        with patch.object(probe, "_run_graphify", return_value={}):
                            with patch.object(probe, "_get_previous_findings", return_value=[]):
                                with patch.object(probe, "_gather_cleanliness_signals", return_value={
                                    "mergeable": {"mergeable": "MERGEABLE"},
                                    "related_prs": [],
                                                                    "test_coverage": {"source_files": ["foo.py"], "test_files": ["tests/test_foo.py"]},
                                                                    "description_quality": {"quality": "good"},
                                                                    "commit_hygiene": {},
                                                                    "staleness": {},
                                                                    "ci_precheck": {},
                                                                }):
                                    result = probe.gather()

        assert "cleanliness" in result
        assert result["cleanliness"]["mergeable"]["mergeable"] == "MERGEABLE"

    def test_probe_gather_cleanliness_real_mocks(self):
        """Test _gather_cleanliness_signals with mocked gh CLI."""
        probe = Probe(42, "ChonSong", "riptide")

        mock_gh_responses = {
            "mergeable": '{"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}',
            "pr_list": json.dumps([
                {"number": 99, "title": "Other PR", "author": {"login": "bob"}, "files": [{"filename": "foo.py"}]},
            ]),
            "commits": json.dumps([{"oid": "abc", "message": "fix: something"}]),
            "checks": json.dumps([{"name": "test-required", "state": "success"}]),
        }

        def mock_subprocess_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            cmd_str = " ".join(cmd)
            if "mergeable" in cmd_str:
                result.stdout = mock_gh_responses["mergeable"]
            elif "pr list" in cmd_str:
                result.stdout = mock_gh_responses["pr_list"]
            elif "commits" in cmd_str:
                result.stdout = mock_gh_responses["commits"]
            elif "checks" in cmd_str:
                result.stdout = mock_gh_responses["checks"]
            else:
                result.stdout = "{}"
            return result

        files = [{"filename": "foo.py", "additions": 10, "deletions": 0, "status": "modified"}]
        pr_data = {"body": "Fixes #123\n\nDescription here", "createdAt": "2026-08-01T00:00:00Z"}

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            signals = probe._gather_cleanliness_signals(pr_data, files)

        assert signals["mergeable"]["mergeable"] == "MERGEABLE"
        assert len(signals["related_prs"]) == 1
        assert signals["related_prs"][0]["number"] == 99
        assert signals["description_quality"]["has_body"] is True
        assert signals["description_quality"]["has_issue_link"] is True
        assert signals["commit_hygiene"]["all_conventional"] is True
        assert signals["ci_precheck"]["status"] == "passing"
