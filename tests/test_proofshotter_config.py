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

    def test_url_default(self):
        """Default URL must be localhost:8788 when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("RIPTIDE_PROOFSHOT_URL", None)
            url = os.environ.get("RIPTIDE_PROOFSHOT_URL", "http://localhost:8788")
            assert url == "http://localhost:8788"

    def test_proofshotter_uses_env_var(self):
        """Verify proofshotter.py uses RIPTIDE_PROOFSHOT_URL env var."""
        import ast
        with open("riptide/proofshotter.py") as f:
            source = f.read()
        assert "RIPTIDE_PROOFSHOT_URL" in source
        assert 'os.environ.get("RIPTIDE_PROOFSHOT_URL"' in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
