#!/usr/bin/env python3
"""test_oracle.py — Targeted test execution from PR diff changed files."""

from __future__ import annotations

import fnmatch
import subprocess
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Static file→test mapping.  Glob-style patterns on the right are resolved
# against the repository root at lookup time.
# ---------------------------------------------------------------------------

FILE_TEST_MAP: dict[str, list[str]] = {
    # Core pipeline / server
    "riptide/webhook.py": ["tests/test_webhook*.py", "tests/test_pipeline*.py"],
    "riptide/companion.py": ["tests/test_companion*.py", "tests/test_pipeline*.py"],
    "riptide/deepthink.py": ["tests/test_deepthink*.py", "tests/test_pipeline*.py"],
    "riptide/fixer.py": ["tests/test_fixer*.py"],
    "riptide/github_app.py": ["tests/test_github_app*.py"],
    "riptide/proofshotter.py": ["tests/test_proofshotter*.py"],
    "riptide/poller.py": ["tests/test_poller*.py"],
    "riptide/state.py": ["tests/test_state*.py", "tests/test_work_state.py"],
    # Pipeline submodules
    "riptide/pipeline/probe.py": ["tests/test_probe*.py", "tests/test_pipeline*.py"],
    "riptide/pipeline/assemble_review.py": ["tests/test_assemble_review*.py"],
    "riptide/pipeline/diagram_analyst.py": ["tests/test_diagram_analyst*.py"],
    "riptide/pipeline/interaction_handler.py": ["tests/test_interaction_handler*.py"],
    "riptide/pipeline/review_memory.py": ["tests/test_review_memory*.py"],
    # Helpers
    "riptide/diff_analyzer.py": ["tests/test_diff_analyzer*.py"],
    "riptide/context_bundle.py": ["tests/test_context_bundle*.py"],
    "riptide/labeler.py": ["tests/test_labeler*.py"],
    "riptide/orchestrator.py": ["tests/test_orchestrator*.py"],
    "riptide/gh_cli_client.py": ["tests/test_gh_cli*.py"],
    "riptide/visual.py": ["tests/test_visual*.py"],
    "riptide/depth.py": ["tests/test_depth*.py"],
    "riptide/assemble_review.py": ["tests/test_assemble_review*.py"],
}


def _resolve_patterns(root: str, patterns: list[str]) -> list[str]:
    """Resolve glob patterns against files that exist under *root*.

    Patterns that match nothing are dropped so pytest doesn't error on
    non-existent paths.  Returned paths are relative to *root*.
    """
    repo = Path(root)
    resolved: list[str] = []
    for pat in patterns:
        for p in repo.rglob(pat):
            try:
                rel = str(p.relative_to(repo))
            except ValueError:
                rel = str(p)
            resolved.append(rel)
    return sorted(set(resolved))


def map_files_to_tests(files_changed: list[str], root: str = ".") -> list[str]:
    """Map changed source files to their corresponding test files.

    Every file in *files_changed* is looked up in ``FILE_TEST_MAP``.  If a
    direct key match is not found, a prefix match (module directory) is
    attempted so that e.g. ``riptide/pipeline/foo.py`` pulls in tests for
    ``riptide/pipeline/*``.

    Glob patterns are resolved against the actual filesystem so that only
    test files that exist are returned.
    """
    tests: set[str] = set()

    for fpath in files_changed:
        # Direct match
        if fpath in FILE_TEST_MAP:
            tests |= set(_resolve_patterns(root, FILE_TEST_MAP[fpath]))
            continue

        # Prefix (directory) match — any ancestor directory in the map
        parts = fpath.split("/")
        matched = False
        for i in range(len(parts), 0, -1):
            ancestor = "/".join(parts[:i]) + "/*"
            if ancestor in FILE_TEST_MAP:
                tests |= set(_resolve_patterns(root, FILE_TEST_MAP[ancestor]))
                matched = True
                break

        # Fallback: convention-based (tests/test_<stem>.py)
        if not matched:
            stem = Path(fpath).stem
            candidate = f"tests/test_{stem}*.py"
            tests |= set(_resolve_patterns(root, [candidate]))

    return sorted(tests)


def find_missing_tests(files_changed: list[str], root: str = ".") -> list[str]:
    """Find changed source files that have **no** corresponding test files.

    Only considers files that are non-test, non-config, non-doc source
    modules.  Returns the subset of *files_changed* that lack any test
    coverage according to ``FILE_TEST_MAP`` and filesystem probing.
    """
    repo = Path(root)
    missing: list[str] = []

    skip_suffixes = (".md", ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".txt", ".lock")
    skip_dirs = ("tests/", "scripts/", "docs/", "docker/", ".github/")

    for fpath in files_changed:
        if fpath.endswith(skip_suffixes):
            continue
        if any(fpath.startswith(d) for d in skip_dirs):
            continue
        if fpath.startswith("tests/test_"):
            continue  # it's already a test

        stem = Path(fpath).stem
        convention = list(repo.rglob(f"tests/test_{stem}*.py"))

        # Check if the file is in FILE_TEST_MAP
        has_mapping = fpath in FILE_TEST_MAP
        if has_mapping:
            mapped_tests = _resolve_patterns(root, FILE_TEST_MAP[fpath])
            if mapped_tests or convention:
                continue
            missing.append(fpath)
        elif convention:
            continue
        else:
            missing.append(fpath)

    return missing


def run_tests(test_files: list[str], cwd: str) -> dict[str, Any]:
    """Run pytest on *test_files* and return structured results.

    Parameters
    ----------
    test_files:
        Paths (relative to *cwd*) of test files / directories to execute.
    cwd:
        Working directory for the pytest subprocess (usually the repo root).

    Returns
    -------
    dict with keys: ``tests_run``, ``passed``, ``failed``, ``missing_tests``,
    ``duration_s``.
    """
    start = time.monotonic()

    if not test_files:
        return {
            "tests_run": 0,
            "passed": 0,
            "failed": 0,
            "missing_tests": [],
            "duration_s": 0.0,
        }

    cmd = [
        "python", "-m", "pytest",
        *test_files,
        "-q",
        "--tb=no",
        "--no-header",
        "-p", "no:cacheprovider",
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=cwd,
    )
    duration = time.monotonic() - start

    # Parse "X passed, Y failed" from pytest output summary line
    passed = 0
    failed = 0
    tests_run = 0
    for line in (result.stdout + result.stderr).splitlines():
        line = line.strip()
        if "passed" in line or "failed" in line:
            for token in line.replace(",", " ").split():
                if token.isdigit():
                    tests_run += int(token)

    # A more precise parse for "N passed"
    import re
    m = re.search(r"(\d+)\s+passed", result.stdout + result.stderr)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", result.stdout + result.stderr)
    if m:
        failed = int(m.group(1))

    # If pytest exited non-zero but our parsing found nothing, assume
    # every file failed (crash on collection).
    if passed == 0 and failed == 0 and result.returncode != 0:
        failed = len(test_files)

    tests_run = passed + failed if (passed + failed) > 0 else tests_run

    return {
        "tests_run": tests_run,
        "passed": passed,
        "failed": failed,
        "missing_tests": [],
        "duration_s": round(duration, 2),
    }


def generate_test_report(
    owner: str,
    repo: str,
    pr_number: int,
    files_changed: list[str],
    root: str = ".",
) -> dict[str, Any]:
    """Main entry point: map changed files → run tests → build report.

    Returns a dict with keys::

        owner, repo, pr_number,
        files_changed, files_mapped, tests_run, passed, failed,
        missing_tests, duration_s, status
    """
    test_files = map_files_to_tests(files_changed, root=root)
    missing = find_missing_tests(files_changed, root=root)

    result = run_tests(test_files, cwd=root)
    result["missing_tests"] = missing

    status = "pass" if result["failed"] == 0 else "fail"
    if result["tests_run"] == 0:
        status = "skip"

    return {
        "owner": owner,
        "repo": repo,
        "pr_number": pr_number,
        "files_changed": files_changed,
        "files_mapped": test_files,
        "tests_run": result["tests_run"],
        "passed": result["passed"],
        "failed": result["failed"],
        "missing_tests": result["missing_tests"],
        "duration_s": result["duration_s"],
        "status": status,
    }