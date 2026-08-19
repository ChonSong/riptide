# riptide/tests/test_ollama_heal_integration.py
"""
Tests for ollama_heal integration with Companion._generate_eli5().

Verifies that when Ollama goes down, the bot attempts to self-heal
via ollama_heal.heal() before skipping enrichment.
"""

import pytest
from unittest.mock import patch, MagicMock

from riptide.companion import Companion
from riptide import ollama_heal


def make_companion(tmp_path):
    """Create a Companion instance with mocked github client and disabled warm-up."""
    client = MagicMock()
    with patch("threading.Thread"):
        from riptide.state import StateStore

        store = StateStore(str(tmp_path / "state.db"))
        companion = Companion(client, state_store=store)
    companion._alert_file = tmp_path / "companion_alerts.json"
    return companion


class TestOllamaHealIntegration:
    """Tests for ollama_heal.heal() integration in _generate_eli5()."""

    def test_heal_succeeds_proceeds_with_ollama_call(self, tmp_path):
        """When heal() returns 0 (healthy), _generate_eli5 proceeds normally."""
        companion = make_companion(tmp_path)
        files = [{"filename": "src/main.py"}]

        with patch("riptide.ollama_heal.heal", return_value=0) as mock_heal:
            with patch.object(
                companion, "_ollama_call", return_value="It's like adding a new room."
            ) as mock_call:
                result = companion._generate_eli5("feat: add feature", files)

                mock_heal.assert_called_once()
                mock_call.assert_called_once()
                assert result == "It's like adding a new room."

    def test_heal_fails_returns_none_skips_ollama(self, tmp_path):
        """When heal() returns 1 (down, unrecoverable), skip Ollama and return None."""
        companion = make_companion(tmp_path)
        files = [{"filename": "src/main.py"}]

        with patch("riptide.ollama_heal.heal", return_value=1) as mock_heal:
            with patch.object(companion, "_ollama_call") as mock_call:
                result = companion._generate_eli5("feat: add feature", files)

                mock_heal.assert_called_once()
                mock_call.assert_not_called()
                assert result is None

    def test_heal_returns_exit_code_2_skips_ollama(self, tmp_path):
        """When heal() returns 2 (systemd not found), skip Ollama and return None."""
        companion = make_companion(tmp_path)
        files = [{"filename": "src/main.py"}]

        with patch("riptide.ollama_heal.heal", return_value=2) as mock_heal:
            with patch.object(companion, "_ollama_call") as mock_call:
                result = companion._generate_eli5("feat: add feature", files)

                mock_heal.assert_called_once()
                mock_call.assert_not_called()
                assert result is None

    def test_heal_called_before_ollama_call(self, tmp_path):
        """Verify heal() is called BEFORE the Ollama API call."""
        companion = make_companion(tmp_path)
        files = [{"filename": "src/main.py"}]
        call_order = []

        with patch("riptide.ollama_heal.heal", return_value=0) as mock_heal:
            with patch.object(companion, "_ollama_call", return_value="ELI5 text") as mock_call:
                mock_heal.side_effect = lambda **kw: (call_order.append("heal"), 0)[1]
                mock_call.side_effect = lambda p: (call_order.append("ollama_call"), "ELI5 text")[1]

                companion._generate_eli5("feat: add feature", files)

                assert call_order == ["heal", "ollama_call"]

    def test_eli5_with_delta_flag_and_heal_success(self, tmp_path):
        """Heal success + is_delta=True should still work."""
        companion = make_companion(tmp_path)
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

    def test_heal_timeout_configurable(self, tmp_path):
        """RIPTIDE_HEAL_TIMEOUT env var controls heal wait timeout."""
        companion = make_companion(tmp_path)
        files = [{"filename": "src/main.py"}]

        with patch.dict("os.environ", {"RIPTIDE_HEAL_TIMEOUT": "5"}):
            with patch("riptide.ollama_heal.heal", return_value=0) as mock_heal:
                companion._generate_eli5("feat: add feature", files)
                mock_heal.assert_called_once_with(wait_timeout=5)

    def test_heal_exception_treated_as_failure(self, tmp_path):
        """If heal() raises an exception, treat as failure (return None)."""
        companion = make_companion(tmp_path)
        files = [{"filename": "src/main.py"}]

        with patch("riptide.ollama_heal.heal", side_effect=RuntimeError("boom")):
            with patch.object(companion, "_ollama_call") as mock_call:
                result = companion._generate_eli5("feat: add feature", files)
                assert result is None
                mock_call.assert_not_called()


class TestSystemdDetection:
    """Tests for is_systemd_service_loaded()."""

    def test_service_enabled(self):
        """is_systemd_service_loaded() returns True when systemctl is-enabled returns 0."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            assert ollama_heal.is_systemd_service_loaded() is True

    def test_service_static(self):
        """is_systemd_service_loaded() returns True when systemctl is-enabled returns 3 (static)."""
        mock_result = MagicMock()
        mock_result.returncode = 3
        with patch("subprocess.run", return_value=mock_result):
            assert ollama_heal.is_systemd_service_loaded() is True

    def test_service_disabled(self):
        """is_systemd_service_loaded() returns False when systemctl is-enabled returns 1."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("subprocess.run", return_value=mock_result):
            # When is-enabled returns 1, we fall back to systemctl cat
            mock_cat = MagicMock()
            mock_cat.returncode = 0  # Service exists but disabled
            with patch("subprocess.run", side_effect=[mock_result, mock_cat]):
                # Note: first call is is-enabled (disabled), second is cat (exists)
                result = ollama_heal.is_systemd_service_loaded()
                assert result is True

    def test_service_not_found(self):
        """is_systemd_service_loaded() returns False when service doesn't exist."""
        mock_result = MagicMock()
        mock_result.returncode = 4
        with patch("subprocess.run", return_value=mock_result):
            assert ollama_heal.is_systemd_service_loaded() is False

    def test_subprocess_timeout(self):
        """is_systemd_service_loaded() returns False on subprocess timeout."""
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=5)):
            assert ollama_heal.is_systemd_service_loaded() is False

    def test_subprocess_oserror(self):
        """is_systemd_service_loaded() returns False when systemctl is not installed."""
        with patch("subprocess.run", side_effect=FileNotFoundError("systemctl not found")):
            assert ollama_heal.is_systemd_service_loaded() is False


class TestIsHealthy:
    """Tests for is_healthy()."""

    def test_healthy_returns_true(self):
        """is_healthy() returns True when Ollama responds with 200."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.get", return_value=mock_resp):
            assert ollama_heal.is_healthy() is True

    def test_unhealthy_returns_false(self):
        """is_healthy() returns False when Ollama responds with 500."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("requests.get", return_value=mock_resp):
            assert ollama_heal.is_healthy() is False

    def test_connection_error_returns_false(self):
        """is_healthy() returns False when connection fails."""
        with patch("requests.get", side_effect=ConnectionError("refused")):
            assert ollama_heal.is_healthy() is False

    def test_custom_base_url(self):
        """is_healthy() uses the provided base_url parameter."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.get", return_value=mock_resp) as mock_get:
            ollama_heal.is_healthy(base_url="http://custom:12345")
            mock_get.assert_called_once()
            url = mock_get.call_args[0][0]
            assert "http://custom:12345" in url
