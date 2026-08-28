"""Tests for startup cleanup fix — ensure non-fatal behavior when DB is locked."""

from unittest.mock import patch

import pytest


class TestStartupCleanupNonFatal:
    """Verify that startup checkbox cleanup failure doesn't prevent server startup."""

    def test_startup_continues_when_cleanup_fails(self):
        """init_db should complete even if cleanup raises."""
        from riptide.webhook import init_db

        with patch("riptide.webhook._get_state_store") as mock_store:
            mock_store.side_effect = Exception("database is locked")
            # Should NOT raise — init_db catches the exception
            init_db()

    def test_startup_completes_normally(self):
        """init_db should complete normally when cleanup succeeds."""
        from riptide.webhook import init_db

        with patch("riptide.webhook._get_state_store") as mock_store:
            mock_store.return_value = type("MockStore", (), {
                "cleanup_stale_checkbox_triggers": lambda **kw: None
            })()
            # Should complete without error
            init_db()
