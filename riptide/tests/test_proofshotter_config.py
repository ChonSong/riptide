"""Tests for proofshotter URL configuration."""

import os
from unittest.mock import patch

import pytest


class TestProofshotterUrlConfig:
    """Verify proofshotter uses configurable URL."""

    def test_url_env_var_override(self):
        """URL can be overridden via environment variable."""
        with patch.dict(os.environ, {"RIPTIDE_PROOFSHOT_URL": "http://example.com:9000"}):
            url = os.environ.get("RIPTIDE_PROOFSHOT_URL", "http://localhost:8788")
            assert url == "http://example.com:9000"

    def test_no_url_default(self):
        """There is no default target: an undeclared repo is skipped, not captured.

        This used to assert the literal fallback `http://localhost:8788`, which was
        the module's real behaviour — so any repo with a UI change captured
        whatever sat on the developer's dev port and posted it as that PR's
        evidence. A target must now be declared.
        """
        with patch.dict(os.environ, {}, clear=True):
            from riptide.proofshotter import _resolve_capture_target

            assert _resolve_capture_target({}) is None

    def test_proofshotter_uses_env_var(self):
        """Verify proofshotter.py uses RIPTIDE_PROOFSHOT_URL env var."""
        import ast
        with open("riptide/proofshotter.py") as f:
            source = f.read()
        assert "RIPTIDE_PROOFSHOT_URL" in source
        assert 'os.environ.get("RIPTIDE_PROOFSHOT_URL"' in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
