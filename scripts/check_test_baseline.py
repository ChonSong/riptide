#!/usr/bin/env python3
"""Fail CI when the pytest failure set drifts from the checked-in baseline.

The repo has a set of pre-existing, known-failing tests. Without a baseline a
red suite is indistinguishable from a green one, so regressions stay invisible.
This checker pins the *exact* set of failing node IDs in
``riptide/tests/baseline_failures.txt`` and fails when that set changes.

Exit codes
----------
0   the current failure set is exactly the baseline
1   drift: one or more NEW failures, and/or one or more STALE baseline entries
2   the check could not be evaluated (missing/malformed baseline, unusable
    pytest output, or pytest could not run)

Usage
-----
    # run the suite and compare against the default baseline
    python scripts/check_test_baseline.py

    # compare an existing pytest output file (no suite run)
    python scripts/check_test_baseline.py --pytest-output /tmp/pytest.log

    # regenerate the baseline from a run
    python scripts/check_test_baseline.py --write-baseline

    # markdown summary for $GITHUB_STEP_SUMMARY
    python scripts/check_test_baseline.py --summary /tmp/summary.md

Runs are always executed from the repo root so node IDs are relative and
stable. Pytest is invoked as ``<current interpreter> -m pytest``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = REPO_ROOT / "riptide" / "tests" / "baseline_failures.txt"
DEFAULT_TESTS = "riptide/tests"

# -rf  : short summary listing "FAILED <nodeid>" lines (what we parse)
# --tb=no: keep output small and deterministic
# -p no:cacheprovider: no .pytest_cache writes from CI
PYTEST_ARGS = ["-q", "-rf", "--tb=no", "-p", "no:cacheprovider"]

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2

# "FAILED riptide/tests/test_x.py::TestA::test_b - AssertionError: ..."
_SHORT_SUMMARY_RE = re.compile(r"^\s*(FAILED|ERROR)\s+(\S+)", re.MULTILINE)
# verbose progress lines: "riptide/tests/test_x.py::TestA::test_b FAILED"
_PROGRESS_RE = re.compile(
    r"^(\S+\.py::\S+?)\s+(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED)\b", re.MULTILINE
)
# trailing counts line: "28 failed, 1096 passed, 4 skipped in 410.12s"
_COUNT_TOKEN_RE = re.compile(
    r"(\d+)\s+(failed|passed|error|errors|skipped|xfailed|xpassed|deselected)"
)
_SUMMARY_LINE_RE = re.compile(r"\bin\s+\d+(?:\.\d+)?s\b")
# a plausible pytest node id
_NODE_ID_RE = re.compile(r"^[^\s:]+\.py(::[^\s]+)?$")

BASELINE_HEADER = """\
# Riptide known-failure baseline.
#
# Measured: {measured} with:
#   {cmd}
# ({failed} failed, {passed} passed — these failures are pre-existing, not
# introduced by the change under review.)
#
# One failing pytest node ID per line, sorted. DELETE the line as soon as the
# test is fixed: a stale entry fails CI just like a new failure does. Add lines
# only for failures that are genuinely known and tracked.
#
# Enforced by scripts/check_test_baseline.py via .github/workflows/pytest.yml
"""


class BaselineError(Exception):
    """Baseline file is missing, unreadable or malformed."""


@dataclass
class PytestOutput:
    """Parsed pytest output."""

    failures: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    has_summary: bool = False
    unparsable: list[str] = field(default_factory=list)

    @property
    def failure_set(self) -> set[str]:
        return set(self.failures)


@dataclass
class Report:
    """Outcome of a baseline comparison."""

    status: str  # "match" | "drift" | "error"
    exit_code: int
    baseline_path: Path
    failures: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    baseline_entries: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    pytest_exit_code: int | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == EXIT_OK

    def text_report(self) -> str:
        lines = ["pytest baseline check"]
        lines.append(f"  baseline : {_display(self.baseline_path)} ({self.baseline_entries} entries)")
        if self.detail:
            lines.append(f"  detail   : {self.detail}")
        if self.counts:
            parts = [f"{n} {k}" for k, n in self.counts.items()]
            lines.append(f"  counts   : {', '.join(parts)}")
        if self.pytest_exit_code is not None:
            lines.append(f"  pytest   : exit {self.pytest_exit_code}")
        lines.append(f"  failing  : {len(self.failures)} node id(s)")
        lines.append(f"  NEW      : {len(self.new)}")
        lines.append(f"  STALE    : {len(self.stale)}")
        if self.new:
            lines.append("")
            lines.append("NEW failures not in the baseline — fix them or, only if")
            lines.append("genuinely known, add them to the baseline:")
            lines.extend(f"  + {nid}" for nid in self.new)
        if self.stale:
            lines.append("")
            lines.append("STALE baseline entries that now PASS — delete them from")
            lines.append(f"{_display(self.baseline_path)}:")
            lines.extend(f"  - {nid}" for nid in self.stale)
        lines.append("")
        if self.status == "match":
            lines.append("OK: failure set matches the baseline exactly.")
        elif self.status == "drift":
            lines.append(
                f"FAIL: failure set drifted from the baseline "
                f"({len(self.new)} new, {len(self.stale)} stale)."
            )
        else:
            lines.append(f"ERROR: baseline check could not be evaluated ({self.detail}).")
        return "\n".join(lines)

    def markdown_report(self) -> str:
        if self.status == "match":
            head = f"## ✅ pytest baseline: exact match"
            body = (
                f"{len(self.failures)} known failure(s) match "
                f"`{_display(self.baseline_path)}` exactly."
            )
        elif self.status == "drift":
            head = "## ❌ pytest baseline: drift detected"
            body = (
                f"{len(self.new)} new failure(s) and {len(self.stale)} stale "
                f"baseline entr(ies)."
            )
        else:
            head = "## ❌ pytest baseline: could not evaluate"
            body = self.detail or "unknown error"
        lines = [head, "", body, ""]
        if self.counts:
            parts = [f"{n} {k}" for k, n in self.counts.items()]
            lines.append(f"`{', '.join(parts)}`")
            lines.append("")
        if self.pytest_exit_code is not None:
            lines.append(f"pytest exit code: `{self.pytest_exit_code}`")
            lines.append("")
        if self.new:
            lines.append(f"### New failures ({len(self.new)})")
            lines.append("")
            lines.extend(f"- `{nid}`" for nid in self.new)
            lines.append("")
        if self.stale:
            lines.append(f"### Stale baseline entries — now passing ({len(self.stale)})")
            lines.append("")
            lines.append(f"Delete these from `{_display(self.baseline_path)}`:")
            lines.append("")
            lines.extend(f"- `{nid}`" for nid in self.stale)
            lines.append("")
        if self.status == "match":
            lines.append("No action needed.")
        return "\n".join(lines).rstrip() + "\n"


def _display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except (ValueError, OSError):
        return str(path)


def normalize_node_id(node_id: str) -> str:
    """Make a node id path-relative so runs from different roots still match."""
    node_id = node_id.strip()
    while node_id.startswith("./"):
        node_id = node_id[2:]
    prefix = str(REPO_ROOT) + "/"
    if node_id.startswith(prefix):
        node_id = node_id[len(prefix):]
    return node_id


def parse_pytest_output(text: str) -> PytestOutput:
    """Extract failing node IDs and the trailing counts from pytest output."""
    out = PytestOutput()
    seen: dict[str, None] = {}

    for _kind, raw in _SHORT_SUMMARY_RE.findall(text):
        node_id = normalize_node_id(raw)
        seen.setdefault(node_id, None)
        if not _NODE_ID_RE.match(node_id):
            out.unparsable.append(node_id)
    for raw, status in _PROGRESS_RE.findall(text):
        if status in ("FAILED", "ERROR"):
            seen.setdefault(normalize_node_id(raw), None)

    out.failures = sorted(seen)

    # trailing summary line, searched bottom-up
    for line in reversed(text.splitlines()):
        if _SUMMARY_LINE_RE.search(line):
            tokens = _COUNT_TOKEN_RE.findall(line)
            if tokens:
                counts: dict[str, int] = {}
                for number, word in tokens:
                    word = "error" if word == "errors" else word
                    counts[word] = counts.get(word, 0) + int(number)
                out.counts = counts
                out.has_summary = True
                break
    return out


def load_baseline(path: Path) -> list[str]:
    """Read the baseline file, skipping blanks and ``#`` comments."""
    if not path.exists():
        raise BaselineError(f"baseline file not found: {path}")
    if not path.is_file():
        raise BaselineError(f"baseline path is not a file: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BaselineError(f"baseline file unreadable: {path} ({exc})") from exc

    entries: list[str] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        node_id = normalize_node_id(stripped)
        if not _NODE_ID_RE.match(node_id):
            raise BaselineError(
                f"malformed baseline entry at {_display(path)}:{lineno}: {stripped!r} "
                "(expected a pytest node id like riptide/tests/test_x.py::test_y)"
            )
        entries.append(node_id)
    return sorted(set(entries))


def compare(baseline: list[str] | set[str], current: list[str] | set[str]) -> tuple[list[str], list[str]]:
    """Return ``(new, stale)`` — new failures, baseline entries that now pass."""
    baseline_set, current_set = set(baseline), set(current)
    new = sorted(current_set - baseline_set)
    stale = sorted(baseline_set - current_set)
    return new, stale


def evaluate(
    output_text: str,
    baseline_path: Path = DEFAULT_BASELINE,
    pytest_exit_code: int | None = None,
) -> Report:
    """Compare a pytest output blob against the baseline and produce a Report."""
    try:
        baseline = load_baseline(baseline_path)
    except BaselineError as exc:
        return Report(
            status="error",
            exit_code=EXIT_ERROR,
            baseline_path=baseline_path,
            pytest_exit_code=pytest_exit_code,
            detail=str(exc),
        )

    parsed = parse_pytest_output(output_text)
    report = Report(
        status="match",
        exit_code=EXIT_OK,
        baseline_path=baseline_path,
        failures=parsed.failures,
        baseline_entries=len(baseline),
        counts=parsed.counts,
        pytest_exit_code=pytest_exit_code,
    )

    if pytest_exit_code == 5:
        report.status = "error"
        report.exit_code = EXIT_ERROR
        report.detail = "pytest collected no tests (exit 5)"
        return report

    if not parsed.failures and not parsed.has_summary:
        report.status = "error"
        report.exit_code = EXIT_ERROR
        report.detail = "could not parse pytest output (no summary line, no failures)"
        return report

    if parsed.unparsable:
        report.status = "error"
        report.exit_code = EXIT_ERROR
        report.detail = f"unrecognised failure entry: {parsed.unparsable[0]!r}"
        return report

    report.new, report.stale = compare(baseline, parsed.failures)
    if report.new or report.stale:
        report.status = "drift"
        report.exit_code = EXIT_DRIFT
    return report


def run_pytest(tests: str, timeout: int | None = None) -> tuple[int, str]:
    """Run pytest from the repo root; return ``(exit_code, combined output)``."""
    cmd = [sys.executable, "-m", "pytest", tests, *PYTEST_ARGS]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise BaselineError(f"could not run pytest: {exc}") from exc
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - environment dependent
        output = exc.output or ""
        if isinstance(output, bytes):  # pragma: no cover - defensive
            output = output.decode("utf-8", "replace")
        raise BaselineError(f"pytest timed out after {timeout}s") from None
    return proc.returncode, proc.stdout or ""


def write_baseline(path: Path, failures: list[str], command: str, counts: dict[str, int]) -> None:
    """(Re)write the baseline file with a dated header."""
    header = BASELINE_HEADER.format(
        measured=date.today().isoformat(),
        cmd=command,
        failed=counts.get("failed", len(failures)) + counts.get("error", 0),
        passed=counts.get("passed", 0),
    )
    body = "".join(f"{nid}\n" for nid in sorted(set(failures)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + body, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare the pytest failure set with the checked-in baseline."
    )
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE,
                        help=f"baseline file (default: {_display(DEFAULT_BASELINE)})")
    parser.add_argument("--tests", default=DEFAULT_TESTS,
                        help=f"pytest target when running the suite (default: {DEFAULT_TESTS})")
    parser.add_argument("--pytest-output", type=Path, default=None,
                        help="read pytest output from this file instead of running the suite")
    parser.add_argument("--pytest-exit-code", type=int, default=None,
                        help="pytest exit code to assume with --pytest-output (default: 1 if failures)")
    parser.add_argument("--summary", type=Path, default=None,
                        help="also write a markdown summary to this path (for $GITHUB_STEP_SUMMARY)")
    parser.add_argument("--write-baseline", action="store_true",
                        help="write the current failure set to the baseline file and exit 0")
    parser.add_argument("--timeout", type=int, default=None,
                        help="seconds before the pytest run is aborted")
    parser.add_argument("--quiet", action="store_true",
                        help="do not echo the raw pytest output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    baseline_path: Path = args.baseline if args.baseline.is_absolute() else REPO_ROOT / args.baseline
    summary_path: Path | None = args.summary

    exit_code = EXIT_ERROR
    output_text = ""
    pytest_exit_code: int | None
    command: str

    try:
        if args.pytest_output is not None:
            out_path = args.pytest_output
            if not out_path.is_absolute():
                out_path = Path.cwd() / out_path
            if not out_path.exists():
                raise BaselineError(f"pytest output file not found: {out_path}")
            output_text = out_path.read_text(encoding="utf-8", errors="replace")
            if args.pytest_exit_code is not None:
                pytest_exit_code = args.pytest_exit_code
            else:
                pytest_exit_code = 1 if parse_pytest_output(output_text).failures else 0
            command = f"read from {out_path}"
        else:
            command = f"{sys.executable} -m pytest {args.tests} {' '.join(PYTEST_ARGS)}"
            pytest_exit_code, output_text = run_pytest(args.tests, timeout=args.timeout)
            if not args.quiet:
                print(output_text, end="" if output_text.endswith("\n") else "\n")

        if args.write_baseline:
            # Regenerating the baseline must not require one to exist yet.
            if pytest_exit_code == 5:
                report = Report(status="error", exit_code=EXIT_ERROR,
                                baseline_path=baseline_path, pytest_exit_code=pytest_exit_code,
                                detail="pytest collected no tests (exit 5); refusing to write a baseline")
                print(report.text_report())
                exit_code = EXIT_ERROR
            else:
                parsed = parse_pytest_output(output_text)
                if not parsed.failures and not parsed.has_summary:
                    report = Report(status="error", exit_code=EXIT_ERROR,
                                    baseline_path=baseline_path, pytest_exit_code=pytest_exit_code,
                                    detail="could not parse pytest output; refusing to write a baseline")
                    print(report.text_report())
                    exit_code = EXIT_ERROR
                else:
                    write_baseline(baseline_path, parsed.failures, command, parsed.counts)
                    counts = ", ".join(f"{n} {k}" for k, n in parsed.counts.items()) or "n/a"
                    print(f"wrote {len(parsed.failures)} failing node id(s) to "
                          f"{_display(baseline_path)} ({counts})")
                    exit_code = EXIT_OK
        else:
            report = evaluate(output_text, baseline_path, pytest_exit_code)
            print(report.text_report())
            exit_code = report.exit_code
            if summary_path is not None:
                _append_summary(summary_path, report.markdown_report())
    except BaselineError as exc:
        report = Report(
            status="error",
            exit_code=EXIT_ERROR,
            baseline_path=baseline_path,
            detail=str(exc),
        )
        print(report.text_report())
        exit_code = EXIT_ERROR
        if summary_path is not None:
            _append_summary(summary_path, report.markdown_report())

    return exit_code


def _append_summary(path: Path, markdown: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(markdown)
    except OSError as exc:  # pragma: no cover - environment dependent
        print(f"warning: could not write summary to {path}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
