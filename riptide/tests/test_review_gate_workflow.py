"""Locks the Riptide Review Required gate's comment selector.

The gate previously treated the Companion's complexity pre-pass
(``## ... Review Required``) as a findings-bearing review, because its marker set
included a loose ``critical`` + ``warning`` text match. The pre-pass posts
*before* the deep-think review, so a push landing in that window made the gate
read its 🟡 rows as findings and fail an otherwise clean PR.

Then the first fix over-corrected: it excluded the pre-pass with a substring test
over the whole body, so a *review that quoted the heading* was filtered out too —
silently hiding a findings review and greening the check.

The selector now lives in scripts/check_riptide_review.sh, so these tests feed it
comment records and read back the one line naming the comment it judged
(``chosen: review <id>`` / ``chosen: pass <ts>`` / ``chosen: none``). Running the
real selector is the only way to catch that class of mistake.
"""

import base64
import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_riptide_review.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "riptide-review-required.yml"

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


def _record(body: str, created: str, cid: int) -> str:
    encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
    return f"review {cid} {created} {encoded}"


def _judge(tmp_path, records: list[str]) -> str:
    """Run the gate over these records, returning its `chosen:` payload."""
    data = tmp_path / "comments.txt"
    data.write_text("\n".join(records) + "\n", encoding="utf-8")
    env = dict(os.environ)
    env.pop("GITHUB_STEP_SUMMARY", None)
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(data)], capture_output=True, text=True, env=env
    )
    match = re.search(r"^chosen: (.+)$", proc.stdout, re.MULTILINE)
    assert match, f"the gate printed no chosen: line\n{proc.stdout}\n{proc.stderr}"
    return match.group(1)


def _script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_the_selector_lives_in_the_gate_script():
    assert SCRIPT.is_file(), f"missing {SCRIPT}"


def test_gate_picks_the_review_not_the_companion_pre_pass(tmp_path):
    chosen = _judge(tmp_path, [
        _record(PRE_PASS, "2026-01-01T00:00:00Z", 1),
        _record(REVIEW, "2026-01-01T00:01:00Z", 2),
    ])
    assert chosen == "review 2", chosen


def test_gate_does_not_drop_a_review_that_quotes_the_pre_pass_heading(tmp_path):
    """The regression: the exclusion must not be a whole-body substring test."""
    chosen = _judge(tmp_path, [
        _record(PRE_PASS, "2026-01-01T00:00:00Z", 1),
        _record(REVIEW_QUOTING_THE_HEADING, "2026-01-01T00:01:00Z", 2),
    ])
    assert chosen == "review 2", (
        "the selector dropped a real review because it quoted the pre-pass heading"
    )


def test_a_later_companion_pass_does_not_shadow_a_findings_review(tmp_path):
    """Otherwise a post-review pass greens the check while findings stand."""
    chosen = _judge(tmp_path, [
        _record(REVIEW, "2026-01-01T00:01:00Z", 2),
        _record(COMPANION_PASS, "2026-01-01T00:02:00Z", 3),
    ])
    assert chosen == "review 2", chosen


def test_the_pass_is_used_when_it_is_the_only_marker(tmp_path):
    chosen = _judge(tmp_path, [
        _record(COMPANION_PASS, "2026-01-01T00:02:00Z", 3),
    ])
    assert chosen.startswith("pass "), chosen


def test_gate_matches_the_markers_a_real_review_carries():
    text = _script_text()
    for marker in (
        "## 🔍 Findings",
        "## 🎯 Summary",
        "Riptide Review ·",
        "## Riptide Pass:",
    ):
        assert marker in text, f"the selector lost the {marker!r} marker"


def test_gate_does_not_use_a_loose_critical_warning_clause():
    """The removed clause matched any comment that happened to use both words."""
    text = _script_text()
    assert 'contains("critical")' not in text, (
        "the loose critical/warning clause is back — it matches non-review comments"
    )
    assert 'contains("warning")' not in text, (
        "the loose critical/warning clause is back — it matches non-review comments"
    )


def test_both_exclusions_are_anchored_to_the_first_line():
    """A body-wide `contains` drops a review that quotes the heading."""
    text = _script_text()
    assert "first_line()" in text
    # The exclusions go through `head`, never through the whole body.
    assert re.search(r'case "\$head" in\s*\n\s*\*"Review Required"\*', text)
    assert '[[ "$head" == *"Riptide Pass:"* ]]' in text


def test_the_workflow_runs_the_script_and_keeps_no_inline_selector():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/check_riptide_review.sh" in text
    assert 'contains("## Riptide Pass:")' not in text, (
        "the body-wide pass test is back in the workflow"
    )
