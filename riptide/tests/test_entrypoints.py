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
    """Verify that running as `python3 riptide/X.py` fails (the wrong way)."""
    # Pick a representative file that imports from riptide.*
    test_file = PKG_DIR / "deepthink.py"
    if not test_file.exists():
        pytest.skip(f"deepthink.py not found at {test_file}")

    result = subprocess.run(
        [sys.executable, str(test_file), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": ""},
    )
    # This SHOULD fail with ModuleNotFoundError — that's the constraint we're testing
    assert "ModuleNotFoundError" in result.stderr or "No module named" in result.stderr, (
        "Expected `python3 riptide/X.py` to fail with ModuleNotFoundError. "
        "If it succeeded, the __init__.py fix may be masking the real issue."
    )
