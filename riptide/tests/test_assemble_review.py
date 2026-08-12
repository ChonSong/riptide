# riptide/tests/test_assemble_review.py
"""
Tests for assemble_review module — structured findings → review comment.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from riptide.assemble_review import (
    assemble_review_body,
    validate_findings,
    post_review,
)


# ── Assembly Tests ──────────────────────────────────────────────────────────


class TestAssembleReviewBody:
    """Tests for assemble_review_body()."""

    def test_empty_findings_clean_pr(self):
        body = assemble_review_body([], "ChonSong", "riptide", 42)
        assert "Clean PR" in body
        assert "Riptide Review via Hermes" in body

    def test_signoff_includes_model_provider(self):
        body = assemble_review_body(
            [], "ChonSong", "riptide", 42,
            model="custom:LongCat-2.0", provider="custom",
        )
        assert "model: `custom:LongCat-2.0`" in body
        assert "provider: `custom`" in body

    def test_signoff_model_only(self):
        body = assemble_review_body(
            [], "ChonSong", "riptide", 42, model="deepseek-v4-flash",
        )
        assert "model: `deepseek-v4-flash`" in body
        assert "provider" not in body

    def test_signoff_provider_only(self):
        body = assemble_review_body(
            [], "ChonSong", "riptide", 42, provider="opencode-go",
        )
        assert "provider: `opencode-go`" in body
        assert "model" not in body

    def test_signoff_neither(self):
        body = assemble_review_body([], "ChonSong", "riptide", 42)
        assert "<sub>Riptide Review via Hermes</sub>" in body
        assert "model" not in body
        assert "provider" not in body

    def test_findings_table_rendered(self):
        findings = [
            {"severity": "warning", "title": "Issue 1", "detail": "Detail 1", "file": "a.py", "line": 10},
            {"severity": "critical", "title": "Issue 2", "detail": "Detail 2", "file": "b.py", "line": 20},
        ]
        body = assemble_review_body(findings, "ChonSong", "riptide", 42)
        assert "| 🟡 warning |" in body
        assert "| 🔴 critical |" in body
        assert "`a.py`" in body
        assert "`b.py`" in body
        assert "Issue 1" in body
        assert "Issue 2" in body

    def test_diagram_url_included(self):
        body = assemble_review_body([], "ChonSong", "riptide", 42, diagram_url="https://example.com/diagram")
        assert "https://example.com/diagram" in body

    def test_no_diagram_url_placeholder(self):
        body = assemble_review_body([], "ChonSong", "riptide", 42)
        assert "No diagram" in body

    def test_next_steps_from_findings(self):
        findings = [
            {"severity": "warning", "title": "Fix error handling", "detail": "...", "file": "a.py", "line": 1},
            {"severity": "suggestion", "title": "Use pathlib", "detail": "...", "file": "b.py", "line": 2},
        ]
        body = assemble_review_body(findings, "ChonSong", "riptide", 42)
        assert "Fix error handling" in body
        assert "Use pathlib" in body

    def test_detail_rendered(self):
        findings = [
            {"severity": "warning", "title": "Issue", "detail": "This is a detailed explanation.", "file": "a.py", "line": 1},
        ]
        body = assemble_review_body(findings, "ChonSong", "riptide", 42)
        assert "This is a detailed explanation." in body

    def test_summary_count(self):
        findings = [
            {"severity": "warning", "title": "A", "detail": "", "file": "", "line": ""},
            {"severity": "critical", "title": "B", "detail": "", "file": "", "line": ""},
            {"severity": "suggestion", "title": "C", "detail": "", "file": "", "line": ""},
        ]
        body = assemble_review_body(findings, "ChonSong", "riptide", 42)
        # 2 critical/warning → "2 issue(s) found"
        assert "2 issue(s) found" in body

    def test_no_file_ref_shows_dash(self):
        findings = [
            {"severity": "info", "title": "General note", "detail": "...", "file": "", "line": ""},
        ]
        body = assemble_review_body(findings, "ChonSong", "riptide", 42)
        assert "—" in body  # dash for missing file


# ── Validation Tests ────────────────────────────────────────────────────────


class TestValidateFindings:
    """Tests for validate_findings()."""

    def test_valid_findings(self):
        errors = validate_findings([
            {"severity": "warning", "title": "Issue", "detail": "..."},
        ])
        assert errors == []

    def test_missing_severity(self):
        errors = validate_findings([{"title": "Issue"}])
        assert any("severity" in e for e in errors)

    def test_missing_title(self):
        errors = validate_findings([{"severity": "warning"}])
        assert any("title" in e for e in errors)

    def test_invalid_severity(self):
        errors = validate_findings([{"severity": "banana", "title": "X"}])
        assert any("invalid severity" in e for e in errors)

    def test_not_a_dict(self):
        errors = validate_findings(["not a dict"])  # type: ignore[list-item]
        assert any("must be a dict" in e for e in errors)

    def test_multiple_errors(self):
        errors = validate_findings([
            {"title": "no severity"},
            {"severity": "bad", "title": "bad severity"},
        ])
        assert len(errors) >= 2


# ── Post Review Tests ───────────────────────────────────────────────────────


class TestPostReview:
    """Tests for post_review()."""

    def test_post_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            assert post_review("ChonSong", "riptide", 42, "body") is True

    def test_post_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="fail")
            assert post_review("ChonSong", "riptide", 42, "body") is False

    def test_post_exception(self):
        with patch("subprocess.run", side_effect=OSError("gh not found")):
            assert post_review("ChonSong", "riptide", 42, "body") is False


# ── CLI Tests ────────────────────────────────────────────────────────────────


class TestCLI:
    """Tests for the CLI entry point."""

    def test_dry_run_outputs_body(self, tmp_path, capsys):
        findings = [{"severity": "warning", "title": "X", "detail": "Y", "file": "a.py", "line": 1}]
        findings_file = tmp_path / "findings.json"
        findings_file.write_text(json.dumps(findings))

        with patch("sys.argv", [
            "assemble_review",
            "--findings", str(findings_file),
            "--owner", "ChonSong",
            "--repo", "riptide",
            "--pr", "42",
            "--dry-run",
        ]):
            from riptide.assemble_review import main
            main()

        captured = capsys.readouterr()
        assert "X" in captured.out
        assert "Y" in captured.out

    def test_missing_findings_file(self, capsys):
        with patch("sys.argv", [
            "assemble_review",
            "--findings", "/nonexistent/findings.json",
            "--owner", "ChonSong",
            "--repo", "riptide",
            "--pr", "42",
        ]):
            from riptide.assemble_review import main
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_invalid_json(self, tmp_path, capsys):
        findings_file = tmp_path / "findings.json"
        findings_file.write_text("{invalid json")

        with patch("sys.argv", [
            "assemble_review",
            "--findings", str(findings_file),
            "--owner", "ChonSong",
            "--repo", "riptide",
            "--pr", "42",
        ]):
            from riptide.assemble_review import main
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_validation_failure(self, tmp_path, capsys):
        findings = [{"title": "no severity field"}]
        findings_file = tmp_path / "findings.json"
        findings_file.write_text(json.dumps(findings))

        with patch("sys.argv", [
            "assemble_review",
            "--findings", str(findings_file),
            "--owner", "ChonSong",
            "--repo", "riptide",
            "--pr", "42",
        ]):
            from riptide.assemble_review import main
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
