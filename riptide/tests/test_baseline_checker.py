# riptide/tests/test_baseline_checker.py
"""Tests for scripts/check_test_baseline.py (the pytest baseline gate).

The checker is a standalone script, not part of the ``riptide`` package, so it
is loaded by path. All fixtures here are synthetic pytest output blobs — no
suite run is needed, which keeps these tests fast and deterministic.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "scripts" / "check_test_baseline.py"
DEFAULT_BASELINE = REPO_ROOT / "riptide" / "tests" / "baseline_failures.txt"

FAIL_A = "riptide/tests/test_fixer.py::TestFixer::test_applies_patch"
FAIL_B = "riptide/tests/test_pipeline.py::test_runs"
FAIL_C = "riptide/tests/test_companion.py::test_comment_body"


def _load_checker():
    spec = importlib.util.spec_from_file_location("_riptide_baseline_checker", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves string annotations (PEP 563) through
    # sys.modules[cls.__module__], so the module must be registered first.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker():
    return _load_checker()


def pytest_output(failures, passed=30, extra=""):
    """Build a synthetic pytest -q -rf output blob for the given node IDs."""
    lines = [
        "........................F.............. [100%]",
        extra,
        "= short test summary info =",
    ]
    lines += [f"FAILED {nid} - AssertionError: synthetic failure" for nid in failures]
    lines.append(f"{len(failures)} failed, {passed} passed in 12.34s")
    return "\n".join(line for line in lines if line) + "\n"


def write_baseline(tmp_path, entries):
    path = tmp_path / "baseline_failures.txt"
    body = "# synthetic baseline\n\n" + "".join(f"{e}\n" for e in entries)
    path.write_text(body, encoding="utf-8")
    return path


# ── outcome: exact match ────────────────────────────────────────────────────

def test_exact_match_exits_zero(checker, tmp_path):
    baseline = write_baseline(tmp_path, [FAIL_B, FAIL_A])  # unsorted on purpose
    report = checker.evaluate(pytest_output([FAIL_A, FAIL_B]), baseline, pytest_exit_code=1)
    assert report.status == "match"
    assert report.exit_code == 0
    assert report.new == []
    assert report.stale == []
    assert report.failures == [FAIL_A, FAIL_B]  # normalised to sorted order


def test_empty_baseline_and_green_suite_exits_zero(checker, tmp_path):
    baseline = write_baseline(tmp_path, [])
    report = checker.evaluate("120 passed in 8.00s\n", baseline, pytest_exit_code=0)
    assert (report.status, report.exit_code, report.failures) == ("match", 0, [])


# ── outcome: new failures ───────────────────────────────────────────────────

def test_new_failure_is_non_zero(checker, tmp_path):
    baseline = write_baseline(tmp_path, [FAIL_A])
    report = checker.evaluate(pytest_output([FAIL_A, FAIL_B]), baseline, pytest_exit_code=1)
    assert report.exit_code == 1
    assert report.status == "drift"
    assert report.new == [FAIL_B]
    assert report.stale == []
    assert FAIL_B in report.text_report()


def test_every_new_failure_is_listed(checker, tmp_path):
    baseline = write_baseline(tmp_path, [FAIL_A])
    report = checker.evaluate(pytest_output([FAIL_A, FAIL_B, FAIL_C]), baseline, pytest_exit_code=1)
    assert report.exit_code == 1
    assert report.new == sorted([FAIL_B, FAIL_C])


# ── outcome: stale baseline entries ─────────────────────────────────────────

def test_stale_entry_is_reported_but_not_fatal(checker, tmp_path):
    """A passing test is not a regression, so a stale entry must not fail CI."""
    baseline = write_baseline(tmp_path, [FAIL_A, FAIL_B])
    report = checker.evaluate(pytest_output([FAIL_A]), baseline, pytest_exit_code=1)
    assert report.exit_code == 0
    assert report.status == "match"
    assert report.stale == [FAIL_B]
    assert report.new == []
    assert "delete" in report.text_report().lower()


def test_strict_stale_entry_fails_when_asked(checker, tmp_path):
    baseline = write_baseline(tmp_path, [FAIL_A, FAIL_B])
    report = checker.evaluate(pytest_output([FAIL_A]), baseline, pytest_exit_code=1,
                              strict_stale=True)
    assert report.exit_code == 1
    assert report.status == "drift"
    assert report.stale == [FAIL_B]
    assert report.new == []


def test_all_baseline_entries_passing_is_not_fatal(checker, tmp_path):
    baseline = write_baseline(tmp_path, [FAIL_A, FAIL_B])
    report = checker.evaluate("120 passed in 8.00s\n", baseline, pytest_exit_code=0)
    assert report.exit_code == 0
    assert report.stale == [FAIL_A, FAIL_B]


def test_new_and_stale_together(checker, tmp_path):
    baseline = write_baseline(tmp_path, [FAIL_A])
    report = checker.evaluate(pytest_output([FAIL_B]), baseline, pytest_exit_code=1)
    assert report.exit_code == 1
    assert report.new == [FAIL_B]
    assert report.stale == [FAIL_A]


# ── outcome: unusable baseline / output ─────────────────────────────────────

def test_missing_baseline_exits_two(checker, tmp_path):
    missing = tmp_path / "does_not_exist.txt"
    report = checker.evaluate(pytest_output([FAIL_A]), missing, pytest_exit_code=1)
    assert report.exit_code == 2
    assert report.status == "error"
    assert "not found" in report.detail


def test_malformed_baseline_exits_two(checker, tmp_path):
    path = tmp_path / "baseline_failures.txt"
    path.write_text("# header\nriptide/tests/test_fixer.py::test_ok\nnot a node id\n", encoding="utf-8")
    report = checker.evaluate(pytest_output([FAIL_A]), path, pytest_exit_code=1)
    assert report.exit_code == 2
    assert report.status == "error"
    assert "malformed" in report.detail


def test_malformed_baseline_points_at_the_line(checker, tmp_path):
    path = tmp_path / "baseline_failures.txt"
    path.write_text("riptide/tests/test_fixer.py::test_ok\n=== broken ===\n", encoding="utf-8")
    with pytest.raises(checker.BaselineError) as excinfo:
        checker.load_baseline(path)
    assert ":2" in str(excinfo.value)


def test_unparsable_pytest_output_exits_two(checker, tmp_path):
    baseline = write_baseline(tmp_path, [])
    report = checker.evaluate("this is not pytest output\n", baseline, pytest_exit_code=2)
    assert report.exit_code == 2
    assert "could not parse" in report.detail


def test_pytest_exit_5_is_an_error(checker, tmp_path):
    baseline = write_baseline(tmp_path, [])
    report = checker.evaluate("no tests ran in 0.01s\n", baseline, pytest_exit_code=5)
    assert report.exit_code == 2
    assert "collected no tests" in report.detail


# ── parsing details ─────────────────────────────────────────────────────────

def test_counts_are_parsed(checker):
    parsed = checker.parse_pytest_output(pytest_output([FAIL_A, FAIL_B], passed=1096))
    assert parsed.has_summary is True
    assert parsed.counts["failed"] == 2
    assert parsed.counts["passed"] == 1096


def test_error_entries_are_failures(checker):
    blob = (
        "= short test summary info =\n"
        "ERROR riptide/tests/test_broken.py::test_import - ImportError\n"
        "1 error in 1.00s\n"
    )
    parsed = checker.parse_pytest_output(blob)
    assert parsed.failures == ["riptide/tests/test_broken.py::test_import"]


def test_verbose_progress_lines_are_detected(checker):
    blob = (
        "riptide/tests/test_x.py::TestA::test_pass PASSED [ 50%]\n"
        "riptide/tests/test_x.py::TestA::test_fail FAILED [100%]\n"
        "1 failed, 1 passed in 2.00s\n"
    )
    assert checker.parse_pytest_output(blob).failures == ["riptide/tests/test_x.py::TestA::test_fail"]


def test_node_ids_are_normalised(checker):
    assert checker.normalize_node_id("./riptide/tests/test_x.py::test_y") == "riptide/tests/test_x.py::test_y"
    absolute = f"{REPO_ROOT}/riptide/tests/test_x.py::test_y"
    assert checker.normalize_node_id(absolute) == "riptide/tests/test_x.py::test_y"
    blob = pytest_output([])
    blob = blob.replace("= short test summary info =", "= short test summary info =\nFAILED  ./riptide/tests/test_x.py::test_y - E")
    parsed = checker.parse_pytest_output(blob)
    assert parsed.failures == ["riptide/tests/test_x.py::test_y"]


def test_baseline_comments_and_blanks_ignored(checker, tmp_path):
    path = tmp_path / "baseline_failures.txt"
    path.write_text(
        "# comment\n\n   \nriptide/tests/test_x.py::test_y\n# trailing comment\n",
        encoding="utf-8",
    )
    assert checker.load_baseline(path) == ["riptide/tests/test_x.py::test_y"]


def test_compare_reports_both_directions(checker):
    new, stale = checker.compare([FAIL_A, FAIL_B], [FAIL_B, FAIL_C])
    assert new == [FAIL_C]
    assert stale == [FAIL_A]


# ── summary + baseline writing ──────────────────────────────────────────────

def test_markdown_summary_is_written_and_lists_new_failures(checker, tmp_path):
    baseline = write_baseline(tmp_path, [FAIL_A])
    summary = tmp_path / "out.md"
    code = checker.main([
        "--pytest-output", str(_write_output(tmp_path, [FAIL_A, FAIL_B])),
        "--baseline", str(baseline),
        "--pytest-exit-code", "1",
        "--summary", str(summary),
    ])
    assert code == 1
    text = summary.read_text(encoding="utf-8")
    assert "drift" in text.lower()
    assert FAIL_B in text


def test_markdown_summary_for_a_match(checker, tmp_path):
    baseline = write_baseline(tmp_path, [FAIL_A])
    summary = tmp_path / "out.md"
    code = checker.main([
        "--pytest-output", str(_write_output(tmp_path, [FAIL_A])),
        "--baseline", str(baseline),
        "--pytest-exit-code", "1",
        "--summary", str(summary),
    ])
    assert code == 0
    text = summary.read_text(encoding="utf-8")
    assert "exact match" in text
    assert "No action needed" in text


def test_write_baseline_round_trips(checker, tmp_path):
    baseline = tmp_path / "baseline_failures.txt"
    output = _write_output(tmp_path, [FAIL_A, FAIL_B])
    assert checker.main([
        "--pytest-output", str(output),
        "--baseline", str(baseline),
        "--write-baseline",
    ]) == 0
    text = baseline.read_text(encoding="utf-8")
    assert text.startswith("#")
    assert "DELETE" in text.upper()
    assert "Measured:" in text
    assert checker.load_baseline(baseline) == [FAIL_A, FAIL_B]

    # and the freshly written baseline now matches exactly
    assert checker.main([
        "--pytest-output", str(output),
        "--baseline", str(baseline),
        "--pytest-exit-code", "1",
    ]) == 0


# ── CLI exit codes end to end ───────────────────────────────────────────────

def _write_output(tmp_path, failures, passed=30):
    path = tmp_path / "pytest-output.txt"
    path.write_text(pytest_output(failures, passed=passed), encoding="utf-8")
    return path


def _run_cli(args):
    return subprocess.run(
        [sys.executable, str(CHECKER_PATH), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def test_cli_exit_codes(tmp_path):
    output = _write_output(tmp_path, [FAIL_A])
    baseline = write_baseline(tmp_path, [FAIL_A])

    match = _run_cli(["--pytest-output", str(output), "--baseline", str(baseline), "--pytest-exit-code", "1"])
    assert match.returncode == 0, match.stdout + match.stderr
    assert "OK: failure set matches the baseline exactly." in match.stdout

    new_baseline = write_baseline(tmp_path, [])
    drift = _run_cli(["--pytest-output", str(output), "--baseline", str(new_baseline), "--pytest-exit-code", "1"])
    assert drift.returncode == 1, drift.stdout + drift.stderr
    assert FAIL_A in drift.stdout

    stale_baseline = write_baseline(tmp_path, [FAIL_A, FAIL_B])
    stale = _run_cli(["--pytest-output", str(output), "--baseline", str(stale_baseline), "--pytest-exit-code", "1"])
    assert stale.returncode == 0, stale.stdout + stale.stderr
    assert FAIL_B in stale.stdout

    strict_stale = _run_cli(["--pytest-output", str(output), "--baseline", str(stale_baseline),
                             "--pytest-exit-code", "1", "--strict-stale"])
    assert strict_stale.returncode == 1
    assert FAIL_B in strict_stale.stdout

    missing = _run_cli(["--pytest-output", str(output), "--baseline", str(tmp_path / "nope.txt")])
    assert missing.returncode == 2

    absent_output = _run_cli(["--pytest-output", str(tmp_path / "nope.log"), "--baseline", str(baseline)])
    assert absent_output.returncode == 2
    assert "not found" in absent_output.stdout


# ── the checked-in artefacts ────────────────────────────────────────────────

def test_checked_in_baseline_exists_and_is_wellformed(checker):
    assert DEFAULT_BASELINE.is_file(), f"missing {DEFAULT_BASELINE}"
    raw = DEFAULT_BASELINE.read_text(encoding="utf-8")
    assert raw.startswith("#"), "baseline must start with a header comment"
    assert "Measured:" in raw
    assert "delete" in raw.lower()
    entries = checker.load_baseline(DEFAULT_BASELINE)
    # An empty baseline is the goal, not a fault: it means every known failure
    # has been fixed. Keep the header, and keep entries sorted and repo-scoped
    # whenever there are any.
    assert entries == sorted(entries)
    assert all(entry.startswith("riptide/tests/") for entry in entries)


def test_workflow_runs_the_checker():
    workflow = REPO_ROOT / ".github" / "workflows" / "pytest.yml"
    assert workflow.is_file()
    text = workflow.read_text(encoding="utf-8")
    assert "scripts/check_test_baseline.py" in text
    assert "GITHUB_STEP_SUMMARY" in text
