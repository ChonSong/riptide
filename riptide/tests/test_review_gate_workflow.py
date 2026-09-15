"""Locks the Riptide Review Required gate's comment selector.

The gate previously treated the Companion's complexity pre-pass
(``## ✨ Review Required``) as a findings-bearing review, because its marker set
included a loose ``critical`` + ``warning`` text match. The pre-pass posts
*before* the deep-think review, so a push landing in that window made the gate
read its 🟡 rows as findings and fail an otherwise clean PR.

These assertions are deliberately about the workflow text: the selector is a jq
program inside the workflow, so the only way to catch it regressing is to pin
what it matches.
"""

from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "riptide-review-required.yml"
)


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_exists():
    assert WORKFLOW.is_file(), f"missing {WORKFLOW}"


def test_gate_excludes_the_companion_pre_pass():
    """The pre-pass must never be selected as a review."""
    text = _workflow_text()
    assert 'contains("## ✨ Review Required")' in text, (
        "the Companion's pre-pass is not excluded from the review selector"
    )
    assert "| not)" in text, "the exclusion must actually negate the match"


def test_gate_matches_the_markers_a_real_review_carries():
    text = _workflow_text()
    for marker in (
        "## 🔍 Findings",
        "## 🎯 Summary",
        "Riptide Review ·",
        "## Riptide Pass:",
    ):
        assert f'contains("{marker}")' in text, f"selector lost the {marker!r} marker"


def test_gate_does_not_use_a_loose_critical_warning_clause():
    """The removed clause matched any comment that happened to use both words."""
    text = _workflow_text()
    assert 'contains("critical")' not in text, (
        "the loose critical/warning clause is back — it matches non-review comments"
    )
    assert 'contains("warning")' not in text, (
        "the loose critical/warning clause is back — it matches non-review comments"
    )
