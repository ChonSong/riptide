#!/usr/bin/env python3
"""Tests for ADHD-friendly review output formatting."""

import pytest
from datetime import datetime, timezone, timedelta
from riptide.assemble_review import (
    assemble_review_body,
    validate_findings,
    MAX_VISIBLE_FINDINGS,
    SEVERITY_TIME_ESTIMATES,
    _build_verdict,
    _build_numbered_findings,
    _format_finding,
    _get_time_estimate,
    _compute_total_time,
    _build_next_action,
    _format_file_ref,
    _compute_elapsed,
    _format_elapsed,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def critical_finding():
    return {
        "severity": "critical",
        "title": "Race condition",
        "detail": "work queue claim races on restart",
        "file": "webhook.py",
        "line": 344,
    }


@pytest.fixture
def warning_finding():
    return {
        "severity": "warning",
        "title": "Missing retry",
        "detail": "spawn failure silently drops",
        "file": "deepthink.py",
        "line": 217,
    }


@pytest.fixture
def suggestion_finding():
    return {
        "severity": "suggestion",
        "title": "Add type hints",
        "detail": "improves readability",
        "file": "utils.py",
        "line": 42,
    }


@pytest.fixture
def multi_step_finding():
    return {
        "severity": "critical",
        "title": "DB lock contention",
        "detail": "reserve_job deadlocks under load",
        "file": "state.py",
        "line": 475,
        "actions": [
            "Add timeout to reserve_job",
            "Add retry with backoff",
            "Add deadlock detection test",
        ],
    }


@pytest.fixture
def many_findings():
    """Generate 8 findings (more than MAX_VISIBLE_FINDINGS)."""
    return [
        {"severity": "warning", "title": f"Issue {i}", "detail": f"detail {i}", "file": f"file{i}.py", "line": i * 10}
        for i in range(1, 9)
    ]


# ── Verdict-first format ───────────────────────────────────────────────────

class TestVerdictFirst:
    """Test that the verdict line leads the output."""

    def test_verdict_starts_with_count(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert body.startswith("1 critical")

    def test_verdict_includes_warning_count(self, critical_finding, warning_finding):
        body = assemble_review_body([critical_finding, warning_finding], "ChonSong", "riptide", 1)
        assert "1 critical, 1 warning(s)" in body

    def test_verdict_includes_fix_directive(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert "Fix `webhook.py:344` first" in body

    def test_verdict_lowercases_reason(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert "race condition." in body

    def test_verdict_only_suggestions(self, suggestion_finding):
        body = assemble_review_body([suggestion_finding], "ChonSong", "riptide", 1)
        assert "No critical issues or warnings" in body


# ── Findings cap at 5 ──────────────────────────────────────────────────────

class TestFindingsCap:
    """Test that findings are capped at 5 visible with <details> for remainder."""

    def test_five_or_less_no_details(self, warning_finding):
        findings = [
            {"severity": "warning", "title": f"Issue {i}", "detail": "d", "file": "f.py", "line": i}
            for i in range(1, 5)
        ]
        body = assemble_review_body(findings, "ChonSong", "riptide", 1)
        assert "<details>" not in body

    def test_six_findings_has_details(self, warning_finding):
        findings = [
            {"severity": "warning", "title": f"Issue {i}", "detail": "d", "file": "f.py", "line": i}
            for i in range(1, 7)
        ]
        body = assemble_review_body(findings, "ChonSong", "riptide", 1)
        assert "<details>" in body
        assert "Additional findings (1)" in body

    def test_eight_findings_details_count(self, many_findings):
        body = assemble_review_body(many_findings, "ChonSong", "riptide", 1)
        assert "Additional findings (3)" in body

    def test_details_contains_remainder(self, many_findings):
        body = assemble_review_body(many_findings, "ChonSong", "riptide", 1)
        assert "6. **Issue 6**" in body
        assert "7. **Issue 7**" in body
        assert "8. **Issue 8**" in body

    def test_visible_findings_numbered_1_to_5(self, many_findings):
        body = assemble_review_body(many_findings, "ChonSong", "riptide", 1)
        for i in range(1, 6):
            assert f"{i}. **Issue {i}**" in body

    def test_details_properly_closed(self, many_findings):
        body = assemble_review_body(many_findings, "ChonSong", "riptide", 1)
        assert "</details>" in body


# ── Time estimates ─────────────────────────────────────────────────────────

class TestTimeEstimates:
    """Test that time estimates appear after each finding."""

    def test_critical_has_5min(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert "~5min" in body

    def test_warning_has_2min(self, warning_finding):
        body = assemble_review_body([warning_finding], "ChonSong", "riptide", 1)
        assert "~2min" in body

    def test_suggestion_has_1min(self, suggestion_finding):
        body = assemble_review_body([suggestion_finding], "ChonSong", "riptide", 1)
        assert "~1min" in body

    def test_custom_time_override(self, critical_finding):
        body = assemble_review_body(
            [critical_finding], "ChonSong", "riptide", 1,
            time_estimates={"Race condition": "~10min"}
        )
        assert "~10min" in body

    def test_time_after_finding_text(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        # Time should appear after the finding detail
        lines = body.split("\n")
        finding_line = [l for l in lines if "Race condition" in l][0]
        assert "~5min" in finding_line


# ── Numbered multi-step actions ───────────────────────────────────────────

class TestNumberedActions:
    """Test that multi-step fixes are numbered."""

    def test_multi_step_numbered(self, multi_step_finding):
        body = assemble_review_body([multi_step_finding], "ChonSong", "riptide", 1)
        assert "1. Add timeout to reserve_job" in body
        assert "2. Add retry with backoff" in body
        assert "3. Add deadlock detection test" in body

    def test_actions_indented(self, multi_step_finding):
        body = assemble_review_body([multi_step_finding], "ChonSong", "riptide", 1)
        lines = body.split("\n")
        action_lines = [l for l in lines if l.strip().startswith("1. Add timeout")]
        assert len(action_lines) == 1
        assert action_lines[0].startswith("   1.")

    def test_no_actions_no_numbered_list(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert "1. Add" not in body


# ── Single next action footer ─────────────────────────────────────────────

class TestNextAction:
    """Test that the footer has a single next action."""

    def test_next_action_present(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert "Next: Fix" in body

    def test_next_action_includes_file(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert "in `webhook.py:344`" in body

    def test_next_action_includes_time(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert "(~5min" in body

    def test_next_action_total_time(self, critical_finding, warning_finding):
        body = assemble_review_body([critical_finding, warning_finding], "ChonSong", "riptide", 1)
        assert "total ~7min" in body

    def test_next_action_clean_pr(self):
        body = assemble_review_body([], "ChonSong", "riptide", 1)
        assert "Next: Merge when ready" in body


# ── No preamble headers ───────────────────────────────────────────────────

class TestNoPreamble:
    """Test that preamble headers are removed."""

    def test_no_summary_header(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert "## 🎯 Summary" not in body
        assert "## Summary" not in body

    def test_no_findings_header(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert "## 🔍 Findings" not in body

    def test_no_next_steps_header(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert "## 📌 Next Steps" not in body

    def test_no_diagram_header(self, critical_finding):
        body = assemble_review_body(
            [critical_finding], "ChonSong", "riptide", 1,
            diagram_url="https://example.com/diagram"
        )
        assert "## 🔗 Diagram" not in body

    def test_diagram_link_present(self, critical_finding):
        body = assemble_review_body(
            [critical_finding], "ChonSong", "riptide", 1,
            diagram_url="https://example.com/diagram"
        )
        assert "[Diagram](https://example.com/diagram)" in body


# ── Success message for clean PRs ─────────────────────────────────────────

class TestSuccessMessage:
    """Test success message when no findings."""

    def test_clean_pr_success(self):
        body = assemble_review_body([], "ChonSong", "riptide", 1)
        assert "✅" in body
        assert "No critical or warning findings" in body
        assert "Ready to merge" in body

    def test_clean_pr_no_next_action(self):
        body = assemble_review_body([], "ChonSong", "riptide", 1)
        assert "Next: Merge when ready" in body


# ── Error message format ──────────────────────────────────────────────────

class TestErrorMessages:
    """Test matter-of-fact error messages."""

    def test_validation_error_no_exclamation(self):
        errors = validate_findings([{"severity": "bad", "title": "x"}])
        assert len(errors) == 1
        assert "!" not in errors[0]

    def test_validation_error_lists_valid(self):
        errors = validate_findings([{"severity": "bad", "title": "x"}])
        assert "must be one of" in errors[0]

    def test_missing_severity_error(self):
        errors = validate_findings([{"title": "x"}])
        assert any("missing 'severity'" in e for e in errors)

    def test_missing_title_error(self):
        errors = validate_findings([{"severity": "critical"}])
        assert any("missing 'title'" in e for e in errors)


# ── Backward compatibility ────────────────────────────────────────────────

class TestBackwardCompatibility:
    """Test that old kwargs still work."""

    def test_old_positional_args(self, critical_finding):
        body = assemble_review_body(
            [critical_finding], "ChonSong", "riptide", 1,
            None, "model", "provider"
        )
        assert "Race condition" in body

    def test_old_triggered_at(self, critical_finding):
        now = datetime.now(timezone.utc)
        triggered = (now - timedelta(seconds=5)).isoformat()
        body = assemble_review_body(
            [critical_finding], "ChonSong", "riptide", 1,
            triggered_at=triggered, model="LongCat-2.0", provider="custom"
        )
        assert "⏱️ Review posted in" in body

    def test_old_pr_created_at_fallback(self, critical_finding):
        body = assemble_review_body(
            [critical_finding], "ChonSong", "riptide", 1,
            pr_created_at="2026-08-13T00:00:00+00:00",
            model="LongCat-2.0", provider="custom"
        )
        assert "⏱️ Review posted in" in body
        assert "since PR opened" in body

    def test_no_timing_info(self, critical_finding):
        body = assemble_review_body(
            [critical_finding], "ChonSong", "riptide", 1,
            model="LongCat-2.0", provider="custom"
        )
        assert "⏱️" not in body


# ── Sign-off format ───────────────────────────────────────────────────────

class TestSignoff:
    """Test sign-off line format."""

    def test_signoff_present(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert "Riptide Review" in body

    def test_signoff_with_model(self, critical_finding):
        body = assemble_review_body(
            [critical_finding], "ChonSong", "riptide", 1,
            model="LongCat-2.0"
        )
        assert "model: `LongCat-2.0`" in body

    def test_signoff_with_timing(self, critical_finding):
        now = datetime.now(timezone.utc)
        triggered = (now - timedelta(seconds=5)).isoformat()
        body = assemble_review_body(
            [critical_finding], "ChonSong", "riptide", 1,
            triggered_at=triggered
        )
        assert "⏱️" in body


# ── Helper function tests ─────────────────────────────────────────────────

class TestHelpers:
    """Test internal helper functions."""

    def test_format_file_ref_with_line(self):
        ref = _format_file_ref({"file": "a.py", "line": 42})
        assert ref == "a.py:42"

    def test_format_file_ref_no_line(self):
        ref = _format_file_ref({"file": "a.py"})
        assert ref == "a.py"

    def test_format_file_ref_empty(self):
        ref = _format_file_ref({})
        assert ref == ""

    def test_get_time_estimate_override(self):
        est = _get_time_estimate("X", "critical", {"X": "~3min"})
        assert est == "~3min"

    def test_get_time_estimate_default(self):
        est = _get_time_estimate("X", "warning", {})
        assert est == "~2min"

    def test_compute_total_time_single(self):
        total = _compute_total_time(
            [{"severity": "critical", "title": "X"}], {}
        )
        assert total == "~5min"

    def test_compute_total_time_multiple(self):
        total = _compute_total_time(
            [
                {"severity": "critical", "title": "X"},
                {"severity": "warning", "title": "Y"},
            ], {}
        )
        assert total == "~7min"

    def test_format_elapsed_ms(self):
        assert "ms" in _format_elapsed(0.5)

    def test_format_elapsed_seconds(self):
        result = _format_elapsed(5.0)
        assert "s" in result
        assert "5" in result

    def test_format_elapsed_minutes(self):
        result = _format_elapsed(300.0)
        assert "m" in result

    def test_format_elapsed_hours(self):
        result = _format_elapsed(7200.0)
        assert "h" in result

    def test_max_visible_constant(self):
        assert MAX_VISIBLE_FINDINGS == 5

    def test_severity_time_map(self):
        assert SEVERITY_TIME_ESTIMATES["critical"] == "~5min"
        assert SEVERITY_TIME_ESTIMATES["warning"] == "~2min"
        assert SEVERITY_TIME_ESTIMATES["suggestion"] == "~1min"


# ── Edge cases ────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_single_critical(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert "1 critical" in body
        assert "webhook.py:344" in body

    def test_many_criticals(self):
        findings = [
            {"severity": "critical", "title": f"Critical {i}", "detail": "d", "file": f"f{i}.py", "line": i}
            for i in range(3)
        ]
        body = assemble_review_body(findings, "ChonSong", "riptide", 1)
        assert "3 critical" in body

    def test_finding_no_file(self):
        finding = {"severity": "warning", "title": "No file", "detail": "test"}
        body = assemble_review_body([finding], "ChonSong", "riptide", 1)
        assert "**No file**" in body

    def test_finding_no_detail(self):
        finding = {"severity": "warning", "title": "No detail", "file": "a.py", "line": 1}
        body = assemble_review_body([finding], "ChonSong", "riptide", 1)
        assert "**No detail**" in body

    def test_exactly_five_findings_no_details(self):
        findings = [
            {"severity": "warning", "title": f"Issue {i}", "detail": "d", "file": "f.py", "line": i}
            for i in range(1, 6)
        ]
        body = assemble_review_body(findings, "ChonSong", "riptide", 1)
        assert "<details>" not in body

    def test_six_findings_has_details(self):
        findings = [
            {"severity": "warning", "title": f"Issue {i}", "detail": "d", "file": "f.py", "line": i}
            for i in range(1, 7)
        ]
        body = assemble_review_body(findings, "ChonSong", "riptide", 1)
        assert "<details>" in body

    def test_truncated_at_limit(self):
        """Test that very long output is truncated."""
        findings = [
            {"severity": "warning", "title": "X" * 1000, "detail": "D" * 1000, "file": "f.py", "line": i}
            for i in range(100)
        ]
        body = assemble_review_body(findings, "ChonSong", "riptide", 1)
        assert len(body) <= 65536

    def test_diagram_url_included(self, critical_finding):
        body = assemble_review_body(
            [critical_finding], "ChonSong", "riptide", 1,
            diagram_url="https://excalidraw.com/#json=abc"
        )
        assert "[Diagram](https://excalidraw.com/#json=abc)" in body

    def test_no_diagram_url_no_link(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert "[Diagram]" not in body


# ── Full output structure ─────────────────────────────────────────────────

class TestFullOutputStructure:
    """Test the complete output structure matches expected format."""

    def test_full_output_has_all_sections(self, critical_finding, warning_finding):
        body = assemble_review_body(
            [critical_finding, warning_finding], "ChonSong", "riptide", 1,
            model="LongCat-2.0"
        )
        # Verdict first
        assert body.startswith("1 critical, 1 warning(s)")
        # Numbered findings
        assert "1. **Race condition**" in body
        assert "2. **Missing retry**" in body
        # Next action
        assert "Next: Fix" in body
        # Sign-off
        assert "Riptide Review" in body

    def test_full_output_no_table(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        assert "| Severity |" not in body
        assert "|----------|" not in body

    def test_full_output_no_bullet_list(self, critical_finding):
        body = assemble_review_body([critical_finding], "ChonSong", "riptide", 1)
        # No bullet list for next steps
        assert "- Address:" not in body