#!/usr/bin/env python3
"""Tests for _get_review_findings in riptide.pipeline.probe."""

import json
import subprocess

from unittest.mock import MagicMock, patch

import pytest

from riptide.pipeline.probe import Probe


class TestGetReviewFindings:
    """Tests for _get_review_findings method."""

    @pytest.fixture
    def probe(self):
        return Probe(pr_number=1, owner="ChonSong", repo="riptide")

    # ── Riptide review (severity table) ──────────────────────────────────────

    def test_riptide_severity_table_two_rows(self, probe):
        """2-row severity table -> 2 findings with correct file/line/severity."""
        mock_body = (
            "## Review: 2 warning(s).\n\n"
            "| | Finding | File |\n|---|---|---|\n"
            "| 🔴 | Title 1 | `path.py:123` |\n"
            "| 🟡 | Title 2 | `other.py` |\n\n"
            "More text after the table"
        )
        mock_review = {
            "id": "123",
            "user": {"login": "riptide-bot"},
            "state": "COMMENT",
            "body": mock_body.strip(),
        }

        mock_body = (
            "## Review: 2 warning(s).\n\n"
            "| | Finding | File |\n|---|---|---|\n"
            "| 🔴 | Title 1 | `path.py:123` |\n"
            "| 🟡 | Title 2 | `other.py` |\n\n"
            "More text after the table"
        )
        mock_issue_comment = {
            "id": 456,
            "user": {"login": "riptide-review[bot]"},
            "body": mock_body,
        }

        with patch(
            "riptide.pipeline.probe.subprocess.run"
        ) as mock_run:
            mock_run.side_effect = [
                # Pull requests reviews (CodeRabbit + human)
                MagicMock(returncode=0, stdout=json.dumps([])),
                # Issue comments (riptide review)
                MagicMock(
                    returncode=0,
                    stdout=json.dumps([mock_issue_comment]),
                ),
                # PR inline comments (CodeRabbit + human)
                MagicMock(returncode=0, stdout=json.dumps([])),
            ]

            findings = probe._get_review_findings()

        # Should find 2 findings from the riptide review table
        assert len(findings) == 2
        # First finding: severity 🔴
        f1 = findings[0]
        assert f1["severity"] == "🔴"
        assert f1["file"] == "path.py"
        assert f1["line"] is None  # line stripped from :123
        assert "Title 1" in f1["body"]
        # Second finding: severity 🟡
        f2 = findings[1]
        assert f2["severity"] == "🟡"
        assert f2["file"] == "other.py"
        assert f2["line"] is None
        assert "Title 2" in f2["body"]

    def test_riptide_severity_table_single_row_no_line(self, probe):
        """Single row with just filename (no :line suffix) — real gate shape:
        | sev | finding | file |, File cell last."""
        mock_body = (
            "## Review: 1 warning(s).\n\n"
            "| | Finding | File |\n|---|---|---|\n"
            "| 🔴 | Bug Fix | `src/utils.py` |"
        )
        mock_issue_comment = {
            "id": 789,
            "user": {"login": "riptide-review[bot]"},
            "body": mock_body,
        }

        with patch(
            "riptide.pipeline.probe.subprocess.run"
        ) as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps([])),
                MagicMock(returncode=0, stdout=json.dumps([mock_issue_comment])),
                MagicMock(returncode=0, stdout=json.dumps([])),
            ]

            findings = probe._get_review_findings()

        assert len(findings) == 1
        f = findings[0]
        assert f["severity"] == "🔴"
        assert f["file"] == "src/utils.py"
        assert f["line"] is None
        assert "Bug Fix" in f["body"]

    # ── CodeRabbit inline comments ──────────────────────────────────────────

    def test_coderabbit_inline_comment(self, probe):
        """CodeRabbit inline comment -> finding with file/line from the
        comment's path/line fields, severity mapped from the body marker,
        HTML comments stripped."""
        cr = {
            "id": 6,
            "user": {"login": "coderabbitai[bot]"},
            "path": "app/util.py",
            "line": 9,
            "body": "<!-- ac -->\n\n**🟡 Minor** off-by-one in loop bound",
        }
        with patch("riptide.pipeline.probe.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps([])),
                MagicMock(returncode=0, stdout=json.dumps([])),
                MagicMock(returncode=0, stdout=json.dumps([cr])),
            ]
            findings = probe._get_review_findings()

        assert len(findings) == 1
        f = findings[0]
        assert f["severity"] == "🟡"
        assert f["file"] == "app/util.py"
        assert f["line"] == 9
        assert "<!--" not in f["body"]
        assert "off-by-one" in f["body"]

    def test_coderabbit_summary_comment_skipped(self, probe):
        """CodeRabbit walk-through/summary comments carry no actionable
        finding and must be excluded."""
        cr = {
            "id": 7,
            "user": {"login": "coderabbitai[bot]"},
            "path": "x.py",
            "line": 1,
            "body": "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->Walkthrough",
        }
        with patch("riptide.pipeline.probe.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps([cr])),
                MagicMock(returncode=0, stdout=json.dumps([])),
                MagicMock(returncode=0, stdout=json.dumps([])),
            ]
            findings = probe._get_review_findings()

        assert findings == []

    # ── Human reviews ───────────────────────────────────────────────────────

    def test_human_review_verbatim(self, probe):
        """A human review submission passes through verbatim: severity 🟡
        (unknown), no file/line claimed, body intact."""
        rev = {
            "id": 8,
            "user": {"login": "franksong2702"},
            "state": "CHANGES_REQUESTED",
            "body": "The queue deadlock needs fixing before merge.",
        }
        with patch("riptide.pipeline.probe.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps([rev])),
                MagicMock(returncode=0, stdout=json.dumps([])),
                MagicMock(returncode=0, stdout=json.dumps([])),
            ]
            findings = probe._get_review_findings()

        assert len(findings) == 1
        f = findings[0]
        assert f["source"] == "human"
        assert f["severity"] == "🟡"
        assert f["file"] is None and f["line"] is None
        assert f["body"] == "The queue deadlock needs fixing before merge."

    # ── Reply chains, dedupe, cap ───────────────────────────────────────────

    def test_reply_chain_collapses(self, probe):
        """A reply (in_reply_to_id) does not become its own finding."""
        root = {
            "id": 10,
            "user": {"login": "coderabbitai[bot]"},
            "path": "a.py",
            "line": 3,
            "body": "🟡 Possible race here",
        }
        reply = {
            "id": 11,
            "in_reply_to_id": 10,
            "user": {"login": "ChonSong"},
            "path": "a.py",
            "line": 3,
            "body": "Addressed in follow-up commit",
        }
        with patch("riptide.pipeline.probe.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps([])),
                MagicMock(returncode=0, stdout=json.dumps([])),
                MagicMock(returncode=0, stdout=json.dumps([root, reply])),
            ]
            findings = probe._get_review_findings()

        assert len(findings) == 1
        assert findings[0]["file"] == "a.py"

    def test_dedupe_merges_sources(self, probe):
        """Same file+line reported by two sources -> one finding, sources
        joined."""
        cr = {
            "id": 20,
            "user": {"login": "coderabbitai[bot]"},
            "path": "app/x.py",
            "body": "🟡 Unused variable",
        }
        riptide_body = (
            "## Review: 1 warning(s).\n\n| | Finding | File |\n|---|---|---|\n"
            "| 🟡 | Unused variable | `app/x.py` |"
        )
        ic = {"id": 21, "user": {"login": "riptide-review[bot]"}, "body": riptide_body}
        with patch("riptide.pipeline.probe.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps([])),
                MagicMock(returncode=0, stdout=json.dumps([ic])),
                MagicMock(returncode=0, stdout=json.dumps([cr])),
            ]
            findings = probe._get_review_findings()

        assert len(findings) == 1
        assert "coderabbitai" in findings[0]["source"]
        assert "riptide-review" in findings[0]["source"]

    def test_cap_at_fifteen(self, probe):
        """21 findings -> 15, truncation noted on the last."""
        crs = [
            {
                "id": 100 + i,
                "user": {"login": "coderabbitai[bot]"},
                "path": f"f{i}.py",
                "line": 1,
                "body": f"🟡 Finding number {i}",
            }
            for i in range(21)
        ]
        with patch("riptide.pipeline.probe.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps([])),
                MagicMock(returncode=0, stdout=json.dumps([])),
                MagicMock(returncode=0, stdout=json.dumps(crs)),
            ]
            findings = probe._get_review_findings()

        assert len(findings) == 15
        assert "truncated" in findings[-1]["body"]
        assert "6 more" in findings[-1]["body"]

    def test_hermes_ack_skipped(self, probe):
        """The fixer's '🤖 Hermes review' ack comment is not a finding."""
        ack = {"id": 30, "user": {"login": "ChonSong"}, "body": "🤖 Hermes review · deepseek"}
        with patch("riptide.pipeline.probe.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps([])),
                MagicMock(returncode=0, stdout=json.dumps([ack])),
                MagicMock(returncode=0, stdout=json.dumps([])),
            ]
            findings = probe._get_review_findings()

        assert findings == []

    def test_gather_includes_review_findings(self, probe):
        """gather() exposes review_findings."""
        with patch.object(probe, "_get_review_findings", return_value=[{"body": "x"}]):
            with patch.object(probe, "_get_pr_data", return_value={}), \
                 patch.object(probe, "_get_pr_files", return_value=[]), \
                 patch.object(probe, "_run_diff_analyzer", return_value={}), \
                 patch.object(probe, "_run_context_bundle", return_value={}), \
                 patch.object(probe, "_run_graphify", return_value={}), \
                 patch.object(probe, "_get_previous_findings", return_value=[]), \
                 patch.object(probe, "_extract_key_facts", return_value={}):
                result = probe.gather()
        assert "review_findings" in result
        assert result["review_findings"] == [{"body": "x"}]
