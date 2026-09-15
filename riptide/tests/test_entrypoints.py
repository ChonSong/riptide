"""
Entry-point import test.

Verifies that every module in riptide/ can be imported as `riptide.X`
— the correct cron execution model uses `python3 -m riptide.X` which
imports riptide as a package first, then finds X as a submodule.

Running as `python3 riptide/X.py` (without -m) makes Python treat
`riptide/` as the package directory, so `from riptide.Y import Z`
resolves to `riptide/riptide/Y.py` — wrong.

The fix: cron wrappers use `python3 -m riptide.X`, and __init__.py
bootstraps __path__ so submodules resolve correctly.

Generalizes to any repo: change PKG_NAME and REPO_ROOT.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

# This repo's layout — adjust PKG_NAME for other repos.
# test_entrypoints.py lives in riptide/tests/, so we go up 3 levels.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PKG_NAME = "riptide"
PKG_DIR = REPO_ROOT / PKG_NAME


def _import_module(module_name: str) -> tuple[bool, str]:
    """Import riptide.X and return (ok, output)."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {PKG_NAME}.{module_name}; print('OK')"],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": ""},  # No PYTHONPATH injection — pure test
    )
    ok = result.returncode == 0 and "OK" in result.stdout
    return ok, result.stdout + result.stderr


def test_modules_importable():
    """Every riptide/*.py that imports from riptide.* must import cleanly."""
    failures = []

    for py_file in sorted(PKG_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        # Only test files that actually import from the package
        content = py_file.read_text()
        if f"from {PKG_NAME}." not in content and f"import {PKG_NAME}." not in content:
            continue

        module_name = py_file.stem
        ok, output = _import_module(module_name)
        if not ok:
            failures.append((module_name, output))

    assert not failures, (
        f"{len(failures)} module(s) fail to import as `import riptide.X`:\n"
        + "\n".join(f"  {name}: {err[:300]}" for name, err in failures)
    )


def test_no_script_execution():
    """Running a module as `python3 riptide/X.py` is the wrong way and must fail fast.

    Two environments are legitimate, so TWO failure modes are accepted:

    * the package is NOT installed (e.g. CI's clean venv): the module's own
      ``from riptide.Y import Z`` cannot resolve, so Python dies immediately with
      ``ModuleNotFoundError: No module named 'riptide...'``;
    * the package IS installed (e.g. the dev venv, editable install): the import
      resolves, so the module itself must refuse script execution and emit the
      explicit guard message (see the ``__main__`` block of ``riptide/deepthink.py``).

    The old assertion accepted only ``ModuleNotFoundError``, which made the test
    environment-dependent: with the package installed that assertion could never
    hold, and ``deepthink.py`` — having no argparse — simply ignored ``--help``
    and started real work (polling GitHub) until the subprocess timed out. The
    test now asserts the INTENT (a direct invocation exits non-zero quickly and
    does no work) instead of an incidental exception type.
    """
    # Pick a representative file that imports from riptide.*
    test_file = PKG_DIR / "deepthink.py"
    if not test_file.exists():
        pytest.skip(f"deepthink.py not found at {test_file}")

    try:
        result = subprocess.run(
            [sys.executable, str(test_file), "--help"],
            capture_output=True,
            text=True,
            timeout=5,  # fail-fast budget: real work (polling/network) cannot fit
            cwd=str(REPO_ROOT),
            env={**os.environ, "PYTHONPATH": ""},
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"`python3 {test_file.relative_to(REPO_ROOT)} --help` did not exit "
            "within 5s — it is doing work (polling/network) instead of refusing "
            "to run as a script."
        )

    assert result.returncode != 0, (
        f"`python3 {test_file.relative_to(REPO_ROOT)}` exited 0 — a riptide module "
        "must not run as a script.\n"
        f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
    )

    guarded = "must be imported and run as a module" in result.stderr
    unimportable = (
        "ModuleNotFoundError" in result.stderr or "No module named" in result.stderr
    )
    assert guarded or unimportable, (
        "Expected `python3 riptide/X.py` to fail fast with either the explicit "
        "script-mode guard message or ModuleNotFoundError (package not installed); "
        "it failed some other way, which may mean it ran real work.\n"
        f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
    )

    # It must not have reached the module's normal work path at all.
    combined = result.stdout + result.stderr
    assert "Checking " not in combined, (
        "Script-mode invocation started polling (log line `Checking <repo>...`): "
        "the guard did not stop execution before doing work."
    )
