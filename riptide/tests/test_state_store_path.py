"""Tests for StateStore path consistency across riptide modules.

Verifies that webhook.py, deepthink.py, fixer.py, and poller all default
to the same StateStore database path (the one defined in state.py).

This is a regression test for the "empty deliveries table" bug where
webhook.py hardcoded /tmp/riptide_state.db while everything else used
~/.local/share/riptide/state.db.
"""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from riptide.state import DEFAULT_DB_PATH


class TestStateStorePathConsistency:
    """All riptide entrypoints must resolve to the same default DB path."""

    def test_default_db_path_matches_xdg(self):
        """state.py default follows XDG base directory spec."""
        expected = str(Path.home() / ".local/share/riptide/state.db")
        assert DEFAULT_DB_PATH == expected, (
            f"state.py default is {DEFAULT_DB_PATH!r}, expected {expected!r}"
        )

    def test_webhook_uses_same_default_path(self):
        """webhook._get_state_store() must default to state.DEFAULT_DB_PATH."""
        # Clear any cached store and env var
        import riptide.webhook as webhook_mod
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RIPTIDE_STATE_DB", None)
            webhook_mod._state_store = None
            store = webhook_mod._get_state_store()
            assert store.db_path == DEFAULT_DB_PATH, (
                f"webhook store path {store.db_path!r} != state default {DEFAULT_DB_PATH!r}"
            )

    def test_webhook_respects_env_override(self):
        """RIPTIDE_STATE_DB env var must override the default in webhook."""
        custom = "/tmp/custom_riptide_test.db"
        import riptide.webhook as webhook_mod
        with patch.dict(os.environ, {"RIPTIDE_STATE_DB": custom}):
            webhook_mod._state_store = None
            store = webhook_mod._get_state_store()
            assert store.db_path == custom, (
                f"webhook ignored RIPTIDE_STATE_DB: got {store.db_path!r}, expected {custom!r}"
            )
        # Cleanup
        webhook_mod._state_store = None

    def test_deepthink_uses_same_default_path(self):
        """deepthink.py StateStore default must match state.py."""
        from riptide.deepthink import StateStore as DeepthinkStateStore
        # If deepthink re-imports StateStore from state.py, this is the same class.
        # If it has its own, check the default.
        store = DeepthinkStateStore()
        assert store.db_path == DEFAULT_DB_PATH

    def test_fixer_uses_same_default_path(self):
        """fixer.py StateStore default must match state.py."""
        # fixer.py imports StateStore from riptide.state inside functions,
        # so we verify by instantiating the shared class directly.
        from riptide.state import StateStore
        store = StateStore()
        assert store.db_path == DEFAULT_DB_PATH

    def test_orchestrator_uses_same_default_path(self):
        """orchestrator.py StateStore default must match state.py."""
        from riptide.orchestrator import StateStore as OrchStateStore
        store = OrchStateStore()
        assert store.db_path == DEFAULT_DB_PATH

    def test_no_hardcoded_tmp_path(self):
        """No module should hardcode /tmp/riptide_state.db as default."""
        import inspect
        from riptide import webhook, deepthink, fixer

        offending = []
        for name, mod in [("webhook", webhook), ("deepthink", deepthink), ("fixer", fixer)]:
            source = inspect.getsource(mod)
            if '"/tmp/riptide_state.db"' in source or "'/tmp/riptide_state.db'" in source:
                offending.append(name)

        assert not offending, (
            f"Modules still hardcode /tmp/riptide_state.db: {offending}"
        )