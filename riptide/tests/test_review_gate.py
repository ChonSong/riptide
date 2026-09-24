# riptide/tests/test_review_gate.py
"""Tests for scripts/check_riptide_review.sh — the `riptide-review-required` CI gate.

The gate decides exactly one thing: whether the newest Riptide review's findings have
been answered. That decision used to be inline bash in
`.github/workflows/riptide-review-required.yml`, where the only way to exercise it was
to open a PR against GitHub — and where three separate ways to green a check while
findings stood went unnoticed:

  * any commit after the review counted, addressed or not, so a review could be
    greened by a push that changed something else entirely;
  * the pass-heading exclusion matched the whole comment body, so a review that merely
    QUOTED `## Riptide Pass:` was dropped and the gate fell back to an older comment;
  * a pass comment alone satisfied the gate while saying nothing about whether the
    head had ever been reviewed.

The rule now lives in a standalone script that takes the PR's comments and commits as
data (the same records the workflow builds from the GitHub API), so all of it can be
tested here directly.

Every test below RUNS the script over synthetic records and asserts on its exit code:
0 = answered (or nothing to answer), 1 = findings stand, 2 = could not be evaluated.
No network, no checkout, no GitHub.
"""

import base64
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_riptide_review.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "riptide-review-required.yml"

REVIEWED_AT = "2026-09-22T00:20:44Z"
BEFORE = "2026-09-22T00:10:00Z"
AFTER = "2026-09-22T10:00:00Z"

SIGNOFF = "<sub>Riptide Review · model: `x` · provider: `y`</sub>"

CLEAN_REVIEW = f"""## Review: no findings.

The code reads well and the tests cover the paths that changed.

{SIGNOFF}
"""

FINDINGS_REVIEW = f"""## Review: 1 warning(s). Fix `riptide/companion.py:429` first — the buckets overlap.

1. **Pass evidence can print a negative file bucket** in `riptide/companion.py:429` — `others` subtracts overlapping buckets, so the remainder goes negative.

| | Finding | File |
|---|---|---|
| 🟡 | Pass evidence can print a negative file bucket | `riptide/companion.py:429` |

{SIGNOFF}
"""

# A review of the *selector* itself: it quotes both excluded headings in prose, which
# is exactly what the old body-wide test could not tell apart from a pass.
QUOTING_REVIEW = f"""## Review: 1 warning(s). The selector still greps the whole body.

1. **The pass test is body-wide** — it matches a review that quotes `## Riptide Pass:` or `## ✨ Review Required` while discussing them.

| | Finding | File |
|---|---|---|
| 🟡 | The pass test is body-wide | `.github/workflows/riptide-review-required.yml:40` |

{SIGNOFF}
"""

FILELESS_REVIEW = f"""## Review: 1 warning(s).

| | Finding | File |
|---|---|---|
| 🟡 | Something the reviewer could not pin to a file | — |

{SIGNOFF}
"""

PASS_COMMENT = """## Riptide Pass: deterministic review

Scanned 4 changed file(s); no template or contract violations.

<sub>Riptide Companion · deterministic pass</sub>
"""

PRE_PASS = """## ✨ Review Required

This PR changes 240 lines across 6 files — a deep review is queued.

| | Finding | File |
|---|---|---|
| 🟡 | Complexity pre-pass | `riptide/companion.py:1` |
"""


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def write_data(tmp_path, reviews=(), commits=(), name="pr.txt"):
    """Write a gate data file. `reviews` is [(id, created_at, body)]; `commits` is
    [(sha, committer_date, [paths])]."""
    lines = []
    for comment_id, created_at, body in reviews:
        lines.append(f"review {comment_id} {created_at} {b64(body)}")
    for sha, date, files in commits:
        lines.append(f"commit {sha} {date}")
        lines.extend(f"file {path}" for path in files)
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_gate(data_path, tmp_path=None, summary=False):
    """Run the gate over a data file (or raw text). Returns the CompletedProcess."""
    if isinstance(data_path, str):
        path = Path(tmp_path or ".") / "inline.txt"
        path.write_text(data_path, encoding="utf-8")
    else:
        path = data_path
    env = dict(os.environ)
    env.pop("GITHUB_STEP_SUMMARY", None)
    if summary:
        summary_path = Path(tmp_path) / "summary.md"
        env["GITHUB_STEP_SUMMARY"] = str(summary_path)
    return subprocess.run(
        ["bash", str(SCRIPT), str(path)], capture_output=True, text=True, env=env
    )


# ── The selection: which comment the gate judges ──────────────────────────────


def test_no_comments_fails_and_asks_for_a_review(tmp_path):
    """An unreviewed PR keeps the check red, and the message says how to clear it."""
    proc = run_gate(write_data(tmp_path), tmp_path)

    assert proc.returncode == 1
    assert "@riptide-bot review" in proc.stdout


def test_a_clean_review_satisfies(tmp_path):
    data = write_data(tmp_path, reviews=[(41, REVIEWED_AT, CLEAN_REVIEW)])
    proc = run_gate(data, tmp_path)

    assert proc.returncode == 0
    assert "clean" in proc.stdout


def test_a_findings_review_with_no_commit_fails(tmp_path):
    data = write_data(tmp_path, reviews=[(41, REVIEWED_AT, FINDINGS_REVIEW)])
    proc = run_gate(data, tmp_path)

    assert proc.returncode == 1
    assert "At least one commit" in proc.stdout


def test_a_commit_before_the_review_does_not_count(tmp_path):
    """The review was written against the commit, so it cannot be its answer."""
    data = write_data(
        tmp_path,
        reviews=[(41, REVIEWED_AT, FINDINGS_REVIEW)],
        commits=[("aaa", BEFORE, ["riptide/companion.py"])],
    )
    proc = run_gate(data, tmp_path)

    assert proc.returncode == 1


def test_a_pass_posted_after_a_review_does_not_shadow_it(tmp_path):
    """The newer pass is not a review: the findings review is still the one judged."""
    data = write_data(
        tmp_path,
        reviews=[(41, REVIEWED_AT, FINDINGS_REVIEW), (42, AFTER, PASS_COMMENT)],
    )
    proc = run_gate(data, tmp_path)

    assert proc.returncode == 1


def test_the_complexity_pre_pass_is_not_read_as_a_findings_review(tmp_path):
    """It posts before the review and carries 🟡 rows; on its own it must not block."""
    data = write_data(tmp_path, reviews=[(40, BEFORE, PRE_PASS)])
    proc = run_gate(data, tmp_path)

    assert proc.returncode == 1
    assert "No Riptide review found" in proc.stdout


# ── Quoting an excluded heading must not drop a real review ───────────────────


def test_a_review_quoting_the_pass_heading_is_still_judged(tmp_path):
    """The regression: the pass test ran over the whole body, so this review was
    dropped from the non-pass pool — and with a pass posted after it, the old
    selector's fallback picked that pass and the check went GREEN while the finding
    stood (reproduced: it selected comment 42, not 41). Anchored to the first line,
    the review is judged and the gate is red."""
    data = write_data(
        tmp_path,
        reviews=[(41, REVIEWED_AT, QUOTING_REVIEW), (42, AFTER, PASS_COMMENT)],
    )
    proc = run_gate(data, tmp_path)

    assert proc.returncode == 1
    assert "At least one commit" in proc.stdout


def test_a_review_quoting_the_prepass_heading_is_still_judged(tmp_path):
    data = write_data(tmp_path, reviews=[(41, REVIEWED_AT, QUOTING_REVIEW.replace(
        "`## Riptide Pass:` or ", "`## ✨ Review Required` or "
    ))])
    proc = run_gate(data, tmp_path)

    assert proc.returncode == 1


# ── The addressing commit must touch what the findings name ───────────────────


def test_a_commit_touching_the_named_file_satisfies(tmp_path):
    data = write_data(
        tmp_path,
        reviews=[(41, REVIEWED_AT, FINDINGS_REVIEW)],
        commits=[("bbb", AFTER, ["riptide/companion.py", "CHANGELOG.md"])],
    )
    proc = run_gate(data, tmp_path)

    assert proc.returncode == 0
    assert "riptide/companion.py" in proc.stdout


def test_a_commit_touching_something_else_fails(tmp_path):
    """The regression: this used to be enough. Any commit greened the check while
    both findings stood."""
    data = write_data(
        tmp_path,
        reviews=[(41, REVIEWED_AT, FINDINGS_REVIEW)],
        commits=[("bbb", AFTER, ["docs/README.md"])],
    )
    proc = run_gate(data, tmp_path)

    assert proc.returncode == 1
    assert "none touches the files the findings name" in proc.stdout
    assert "riptide/companion.py" in proc.stdout


def test_only_commits_after_the_review_are_considered(tmp_path):
    """A touched file counts only from a commit the review did not already cover."""
    data = write_data(
        tmp_path,
        reviews=[(41, REVIEWED_AT, FINDINGS_REVIEW)],
        commits=[
            ("aaa", BEFORE, ["riptide/companion.py"]),
            ("bbb", AFTER, ["docs/README.md"]),
        ],
    )
    proc = run_gate(data, tmp_path)

    assert proc.returncode == 1


def test_a_finding_naming_no_file_accepts_any_commit_and_says_so(tmp_path):
    """An unverifiable row must not be silently treated as answered — and must not
    block a PR whose other findings were answered either."""
    data = write_data(
        tmp_path,
        reviews=[(41, REVIEWED_AT, FILELESS_REVIEW)],
        commits=[("bbb", AFTER, ["docs/README.md"])],
    )
    proc = run_gate(data, tmp_path)

    assert proc.returncode == 0
    assert "names no file" in proc.stdout


# ── The honest label on a deterministic pass ─────────────────────────────────


def test_a_pass_alone_satisfies_but_reports_that_no_review_ran(tmp_path):
    data = write_data(tmp_path, reviews=[(30, BEFORE, PASS_COMMENT)])
    proc = run_gate(data, tmp_path, summary=True)

    assert proc.returncode == 0
    assert "not a review" in proc.stdout
    summary = (Path(tmp_path) / "summary.md").read_text(encoding="utf-8")
    assert "No deep review" in summary
    assert "✅" in summary


def test_a_failed_gate_writes_too(tmp_path):
    data = write_data(tmp_path, reviews=[(41, REVIEWED_AT, FINDINGS_REVIEW)])
    run_gate(data, tmp_path, summary=True)
    summary = (Path(tmp_path) / "summary.md").read_text(encoding="utf-8")

    assert "❌" in summary
    assert "At least one commit" in summary


# ── Malformed input is not evaluable ─────────────────────────────────────────


def test_a_review_without_a_body_is_exit_2(tmp_path):
    proc = run_gate("review 41 2026-09-22T00:20:44Z\n", tmp_path)

    assert proc.returncode == 2


def test_an_unknown_field_is_exit_2(tmp_path):
    proc = run_gate("nonsense 1 2 3\n", tmp_path)

    assert proc.returncode == 2


def test_a_file_before_any_commit_is_exit_2(tmp_path):
    proc = run_gate("file riptide/companion.py\n", tmp_path)

    assert proc.returncode == 2


def test_a_missing_data_file_is_exit_2(tmp_path):
    """A path that does not exist is not evaluable — not an empty PR."""
    env = dict(os.environ)
    env.pop("GITHUB_STEP_SUMMARY", None)
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path / "does-not-exist.txt")],
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 2
    assert "cannot read data file" in proc.stderr


# ── The workflow must not re-implement the rule inline ──────────────────────


def test_the_workflow_calls_the_extracted_gate():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "scripts/check_riptide_review.sh" in text
    # The selector lives in the script; a body-wide pass test must not creep back in.
    assert 'contains("## Riptide Pass:")' not in text
    assert "split(\"\\n\")[0]" not in text


# ── Rows the parser must read the way the detection does ────────────────────

REPEATED_FILE_REVIEW = f"""## Review: 2 warning(s). Two findings, one file.

1. **A negative bucket** in `riptide/companion.py:429`.
2. **A single-kind diff called mixed** in `riptide/companion.py:436`.

| | Finding | File |
|---|---|---|
| 🟡 | Pass evidence can print a negative file bucket | `riptide/companion.py:429` |
| 🟡 | A single-kind diff is called mixed | `riptide/companion.py:436` |

{SIGNOFF}
"""

FILELESS_ROW_WITH_CODE_REVIEW = f"""## Review: 1 warning(s).

1. **A helper is used wrongly** — the reviewer pinned no file.

| | Finding | File |
|---|---|---|
| 🟡 | `_describe_changed_files()` mixes classification and rendering | — |

{SIGNOFF}
"""

QUOTED_ROW_REVIEW = f"""## Review: no findings of its own.

The review it answers had said:

> | | Finding | File |
> |---|---|---|
> | 🟡 | An earlier finding, quoted here for context | `riptide/companion.py:429` |

{SIGNOFF}
"""


def test_two_rows_naming_one_file_still_require_a_touching_commit(tmp_path):
    """Two findings in one file are two named rows. Counting unique paths instead
    relaxed the rule back to "any commit" for exactly the reviews that repeat a
    file — which is most of them, and all three rows of one recent review."""
    data = write_data(
        tmp_path,
        reviews=[(41, REVIEWED_AT, REPEATED_FILE_REVIEW)],
        commits=[("bbb", AFTER, ["docs/README.md", "CHANGELOG.md"])],
    )

    proc = run_gate(data)

    assert proc.returncode == 1
    assert "none touches the files the findings name" in proc.stdout
    assert "names no file" not in proc.stdout


def test_a_fileless_row_with_inline_code_is_not_a_path(tmp_path):
    """The File cell is `—`, so the row names no file. Reading the row's last code
    span as the path demanded a commit touching `_describe_changed_files()` — a
    red no push can clear."""
    data = write_data(
        tmp_path,
        reviews=[(41, REVIEWED_AT, FILELESS_ROW_WITH_CODE_REVIEW)],
        commits=[("bbb", AFTER, ["docs/README.md"])],
    )

    proc = run_gate(data)

    assert proc.returncode == 0
    assert "0 naming a file" in proc.stdout
    assert "_describe_changed_files" not in proc.stdout


def test_a_quoted_row_is_not_a_finding(tmp_path):
    """Detection and parsing share one predicate, so a review that quotes a
    severity row raises nothing — it must not reach the relaxation branch with a
    self-contradictory "0 finding row(s)" note."""
    data = write_data(tmp_path, reviews=[(41, REVIEWED_AT, QUOTED_ROW_REVIEW)])

    proc = run_gate(data)

    assert proc.returncode == 0
    assert "is clean" in proc.stdout
    assert "finding row(s)" not in proc.stdout
