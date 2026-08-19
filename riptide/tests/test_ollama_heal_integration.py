# riptide/tests/test_ollama_heal_integration.py
"""
Tests for ollama_heal integration with Companion._generate_eli5().

Verifies that when Ollama goes down, the bot attempts to self-heal
via ollama_heal.heal() before skipping enrichment.
"""

import pytest
from unittest.mock import patch, MagicMock

from riptide.companion import Companion


def make_companion(tmp_path=None):
    """Create a Companion instance with mocked github client and disabled warm-up."""
    client = MagicMock()
    with patch("threading.Thread"):
        from riptide.state import StateStore
        import tempfile as _tempfile
        from pathlib import Path

        state_dir = tmp_path if tmp_path else _tempfile.mkdtemp(prefix="companion-test-")
        store = StateStore(str(Path(state_dir) / "state.db"))
        companion = Companion(client, state_store=store)
    if tmp_path:
        companion._alert_file = tmp_path / "companion_alerts.json"
    return companion


class TestOllamaHealIntegration:
    """Tests for ollama_heal.heal() integration in _generate_eli5()."""

    def test_heal_succeeds_proceeds_with_ollama_call(self):
        """When heal() returns 0 (healthy), _generate_eli5 proceeds normally."""
        companion = make_companion()
        files = [{"filename": "src/main.py"}]

        with patch("riptide.ollama_heal.heal", return_value=0) as mock_heal:
            with patch.object(companion, "_ollama_call", return_value="It's like adding a new room.") as mock_call:
                result = companion._generate_eli5("feat: add feature", files)

                mock_heal.assert_called_once()
                mock_call.assert_called_once()
                assert result == "It's like adding a new room."

    def test_heal_fails_returns_none_skips_ollama(self):
        """When heal() returns 1 (down, unrecoverable), skip Ollama and return None."""
        companion = make_companion()
        files = [{"filename": "src/main.py"}]

        with patch("riptide.ollama_heal.heal", return_value=1) as mock_heal:
            with patch.object(companion, "_ollama_call") as mock_call:
                result = companion._generate_eli5("feat: add feature", files)

                mock_heal.assert_called_once()
                mock_call.assert_not_called()
                assert result is None

    def test_heal_returns_exit_code_2_skips_ollama(self):
        """When heal() returns 2 (systemd not found), skip Ollama and return None."""
        companion = make_companion()
        files = [{"filename": "src/main.py"}]

        with patch("riptide.ollama_heal.heal", return_value=2) as mock_heal:
            with patch.object(companion, "_ollama_call") as mock_call:
                result = companion._generate_eli5("feat: add feature", files)

                mock_heal.assert_called_once()
                mock_call.assert_not_called()
                assert result is None

    def test_heal_called_before_ollama_call(self):
        """Verify heal() is called BEFORE the Ollama API call."""
        companion = make_companion()
        files = [{"filename": "src/main.py"}]
        call_order = []

        with patch("riptide.ollama_heal.heal", return_value=0) as mock_heal:
            with patch.object(companion, "_ollama_call", return_value="ELI5 text") as mock_call:
                mock_heal.side_effect = lambda: (call_order.append("heal"), 0)[1]
                mock_call.side_effect = lambda p: (call_order.append("ollama_call"), "ELI5 text")[1]

                companion._generate_eli5("feat: add feature", files)

                assert call_order == ["heal", "ollama_call"]

    def test_eli5_with_delta_flag_and_heal_success(self):
        """Heal success + is_delta=True should still work."""
        companion = make_companion()
        files = [{"filename": "src/main.py"}]

        with patch("riptide.ollama_heal.heal", return_value=0):
            with patch.object(companion, "_ollama_call", return_value="It's like a new room.") as mock_call:
                result = companion._generate_eli5("feat: add feature", files, is_delta=True)

                assert result == "It's like a new room."
                # Verify delta context is in prompt
                prompt = mock_call.call_args[0][0]
                assert "new changes in this push of" in prompt