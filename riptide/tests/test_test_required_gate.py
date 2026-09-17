# riptide/tests/test_test_required_gate.py
"""Tests for scripts/check_test_required.sh — the `test-required` CI gate.

The gate decides exactly one thing: whether a `feat:`/`fix:` commit pairs with
test changes, or carries a documented `No-Tests: <reason>` trailer. That decision
used to be inline bash in `.github/workflows/test-required.yml`, where the only
way to exercise it was to open a PR against GitHub. It now lives in a standalone
script that takes the commit data as a file (the same records the workflow builds
from the GitHub API), so the rule can be tested here directly.

Every test below RUNS the script over synthetic commit data and asserts on its
exit code: 0 = all commits OK, 1 = a commit failed the rule, 2 = the check could
not be evaluated. No network, no checkout, no GitHub.
"""

import base64
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_test_required.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test-required.yml"
AGENTS = REPO_ROOT / "AGENTS.md"

TRAILER = "No-Tests: the merge dropped this hunk; riptide/tests/test_state.py already covers the path."


def b64(text: str) -> str:
    """Base64 of a full commit message, the way the workflow's `gh api` jq emits it."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def write_data(tmp_path, commits, name="commits.txt"):
    """Write a gate data file. `commits` is a list of (sha, message, [files])."""
    lines = []
    for sha, message, files in commits:
        lines.append(f"commit {sha}")
        lines.append(f"message {b64(message)}")
        lines.extend(f"file {path}" for path in files)
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_gate(data_path, stdin=False):
    """Run the gate. `data_path` is a Path, or the text to pipe on stdin."""
    args = ["bash", str(SCRIPT), "-"] if stdin else ["bash", str(SCRIPT), str(data_path)]
    kwargs = {"input": data_path} if stdin else {}
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        **kwargs,
    )


def test_script_exists_and_is_a_script():
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")


# ── the rule, one case at a time ────────────────────────────────────────────
# (a) the default is still strict


def test_fix_without_tests_and_without_trailer_fails(tmp_path):
    """(a) `fix: x` touching only non-test files, no trailer → exit 1."""
    data = write_data(tmp_path, [
        ("a1", "fix: restore the dropped handler", ["riptide/state.py", "README.md"]),
    ])
    result = run_gate(data)
    assert result.returncode == 1, result.stdout + result.stderr
    # the recognisable wording branch protection discussions point at
    assert "::error::Commit a1 (fix: restore the dropped handler) adds/changes no test files." in result.stdout
    assert "No-Tests: <reason>" in result.stdout


# (b) the single documented escape hatch


def test_fix_with_trailer_and_no_tests_passes(tmp_path):
    """(b) same commit plus a `No-Tests: <reason>` trailer → exit 0."""
    data = write_data(tmp_path, [
        ("b1", f"fix: restore the dropped handler\n\n{TRAILER}\n", ["riptide/state.py"]),
    ])
    result = run_gate(data)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Exempt" in result.stdout
    assert "the merge dropped this hunk" in result.stdout
    assert "::error::" not in result.stdout


# (c) the normal path still works


def test_fix_touching_a_test_file_passes(tmp_path):
    """(c) `fix: x` touching a test file → exit 0."""
    data = write_data(tmp_path, [
        ("c1", "fix: x", ["riptide/state.py", "riptide/tests/test_state.py"]),
    ])
    result = run_gate(data)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 with paired test changes" in result.stdout
    assert "All feat/fix commits have paired test changes." in result.stdout


# (d) non-feat/fix commits are untouched by the rule


def test_docs_commit_without_tests_is_not_checked(tmp_path):
    """(d) `docs: x` with no test files → exit 0 (not checked)."""
    data = write_data(tmp_path, [
        ("d1", "docs: explain the gate", ["AGENTS.md"]),
    ])
    result = run_gate(data)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No feat/fix commits to check" in result.stdout


# (e) an empty reason is not an exemption


@pytest.mark.parametrize("line", ["No-Tests:", "No-Tests:   "])
def test_trailer_with_empty_reason_fails(tmp_path, line):
    """(e) a trailer with an EMPTY reason → exit 1, and say why."""
    data = write_data(tmp_path, [
        ("e1", f"fix: x\n\n{line}\n", ["riptide/state.py"]),
    ])
    result = run_gate(data)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "::error::Commit e1 (fix: x) has a 'No-Tests:' trailer with an empty reason." in result.stdout
    assert "adds/changes no test files" in result.stdout


# ── the trailer must really be a trailing block in the body ─────────────────

def test_trailer_in_the_subject_does_not_exempt(tmp_path):
    """The token only counts in the body: a subject that mentions it is not a trailer."""
    data = write_data(tmp_path, [
        ("s1", "fix: x No-Tests: because the merge ate it", ["riptide/state.py"]),
    ])
    result = run_gate(data)
    assert result.returncode == 1, result.stdout + result.stderr


def test_trailer_buried_above_a_later_paragraph_does_not_exempt(tmp_path):
    """A `No-Tests:` line that is not in the final paragraph is not a trailer."""
    data = write_data(tmp_path, [
        ("b2", f"fix: x\n\n{TRAILER}\n\nThen some closing remark.\n", ["riptide/state.py"]),
    ])
    result = run_gate(data)
    assert result.returncode == 1, result.stdout + result.stderr
    # its reason is non-empty, so it must not be misreported as an empty one
    assert "with an empty reason" not in result.stdout
    assert "adds/changes no test files" in result.stdout


def test_trailer_in_the_final_paragraph_after_prose_passes(tmp_path):
    data = write_data(tmp_path, [
        ("b3", f"fix: x\n\nWhat happened, in prose.\n\n{TRAILER}\n", ["riptide/state.py"]),
    ])
    result = run_gate(data)
    assert result.returncode == 0, result.stdout + result.stderr


def test_token_is_case_insensitive(tmp_path):
    """Spellings like `no-tests:` are honoured, as git trailers generally are."""
    data = write_data(tmp_path, [
        ("b4", "fix: x\n\nno-tests: merge dropped the hunk; nothing to add.\n", ["riptide/state.py"]),
    ])
    result = run_gate(data)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "merge dropped the hunk" in result.stdout


# ── what counts as a feat/fix commit, and the old test-file patterns ────────

@pytest.mark.parametrize("subject", ["feat: add a thing", "fix(scope): repair it"])
def test_scoped_and_unscoped_subjects_are_gated(tmp_path, subject):
    data = write_data(tmp_path, [(subject, subject, ["riptide/companion.py"])])
    result = run_gate(data)
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize("test_file", [
    "riptide/tests/test_state.py",   # test[_/]
    "web/src/Button.test.tsx",       # .test.
    "riptide/widget_test.py",        # _test.py
])
def test_the_three_test_file_patterns_are_still_honoured(tmp_path, test_file):
    data = write_data(tmp_path, [("p1", "fix: x", ["riptide/widget.py", test_file])])
    result = run_gate(data)
    assert result.returncode == 0, result.stdout + result.stderr


# ── a whole PR: one bad commit is enough ───────────────────────────────────

def test_one_offending_commit_fails_the_run_and_is_named(tmp_path):
    data = write_data(tmp_path, [
        ("good1", "fix: a", ["riptide/tests/test_a.py"]),
        ("good2", f"feat: b\n\n{TRAILER}\n", ["riptide/b.py"]),
        ("bad3", "fix: c", ["riptide/c.py"]),
        ("docs4", "docs: d", ["README.md"]),
    ])
    result = run_gate(data)
    assert result.returncode == 1, result.stdout + result.stderr
    errors = [line for line in result.stdout.splitlines() if line.startswith("::error::Commit")]
    assert len(errors) == 1, errors
    assert "bad3" in errors[0]
    assert "good1" not in errors[0] and "good2" not in errors[0]


# ── the seam: stdin, and malformed input fails closed ──────────────────────

def test_data_can_be_read_from_stdin(tmp_path):
    """The workflow passes a file; the seam also accepts stdin."""
    data = write_data(tmp_path, [("i1", "fix: x", ["riptide/tests/test_x.py"])])
    result = run_gate(data.read_text(encoding="utf-8"), stdin=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_data_file_exits_two(tmp_path):
    result = run_gate(tmp_path / "absent.txt")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "data file not found" in result.stderr


def test_no_arguments_exits_two():
    result = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_empty_commit_list_exits_zero(tmp_path):
    data = tmp_path / "empty.txt"
    data.write_text("", encoding="utf-8")
    result = run_gate(data)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No feat/fix commits to check" in result.stdout


def test_record_without_a_message_fails_closed(tmp_path):
    """A generator bug must not silently pass every commit."""
    data = tmp_path / "no-message.txt"
    data.write_text("commit z1\nfile riptide/state.py\n", encoding="utf-8")
    result = run_gate(data)
    assert result.returncode == 2
    assert "no 'message' field" in result.stderr


def test_unrecognised_line_fails_closed(tmp_path):
    data = tmp_path / "garbage.txt"
    data.write_text("commit z2\nwat ist das\n", encoding="utf-8")
    result = run_gate(data)
    assert result.returncode == 2
    assert "unrecognised line" in result.stderr


# ── the workflow is wired to the script, not to inline logic ───────────────

def test_workflow_calls_the_script():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/check_test_required.sh" in text
    assert "bash scripts/check_test_required.sh" in text


def test_workflow_no_longer_carries_the_logic_inline():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "diff-tree" not in text
    assert "test[_/]" not in text
    assert "git rev-list" not in text


def test_workflow_identity_is_unchanged_for_branch_protection():
    """Workflow name, job name and triggers are what branch protection points at."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "name: test-required" in text
    assert "  pull_request:\n    types: [opened, synchronize]" in text
    assert "jobs:\n  check:" in text
    assert "Check that feat/fix commits include test changes" in text


# ── the exemption is documented where the rule is stated ───────────────────
#
# NOTE: `AGENTS.md` is a protected agent-instruction file, so this task could not
# edit it (the write was refused by policy). The doc assertion that belongs here —
# that `## Commits and PRs` states the `No-Tests:` rule — is therefore not
# included, because it would be red until the AGENTS.md bullet lands. Add it back
# with the documented bullet:
#
# def test_agents_md_documents_the_exemption():
#     text = AGENTS.read_text(encoding="utf-8")
#     section = text.split("## Commits and PRs", 1)[1].split("\n## ", 1)[0]
#     assert "test-required" in section
#     assert "No-Tests:" in section
#     assert "no test to add" in section
