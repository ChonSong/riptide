# Riptide package
#
# Cron execution model fix: when run as `python3 riptide/X.py`, Python sets
# sys.path[0] to the script's directory (`riptide/`), NOT the repo root.
# Python finds the `riptide` package at `sys.path[0]/riptide/` and sets the
# package's __path__ to that location. Then `from riptide.Y import Z` looks
# for `state.py` inside `sys.path[0]/riptide/` — which doesn't exist.
#
# This bootstrap adds the repo root to the package's __path__ so submodule
# imports resolve correctly. The fix is viral: every file gets it for free.
# See tests/test_entrypoints.py for the constraint verification.
from pathlib import Path

_pkg_root = Path(__file__).resolve().parent.parent
if str(_pkg_root) not in __path__:
    __path__.insert(0, str(_pkg_root))
