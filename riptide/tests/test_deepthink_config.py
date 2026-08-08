"""Tests for deepthink provider/model configuration."""

import os
import sys
from unittest.mock import patch

import pytest


class TestDeepthinkProviderConfig:
    """Verify deepthink spawns jobs with the correct provider pin."""

    def test_default_provider_is_longcat(self):
        """Default provider must be 'longcat', not 'custom'."""
        # Remove env var so we test the default
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("RIPTIDE_DEEPTHINK_PROVIDER", None)
            os.environ.pop("RIPTIDE_DEEPTHINK_MODEL", None)
            # Force re-import
            import importlib
            import riptide.deepthink
            importlib.reload(riptide.deepthink)
            assert riptide.deepthink.DEEPTHINK_PROVIDER == "longcat", (
                f"Expected DEEPTHINK_PROVIDER='longcat', got '{riptide.deepthink.DEEPTHINK_PROVIDER}'. "
                f"provider='custom' resolves to OpenRouter, not LongCat."
            )

    def test_default_model_is_longcat(self):
        """Default model must reference LongCat-2.0."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("RIPTIDE_DEEPTHINK_PROVIDER", None)
            os.environ.pop("RIPTIDE_DEEPTHINK_MODEL", None)
            import importlib
            import riptide.deepthink
            importlib.reload(riptide.deepthink)
            assert "LongCat" in riptide.deepthink.DEEPTHINK_MODEL, (
                f"Expected LongCat model, got '{riptide.deepthink.DEEPTHINK_MODEL}'"
            )

    def test_provider_longcat_resolves_correctly(self):
        """Provider 'longcat' must resolve to LongCat base_url."""
        # Skip if hermes_cli not available
        pytest.importorskip("hermes_cli.runtime_provider")
        from hermes_cli.runtime_provider import resolve_runtime_provider

        result = resolve_runtime_provider(requested="longcat")
        assert result["base_url"] == "https://api.longcat.chat/openai"
        # provider may be 'custom' (normalized) but base_url must be LongCat's


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
