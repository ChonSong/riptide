#!/usr/bin/env python3
"""tests/test_diagram_analyst.py — Tests for riptide/diagram_analyst.py."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from riptide.diagram_analyst import (
    _build_annotations,
    _build_context_bundle,
    _build_narrative,
    _build_pr_data,
    _compute_confidence,
    _identify_gaps,
    _upload_diagram,
    generate_diagram,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


SAMPLE_FINDINGS = [
    {
        "severity": "critical",
        "title": "SQL injection risk",
        "file": "app.py",
        "line": 42,
        "detail": "Use parameterized queries",
    },
    {
        "severity": "warning",
        "title": "Missing error handling",
        "file": "utils.py",
        "line": 17,
        "detail": "Add try/except for network call",
    },
    {
        "severity": "suggestion",
        "title": "Use f-string instead of format()",
        "file": "helpers.py",
        "line": 88,
    },
]


@pytest.fixture
def findings():
    return [dict(f) for f in SAMPLE_FINDINGS]


@pytest.fixture
def pr_data():
    return {
        "owner": "ChonSong",
        "repo": "riptide",
        "pr_number": 42,
        "pr_title": "feat: add new feature",
        "pr_author": "ChonSong",
        "total_loc": 150,
    }


# ── _build_pr_data ────────────────────────────────────────────────────────────


class TestBuildPrData:
    def test_build_pr_data_basic(self):
        result = _build_pr_data("owner", "repo", 123, "Test PR", "user", 100)
        assert result["title"] == "Test PR"
        assert result["number"] == 123
        assert result["repo"] == "owner/repo"
        assert result["author"]["login"] == "user"
        assert result["loc"] == 100
        assert result["status"] == "open"

    def test_build_pr_data_empty_title(self):
        result = _build_pr_data("owner", "repo", 123, "", "user", 0)
        assert result["title"] == "PR #123"

    def test_build_pr_data_empty_author(self):
        result = _build_pr_data("owner", "repo", 123, "Test", "", 0)
        assert result["author"]["login"] == ""


# ── _build_context_bundle ──────────────────────────────────────────────────────


class TestBuildContextBundle:
    def test_build_context_bundle_basic(self, findings):
        result = _build_context_bundle(findings)
        assert "aggregate" in result
        assert "verdict" in result
        assert "concepts" in result

    def test_verdict_block_for_critical(self, findings):
        result = _build_context_bundle(findings)
        assert result["verdict"] == "block"

    def test_verdict_review_for_warning(self):
        warning_findings = [
            {"severity": "warning", "title": "test", "file": "a.py", "line": 1}
        ]
        result = _build_context_bundle(warning_findings)
        assert result["verdict"] == "review"

    def test_verdict_pass_for_info_only(self):
        info_findings = [
            {"severity": "info", "title": "test", "file": "a.py", "line": 1}
        ]
        result = _build_context_bundle(info_findings)
        assert result["verdict"] == "pass"

    def test_concepts_extract_unique_files(self, findings):
        result = _build_context_bundle(findings)
        filenames = [c["filename"] for c in result["concepts"]]
        assert "app.py" in filenames
        assert "utils.py" in filenames
        assert len(filenames) == 3

    def test_empty_findings(self):
        result = _build_context_bundle([])
        assert result["concepts"] == []
        assert result["aggregate"]["files_count"] == 0


# ── _build_narrative ───────────────────────────────────────────────────────────


class TestBuildNarrative:
    def test_narrative_structure(self, findings, pr_data):
        result = _build_narrative(
            findings,
            pr_data["owner"],
            pr_data["repo"],
            pr_data["pr_number"],
            pr_data["pr_title"],
            pr_data["pr_author"],
            pr_data["total_loc"],
        )
        assert "summary" in result
        assert "severity_breakdown" in result
        assert "files_affected" in result
        assert result["findings_count"] == 3

    def test_narrative_severity_breakdown(self, findings, pr_data):
        result = _build_narrative(
            findings,
            pr_data["owner"],
            pr_data["repo"],
            pr_data["pr_number"],
            pr_data["pr_title"],
            pr_data["pr_author"],
            pr_data["total_loc"],
        )
        assert result["severity_breakdown"]["critical"] == 1
        assert result["severity_breakdown"]["warning"] == 1
        assert result["severity_breakdown"]["suggestion"] == 1

    def test_narrative_files_affected(self, findings, pr_data):
        result = _build_narrative(
            findings,
            pr_data["owner"],
            pr_data["repo"],
            pr_data["pr_number"],
            pr_data["pr_title"],
            pr_data["pr_author"],
            pr_data["total_loc"],
        )
        assert len(result["files_affected"]) == 3
        assert "app.py" in result["files_affected"]

    def test_narrative_empty_findings(self, pr_data):
        result = _build_narrative(
            [],
            pr_data["owner"],
            pr_data["repo"],
            pr_data["pr_number"],
            pr_data["pr_title"],
            pr_data["pr_author"],
            pr_data["total_loc"],
        )
        assert result["findings_count"] == 0
        assert "no issues" in result["summary"]


# ── _build_annotations ─────────────────────────────────────────────────────────


class TestBuildAnnotations:
    def test_annotations_map_to_findings(self, findings):
        result = _build_annotations(findings)
        assert len(result) == 3
        assert result[0]["severity"] == "critical"
        assert result[0]["element_id"] == "finding_0"

    def test_annotations_structure(self, findings):
        result = _build_annotations(findings)
        for ann in result:
            assert "index" in ann
            assert "severity" in ann
            assert "title" in ann
            assert "file" in ann
            assert "line" in ann
            assert "element_id" in ann

    def test_annotations_empty(self):
        assert _build_annotations([]) == []

    def test_annotations_index_sequential(self, findings):
        result = _build_annotations(findings)
        for i, ann in enumerate(result):
            assert ann["index"] == i


# ── _compute_confidence ────────────────────────────────────────────────────────


class TestComputeConfidence:
    def test_confidence_empty_findings(self):
        assert _compute_confidence([]) == 0.5

    def test_confidence_with_quality_findings(self):
        findings = [
            {"severity": "critical", "title": "test", "file": "a.py", "line": 1, "detail": "d"}
        ]
        score = _compute_confidence(findings)
        assert score > 0.5

    def test_confidence_capped_at_one(self):
        findings = [
            {"severity": "critical", "title": f"test{i}", "file": f"f{i}.py", "line": i, "detail": f"d{i}"}
            for i in range(20)
        ]
        score = _compute_confidence(findings)
        assert score <= 1.0

    def test_confidence_minimum_half(self):
        findings = [{"severity": "info", "title": "test"}]
        score = _compute_confidence(findings)
        assert score >= 0.5


# ── _identify_gaps ─────────────────────────────────────────────────────────────


class TestIdentifyGaps:
    def test_gaps_empty_findings(self):
        gaps = _identify_gaps([])
        assert len(gaps) > 0
        assert any("No findings" in g for g in gaps)

    def test_gaps_missing_file(self):
        findings = [{"severity": "critical", "title": "test", "line": 1, "detail": "d"}]
        gaps = _identify_gaps(findings)
        assert any("file references" in g for g in gaps)

    def test_gaps_missing_line(self):
        findings = [{"severity": "critical", "title": "test", "file": "a.py", "detail": "d"}]
        gaps = _identify_gaps(findings)
        assert any("line numbers" in g for g in gaps)

    def test_gaps_missing_detail(self):
        findings = [{"severity": "critical", "title": "test", "file": "a.py", "line": 1}]
        gaps = _identify_gaps(findings)
        assert any("detailed descriptions" in g for g in gaps)

    def test_no_gaps_with_complete_findings(self):
        findings = [{"severity": "critical", "title": "test", "file": "a.py", "line": 1, "detail": "d"}]
        gaps = _identify_gaps(findings)
        assert len(gaps) == 0

    def test_gaps_missing_severity(self):
        findings = [{"title": "test", "file": "a.py", "line": 1, "detail": "d"}]
        gaps = _identify_gaps(findings)
        assert any("missing severity" in g for g in gaps)


# ── _upload_diagram ────────────────────────────────────────────────────────────


class TestUploadDiagram:
    def test_upload_no_scripts_available(self):
        with patch("riptide.diagram_analyst.Path") as mock_path:
            mock_path.return_value.__truediv__.return_value.__truediv__.return_value.exists.return_value = False
            mock_path.return_value.__truediv__.return_value.__truediv__.return_value.__truediv__.return_value.__truediv__.return_value.__truediv__.return_value.exists.return_value = False
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = json.dumps({"id": "test123"}).encode()
                mock_urlopen.return_value.__enter__ = lambda s: mock_resp
                mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

                result = _upload_diagram("/tmp/fake.excalidraw")
                assert result == "https://excalidraw.com/#json=test123"

    def test_upload_api_failure(self):
        with patch("riptide.diagram_analyst.Path") as mock_path:
            mock_path.return_value.__truediv__.return_value.__truediv__.return_value.exists.return_value = False
            with patch("urllib.request.urlopen", side_effect=Exception("API down")):
                result = _upload_diagram("/tmp/fake.excalidraw")
                assert result is None


# ── generate_diagram ───────────────────────────────────────────────────────────


class TestGenerateDiagram:
    def test_generate_diagram_empty_findings(self, pr_data):
        result = generate_diagram(
            findings=[],
            owner=pr_data["owner"],
            repo=pr_data["repo"],
            pr_number=pr_data["pr_number"],
            pr_title=pr_data["pr_title"],
            pr_author=pr_data["pr_author"],
            total_loc=pr_data["total_loc"],
        )
        assert result is None

    def test_generate_diagram_success(self, findings, pr_data, tmp_path):
        output_path = str(tmp_path / "diagram.excalidraw")

        with patch("riptide.diagram_analyst._upload_diagram", return_value="https://excalidraw.com/#json=abc123"):
            result = generate_diagram(
                findings=findings,
                owner=pr_data["owner"],
                repo=pr_data["repo"],
                pr_number=pr_data["pr_number"],
                pr_title=pr_data["pr_title"],
                pr_author=pr_data["pr_author"],
                total_loc=pr_data["total_loc"],
                output_path=output_path,
            )

        assert result is not None
        assert "diagram_url" in result
        assert "narrative" in result
        assert "confidence" in result
        assert "gaps" in result
        assert "annotations" in result
        assert result["diagram_url"] == "https://excalidraw.com/#json=abc123"

    def test_generate_diagram_upload_fails(self, findings, pr_data, tmp_path):
        output_path = str(tmp_path / "diagram.excalidraw")

        with patch("riptide.diagram_analyst._upload_diagram", return_value=None):
            result = generate_diagram(
                findings=findings,
                owner=pr_data["owner"],
                repo=pr_data["repo"],
                pr_number=pr_data["pr_number"],
                pr_title=pr_data["pr_title"],
                pr_author=pr_data["pr_author"],
                total_loc=pr_data["total_loc"],
                output_path=output_path,
            )

        assert result is not None
        assert result["diagram_url"].startswith("file://")

    

    def test_generate_diagram_creates_file(self, findings, pr_data, tmp_path):
        output_path = str(tmp_path / "diagram.excalidraw")

        with patch("riptide.diagram_analyst._upload_diagram", return_value="https://excalidraw.com/#json=xyz"):
            generate_diagram(
                findings=findings,
                owner=pr_data["owner"],
                repo=pr_data["repo"],
                pr_number=pr_data["pr_number"],
                pr_title=pr_data["pr_title"],
                pr_author=pr_data["pr_author"],
                total_loc=pr_data["total_loc"],
                output_path=output_path,
            )

        assert Path(output_path).exists()
        content = json.loads(Path(output_path).read_text())
        assert content["type"] == "excalidraw"
        assert "elements" in content

    def test_generate_diagram_narrative_content(self, findings, pr_data, tmp_path):
        output_path = str(tmp_path / "diagram.excalidraw")

        with patch("riptide.diagram_analyst._upload_diagram", return_value="https://excalidraw.com/#json=test"):
            result = generate_diagram(
                findings=findings,
                owner=pr_data["owner"],
                repo=pr_data["repo"],
                pr_number=pr_data["pr_number"],
                pr_title=pr_data["pr_title"],
                pr_author=pr_data["pr_author"],
                total_loc=pr_data["total_loc"],
                output_path=output_path,
            )

        narrative = result["narrative"]
        assert narrative["title"] == pr_data["pr_title"]
        assert narrative["author"] == pr_data["pr_author"]
        assert narrative["repo"] == f"{pr_data['owner']}/{pr_data['repo']}"
        assert narrative["findings_count"] == 3

    def test_generate_diagram_annotations_count(self, findings, pr_data, tmp_path):
        output_path = str(tmp_path / "diagram.excalidraw")

        with patch("riptide.diagram_analyst._upload_diagram", return_value="https://excalidraw.com/#json=test"):
            result = generate_diagram(
                findings=findings,
                owner=pr_data["owner"],
                repo=pr_data["repo"],
                pr_number=pr_data["pr_number"],
                pr_title=pr_data["pr_title"],
                pr_author=pr_data["pr_author"],
                total_loc=pr_data["total_loc"],
                output_path=output_path,
            )

        assert len(result["annotations"]) == 3

    def test_generate_diagram_confidence_range(self, findings, pr_data, tmp_path):
        output_path = str(tmp_path / "diagram.excalidraw")

        with patch("riptide.diagram_analyst._upload_diagram", return_value="https://excalidraw.com/#json=test"):
            result = generate_diagram(
                findings=findings,
                owner=pr_data["owner"],
                repo=pr_data["repo"],
                pr_number=pr_data["pr_number"],
                pr_title=pr_data["pr_title"],
                pr_author=pr_data["pr_author"],
                total_loc=pr_data["total_loc"],
                output_path=output_path,
            )

        assert 0.0 <= result["confidence"] <= 1.0

    def test_generate_diagram_with_defaults(self, findings, tmp_path):
        output_path = str(tmp_path / "diagram.excalidraw")

        with patch("riptide.diagram_analyst._upload_diagram", return_value="https://excalidraw.com/#json=test"):
            result = generate_diagram(
                findings=findings,
                owner="owner",
                repo="repo",
                pr_number=1,
                output_path=output_path,
            )

        assert result is not None
        assert result["narrative"]["title"] == ""
        assert result["narrative"]["author"] == ""

    def test_generate_diagram_single_finding(self, pr_data, tmp_path):
        output_path = str(tmp_path / "diagram.excalidraw")
        single = [SAMPLE_FINDINGS[0]]

        with patch("riptide.diagram_analyst._upload_diagram", return_value="https://excalidraw.com/#json=test"):
            result = generate_diagram(
                findings=single,
                owner=pr_data["owner"],
                repo=pr_data["repo"],
                pr_number=pr_data["pr_number"],
                pr_title=pr_data["pr_title"],
                pr_author=pr_data["pr_author"],
                total_loc=pr_data["total_loc"],
                output_path=output_path,
            )

        assert result is not None
        assert result["narrative"]["findings_count"] == 1
        assert len(result["annotations"]) == 1

    def test_generate_diagram_many_findings(self, pr_data, tmp_path):
        output_path = str(tmp_path / "diagram.excalidraw")
        many = [
            {"severity": "warning", "title": f"issue {i}", "file": f"f{i}.py", "line": i}
            for i in range(20)
        ]

        with patch("riptide.diagram_analyst._upload_diagram", return_value="https://excalidraw.com/#json=test"):
            result = generate_diagram(
                findings=many,
                owner=pr_data["owner"],
                repo=pr_data["repo"],
                pr_number=pr_data["pr_number"],
                pr_title=pr_data["pr_title"],
                pr_author=pr_data["pr_author"],
                total_loc=pr_data["total_loc"],
                output_path=output_path,
            )

        assert result is not None
        assert result["narrative"]["findings_count"] == 20
        assert len(result["annotations"]) == 20

    def test_generate_diagram_temp_output(self, findings, pr_data):
        """When output_path is None, a temp file should be used."""
        with patch("riptide.diagram_analyst._upload_diagram", return_value="https://excalidraw.com/#json=test"):
            result = generate_diagram(
                findings=findings,
                owner=pr_data["owner"],
                repo=pr_data["repo"],
                pr_number=pr_data["pr_number"],
                pr_title=pr_data["pr_title"],
                pr_author=pr_data["pr_author"],
                total_loc=pr_data["total_loc"],
            )

        assert result is not None


# ── main() CLI ─────────────────────────────────────────────────────────────────


class TestMain:
    def test_main_missing_file(self, capsys):
        with patch("sys.argv", ["diagram_analyst", "--findings", "/nonexistent.json", "--owner", "o", "--repo", "r", "--pr", "1"]):
            result = __import__("riptide.diagram_analyst").diagram_analyst.main()
            assert result == 1

    def test_main_invalid_json(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json")

        with patch("sys.argv", ["diagram_analyst", "--findings", str(bad_file), "--owner", "o", "--repo", "r", "--pr", "1"]):
            with pytest.raises(json.JSONDecodeError):
                __import__("riptide.diagram_analyst").diagram_analyst.main()

    def test_main_not_a_list(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(json.dumps({"not": "a list"}))

        with patch("sys.argv", ["diagram_analyst", "--findings", str(bad_file), "--owner", "o", "--repo", "r", "--pr", "1"]):
            result = __import__("riptide.diagram_analyst").diagram_analyst.main()
            assert result == 1

    def test_main_success_output_file(self, tmp_path):
        findings_file = tmp_path / "findings.json"
        findings_file.write_text(json.dumps(SAMPLE_FINDINGS))
        output_file = tmp_path / "output.json"

        with patch("sys.argv", [
            "diagram_analyst",
            "--findings", str(findings_file),
            "--owner", "ChonSong",
            "--repo", "riptide",
            "--pr", "42",
            "--title", "test",
            "--author", "user",
            "--loc", "100",
            "--output", str(output_file),
        ]):
            with patch("riptide.diagram_analyst._upload_diagram", return_value="https://excalidraw.com/#json=test"):
                result = __import__("riptide.diagram_analyst").diagram_analyst.main()
                assert result == 0

        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert "diagram_url" in data
        assert "narrative" in data

    def test_main_success_stdout(self, tmp_path, capsys):
        findings_file = tmp_path / "findings.json"
        findings_file.write_text(json.dumps(SAMPLE_FINDINGS))

        with patch("sys.argv", [
            "diagram_analyst",
            "--findings", str(findings_file),
            "--owner", "ChonSong",
            "--repo", "riptide",
            "--pr", "42",
        ]):
            with patch("riptide.diagram_analyst._upload_diagram", return_value="https://excalidraw.com/#json=test"):
                result = __import__("riptide.diagram_analyst").diagram_analyst.main()
                assert result == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "diagram_url" in data