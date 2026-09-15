"""Hermetic state isolation for the Riptide test suite.

Test infrastructure only — production code is untouched.

WHY THIS IS A SEPARATE MODULE
-----------------------------
Isolation has to be in place before the *first* riptide import in the pytest
process, because nearly every state path in the package is resolved from the
ambient environment at import time (module-level constants, class defaults and
default arguments).  ``riptide/tests/__init__.py`` imports ``riptide.webhook``
at module scope, and pytest executes that package ``__init__`` before it runs
``riptide/tests/conftest.py``, so a conftest-only swap would be too late: by the
time conftest's body ran, ``riptide.webhook`` → ``riptide.state`` →
``riptide.review_memory`` → ``riptide.orchestrator`` → ``riptide.labeler`` had
already bound the developer's real paths.

So both ``riptide/tests/__init__.py`` (packaged, first) and
``riptide/tests/conftest.py`` (fixtures) import this module and call
:func:`apply`.

AMBIENT STATE PATHS THIS ISOLATES
---------------------------------
path (default)                                        how it is reached
----------------------------------------------------  -------------------------------
``$HOME/.hermes/state/riptide-work-state.json``       riptide/pipeline/work_state.py:14
                                                      ``WORK_STATE_PATH``
                                                      ($RIPTIDE_WORK_STATE) — plus the
                                                      ``<state-dir>/tmp*.tmp`` files
                                                      ``_write_state_unsafe()`` drops
                                                      beside it.
``$HOME/.local/share/riptide/state.db`` (+ .lock,      riptide/state.py:42
-wal, -shm)                                           ``DEFAULT_DB_PATH``
                                                      ($RIPTIDE_STATE_DB) — the bound
                                                      default of ``StateStore.__init__``,
                                                      touched/created by any bare
                                                      ``StateStore()``; also inlined at
                                                      riptide/webhook.py:170.
``$HOME/.local/share/riptide/metadata.db``            riptide/state.py:690
                                                      ``POLLER_DB_PATH``
                                                      (``Path.home()``, no env var).
``$HOME/.hermes/cron/jobs.json``                      riptide/deepthink.py:110
                                                      ``CRON_JOBS_PATH``
                                                      ($RIPTIDE_CRON_JOBS_PATH) — the
                                                      live Hermes cron store.
``$HOME/.local/share/riptide/documentarian.db``       riptide/documentarian.py:25
                                                      ``DEFAULT_DB_PATH``
                                                      ($RIPTIDE_DOCUMENTARIAN_DB).
``$HOME/.local/share/riptide/riptide.log``            written by server.py:32 (only when
                                                      the server entrypoint runs).
``/tmp/riptide``, ``/tmp/riptide-data``               riptide/webhook.py:224 (module-level
                                                      ``DATA_DIR.mkdir()`` — runs on
                                                      import), companion.py:371+:1243,
                                                      deepthink.py:66 (``STATE_FILE``),
                                                      proofshotter.py:56 (``STATE_FILE``),
                                                      server.py:27 ($RIPTIDE_DATA_DIR).
``/home/sc/workspace``                                riptide/fixer.py:58 ``WORKSPACE_ROOT``
                                                      ($RIPTIDE_WORKSPACE_ROOT);
                                                      companion.py:585
                                                      ``Path.home()/"workspace"``
                                                      ($RIPTIDE_REPO_DIR).
``/home/sc/workspace/<repo>/graphify-out``            riptide/grafiphy/orchestrator.py:104,
                                                      graphify_ingest/orchestrator.py:357
                                                      — reads ``GRAPHIFY_CWD``, a path
                                                      env var rather than a module
                                                      constant, so it is isolated via
                                                      ``HERMETIC_ENV`` (not
                                                      ``_TEMP_VALUES``, which can
                                                      only see module constants).
``/home/sc/workspace/proofshot/cli.py``               riptide/proofshotter.py:61
                                                      ``PROOFSHOT_CLI``
                                                      ($RIPTIDE_PROOFSHOT_CLI).
``$HOME/.hermes/hermes-agent/skills/…/excalidraw/     riptide/diagram_analyst.py:221,
scripts/upload.py``, ``$HOME/workspace/riptide/       riptide/pipeline/diagram_builder.py:355,
scripts/upload_excalidraw.py``                        riptide/grafiphy/excalidraw_renderer.py:1158
                                                      — existence probes whose hit/miss
                                                      differs between a dev box and CI.
``/tmp/riptide-review-<run>-<suffix>``,               riptide/pipeline/conductor.py:57,
``/tmp/riptide-review-findings-…-<uuid>.json``        riptide/pipeline/scribe.py:142.
                                                      Hardcoded absolute paths with no
                                                      env override: NOT redirectable from
                                                      the test side without editing
                                                      production code.  Deterministic per
                                                      (PR, role) and handled inside the
                                                      tests that use them.
``/tmp/custom_riptide_test.db``                       hardcoded in
                                                      riptide/tests/test_state_store_path.py:42
                                                      — fixed and machine-independent, so
                                                      it cannot make the failure set
                                                      ambient-dependent.  Left alone.

HOW
---
One throwaway root per session; ``HOME`` is the master switch (``Path.home()``
and ``~`` expansion both follow it) and the ``RIPTIDE_*`` overrides are set
explicitly as well, so a shell that exports them cannot leak in either.
``RIPTIDE_STATE_DB`` is set to exactly what the ``$HOME``-derived default
produces, so test_state_store_path.py's "the default is
``$HOME/.local/share/riptide/state.db``" assertions still hold.

Symptom this cures: the suite's failure count flipped between 27 and 28
depending on what was already in the developer's work-state file.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# One throwaway root for the whole session.
SESSION_TMP = Path(tempfile.mkdtemp(prefix="riptide-tests-"))

# Removal is registered at import time, not only in cleanup(): the session
# fixture's teardown does not run for `--collect-only`, an import error raised
# before the fixture is set up, or SIGKILL — and each such invocation would
# otherwise leak one /tmp root, unbounded across CI runs and developer shells.
atexit.register(shutil.rmtree, SESSION_TMP, ignore_errors=True)

ISOLATED_HOME = SESSION_TMP / "home"
ISOLATED_DATA = SESSION_TMP / "data"
ISOLATED_WORKSPACE = SESSION_TMP / "workspace"

HERMETIC_ENV = {
    # Master switch: work_state, state.POLLER_DB_PATH, deepthink.CRON_JOBS_PATH,
    # documentarian, and every Path.home() existence probe.
    "HOME": str(ISOLATED_HOME),
    # Module-level DATA_DIR / STATE_FILE defaults in webhook, companion,
    # deepthink, proofshotter and server.
    "RIPTIDE_DATA_DIR": str(ISOLATED_DATA),
    # Explicit, so a value exported by the developer's shell cannot leak in.
    "RIPTIDE_WORK_STATE": str(ISOLATED_HOME / ".hermes/state/riptide-work-state.json"),
    # Deliberately equal to the $HOME-derived default (see module docstring).
    "RIPTIDE_STATE_DB": str(ISOLATED_HOME / ".local/share/riptide/state.db"),
    "RIPTIDE_DOCUMENTARIAN_DB": str(
        ISOLATED_HOME / ".local/share/riptide/documentarian.db"
    ),
    "RIPTIDE_CRON_JOBS_PATH": str(ISOLATED_HOME / ".hermes/cron/jobs.json"),
    "RIPTIDE_WORKSPACE_ROOT": str(ISOLATED_WORKSPACE),
    "RIPTIDE_REPO_DIR": str(ISOLATED_WORKSPACE),
    # grafiphy/graphify_ingest resolve os.environ.get("GRAPHIFY_CWD",
    # f"/home/sc/workspace/{repo}") at call time — a path read, not a module
    # constant, so _TEMP_VALUES cannot see it. Without this the suite reads the
    # developer's real graphify-out/graph.json (measured: 7 opens in a full run),
    # which is gitignored and absent from a clean checkout.
    "GRAPHIFY_CWD": str(ISOLATED_WORKSPACE),
    "RIPTIDE_PROOFSHOT_CLI": str(ISOLATED_WORKSPACE / "proofshot" / "cli.py"),
}

# Module-level path constants that must resolve inside SESSION_TMP, with the
# isolated value each one should hold.  Checked (and repaired) by verify()/heal()
# so a future reordering of the import sequence, or a module reload, fails loudly
# instead of silently writing into the developer's real state again.
_TEMP_VALUES = {
    ("riptide.state", "DEFAULT_DB_PATH"): lambda: HERMETIC_ENV["RIPTIDE_STATE_DB"],
    ("riptide.state", "POLLER_DB_PATH"): lambda: ISOLATED_HOME
    / ".local/share/riptide/metadata.db",
    ("riptide.webhook", "DATA_DIR"): lambda: ISOLATED_DATA,
    ("riptide.webhook", "METADATA_DB"): lambda: ISOLATED_DATA / "metadata.db",
    ("riptide.deepthink", "STATE_FILE"): lambda: ISOLATED_DATA
    / "deepthink_acted_prs.json",
    ("riptide.deepthink", "CRON_JOBS_PATH"): lambda: ISOLATED_HOME
    / ".hermes/cron/jobs.json",
    ("riptide.proofshotter", "STATE_FILE"): lambda: ISOLATED_DATA
    / "proofshotter_acted_prs.json",
    ("riptide.proofshotter", "PROOFSHOT_CLI"): lambda: ISOLATED_WORKSPACE
    / "proofshot" / "cli.py",
    ("riptide.poller", "DATA_DIR"): lambda: ISOLATED_DATA,
    ("riptide.poller", "DB_PATH"): lambda: ISOLATED_DATA / "metadata.db",
    ("riptide.pipeline.work_state", "WORK_STATE_PATH"): lambda: HERMETIC_ENV[
        "RIPTIDE_WORK_STATE"
    ],
    ("riptide.documentarian", "DEFAULT_DB_PATH"): lambda: HERMETIC_ENV[
        "RIPTIDE_DOCUMENTARIAN_DB"
    ],
    ("riptide.fixer", "WORKSPACE_ROOT"): lambda: HERMETIC_ENV[
        "RIPTIDE_WORKSPACE_ROOT"
    ],
}


def apply() -> Path:
    """Recreate the throwaway state dirs and repoint every state path.

    Idempotent; safe to call from both ``riptide/tests/__init__.py`` (before its
    riptide imports) and every test's autouse fixture.
    """
    for directory in (
        ISOLATED_HOME / ".hermes/state",
        ISOLATED_HOME / ".hermes/cron",
        ISOLATED_HOME / ".local/share/riptide",
        ISOLATED_DATA,
        ISOLATED_WORKSPACE,
        ISOLATED_WORKSPACE / "proofshot",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    os.environ.update(HERMETIC_ENV)
    return SESSION_TMP


def _is_isolated(value) -> bool:
    return str(value).startswith(str(SESSION_TMP))


def heal() -> list:
    """Re-point any already-imported module constant that drifted outside SESSION_TMP.

    Drift happens because a couple of tests reconfigure a module by reloading it
    inside ``patch.dict(os.environ, {}, clear=True)``:
    ``test_deepthink_config.py:16-35`` (reloads riptide.deepthink) and
    ``test_fixer.py:70-94`` (reloads riptide.fixer).  The reload re-binds the
    module-level paths from a *cleared* environment, i.e. back to the developer's
    real defaults (``~/.hermes/cron/jobs.json``, ``/tmp/riptide-data/…``,
    ``/home/sc/workspace``).  The reload cannot be prevented from here, but the
    drift is undone before the next test runs — the autouse ``hermetic_state``
    fixture calls this — so later tests cannot reach the real state, and a dev box
    behaves like CI.  Tests that reload a module only assert provider/model
    constants and never touch a state path.

    Returns the names of the constants that had to be repointed.
    """
    healed = []
    for (module_name, attribute), temp_value in _TEMP_VALUES.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        current = getattr(module, attribute, None)
        if current is None or _is_isolated(current):
            continue
        desired = temp_value()
        if isinstance(current, Path):
            desired = type(current)(str(desired))
        setattr(module, attribute, desired)
        healed.append(f"{module_name}.{attribute}")

    # StateStore.__init__'s default is bound at class-definition time, so a
    # reload of riptide.state would need this too (no test reloads it today; kept
    # as insurance).
    state_module = sys.modules.get("riptide.state")
    state_store = getattr(state_module, "StateStore", None) if state_module else None
    defaults = getattr(getattr(state_store, "__init__", None), "__defaults__", None)
    if defaults and not _is_isolated(defaults[0]):
        state_store.__init__.__defaults__ = (
            HERMETIC_ENV["RIPTIDE_STATE_DB"],
        ) + tuple(defaults[1:])
        healed.append("riptide.state.StateStore.__init__.__defaults__[0]")
    return healed


def verify() -> None:
    """Fail loudly if any already-imported module still points outside SESSION_TMP.

    Only inspects modules that are already in ``sys.modules`` (a module imported
    later resolves its constants from the environment, which apply() has already
    redirected).
    """
    offenders = []
    for module_name, attribute in _TEMP_VALUES:
        module = sys.modules.get(module_name)
        if module is None:
            continue
        value = getattr(module, attribute, None)
        if value is None or _is_isolated(value):
            continue
        offenders.append(f"{module_name}.{attribute} = {value!r}")
    if offenders:
        raise RuntimeError(
            "test run is NOT hermetic — these state paths point outside the "
            f"session temp root {SESSION_TMP}: " + "; ".join(offenders)
        )


def cleanup() -> None:
    """Remove the throwaway root (best effort)."""
    shutil.rmtree(SESSION_TMP, ignore_errors=True)
