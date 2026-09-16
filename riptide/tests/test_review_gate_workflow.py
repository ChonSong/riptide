"""Locks the Riptide Review Required gate's comment selector.

The gate previously treated the Companion's complexity pre-pass
(``## ... Review Required``) as a findings-bearing review, because its marker set
included a loose ``critical`` + ``warning`` text match. The pre-pass posts
*before* the deep-think review, so a push landing in that window made the gate
read its 🟡 rows as findings and fail an otherwise clean PR.

Then the first fix over-corrected: it excluded the pre-pass with a substring test
over the whole body, so a *review that quoted the heading* was filtered out too —
silently hiding a findings review and greening the check.

These tests run the workflow's own jq selector over representative comment bodies,
which is the only way to catch that class of mistake.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "riptide-review-required.yml"
)

PRE_PASS = (
    "## ✨ Review Required\n\n@someone:\n⚠️ 2 complexity issues found\n"
    "| 🟡 | Function 'x' has nesting depth 5 | `f.py` |\n"
)
REVIEW_QUOTING_THE_HEADING = (
    "## Review: 1 warning(s). Fix `f.py:1` first.\n\n"
    "1. **The pre-pass exclusion is a substring test** — a review quoting "
    "`## ✨ Review Required` used to be dropped.\n\n"
    "| | Finding | File |\n|---|---|---|\n| 🟡 | dropped review | `f.py` |\n\n"
    "<sub>Riptide Review · model: `m` · provider: `p`</sub>"
)
REVIEW = (
    "## Review: 2 warning(s). Fix `f.py:1` first.\n\n"
    "| | Finding | File |\n|---|---|---|\n| 🟡 | something real | `f.py` |\n\n"
    "<sub>Riptide Review · model: `m` · provider: `p`</sub>"
)
COMPANION_PASS = "## Riptide Pass: ✅ No findings\n\n**Verdict:** pass\n"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _selector() -> str:
    """Pull the jq selector out of the workflow's gh api call.

    The selector is one long line ending in ``')``; the ``.*`` is greedy so it
    stops at the last single quote before that (the body itself uses double
    quotes only).
    """
    match = re.search(r"--jq '(.*)'\)\s*$", _workflow_text(), re.MULTILINE)
    assert match, "could not find the --jq selector in the workflow"
    return match.group(1)


def _select(comments: list[dict]) -> dict | None:
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq is not installed")
    proc = subprocess.run(
        [jq, "-c", _selector()],
        input=json.dumps(comments),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    return json.loads(out) if out and out != "null" else None


def _comment(body: str, created: str, cid: int) -> dict:
    return {"id": cid, "body": body, "created_at": created}


def test_workflow_exists():
    assert WORKFLOW.is_file(), f"missing {WORKFLOW}"


def test_gate_picks_the_review_not_the_companion_pre_pass():
    chosen = _select([
        _comment(PRE_PASS, "2026-01-01T00:00:00Z", 1),
        _comment(REVIEW, "2026-01-01T00:01:00Z", 2),
    ])
    assert chosen is not None and chosen["id"] == 2, chosen


def test_gate_does_not_drop_a_review_that_quotes_the_pre_pass_heading():
    """The regression: the exclusion must not be a whole-body substring test."""
    chosen = _select([
        _comment(PRE_PASS, "2026-01-01T00:00:00Z", 1),
        _comment(REVIEW_QUOTING_THE_HEADING, "2026-01-01T00:01:00Z", 2),
    ])
    assert chosen is not None, "the review was filtered out entirely"
    assert chosen["id"] == 2, (
        "the selector dropped a real review because it quoted the pre-pass heading"
    )


def test_a_later_companion_pass_does_not_shadow_a_findings_review():
    """Otherwise a post-review pass greens the check while findings stand."""
    chosen = _select([
        _comment(REVIEW, "2026-01-01T00:01:00Z", 2),
        _comment(COMPANION_PASS, "2026-01-01T00:02:00Z", 3),
    ])
    assert chosen is not None and chosen["id"] == 2, chosen


def test_the_pass_is_used_when_it_is_the_only_marker():
    chosen = _select([_comment(COMPANION_PASS, "2026-01-01T00:02:00Z", 3)])
    assert chosen is not None and chosen["id"] == 3, chosen


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
