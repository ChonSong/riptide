# riptide/tests/test_ollama_heal.py
"""
Tests for ollama_heal integration with Companion._generate_eli5().

Verifies that when Ollama goes down, the bot attempts to self-heal
via ollama_heal.heal() before skipping enrichment.
"""

import pytest
from unittest.mock import patch, MagicMock

from riptide.companion import Companion
from riptide import ollama_heal


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
            with patch.object(
                companion, "_ollama_call", return_value="It's like adding a new room."
            ) as mock_call:
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
            with patch.object(
                companion, "_ollama_call", return_value="It's like a new room."
            ) as mock_call:
                result = companion._generate_eli5("feat: add feature", files, is_delta=True)

                assert result == "It's like a new room."
                # Verify delta context is in prompt
                prompt = mock_call.call_args[0][0]
                assert "new changes in this push of" in prompt


class TestSystemdDetection:
    """Tests for is_systemd_available() and Docker/non-systemd fallback in heal()."""

    def test_is_systemd_available_with_dbus_and_runtime_dir(self):
        """is_systemd_available() returns True when DBUS and /run/user/<uid> exist."""
        with patch.dict("os.environ", {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}):
            with patch("os.path.exists", return_value=True):
                assert ollama_heal.is_systemd_available() is True

    def test_is_systemd_available_missing_dbus(self):
        """is_systemd_available() returns False when DBUS_SESSION_BUS_ADDRESS is unset."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("os.path.exists", return_value=True):
                assert ollama_heal.is_systemd_available() is False

    def test_is_systemd_available_missing_runtime_dir(self):
        """is_systemd_available() returns False when /run/user/<uid> is missing."""
        with patch.dict("os.environ", {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}):
            with patch("os.path.exists", return_value=False):
                assert ollama_heal.is_systemd_available() is False

    def test_heal_docker_returns_1_when_ollama_down(self):
        """In Docker (no systemd), heal() returns 1 when Ollama is unreachable."""
        with patch.object(ollama_heal, "is_healthy", return_value=False):
            with patch.object(ollama_heal, "is_systemd_available", return_value=False):
                result = ollama_heal.heal()
                assert result == 1

    def test_heal_healthy_returns_0(self):
        """heal() returns 0 when Ollama is already healthy."""
        with patch.object(ollama_heal, "is_healthy", return_value=True):
            result = ollama_heal.heal()
            assert result == 0

    def test_restart_ollama_timeout_returns_false(self):
        """restart_ollama() returns False when subprocess.run times out."""
        with patch.object(
            ollama_heal.subprocess,
            "run",
            side_effect=ollama_heal.subprocess.TimeoutExpired(cmd="systemctl", timeout=30),
        ):
            result = ollama_heal.restart_ollama()
            assert result is False
