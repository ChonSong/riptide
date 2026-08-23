#!/usr/bin/env python3
"""Tests for ollama_heal integration.

Covers:
- is_systemd_service_loaded() distinct outcomes (absent/disabled/probe_failed/healthy)
- wait_for_recovery() monotonic clock (no negative sleep)
- heal() routing logic
"""

import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from riptide import ollama_heal


# ── ollama_heal.py unit tests ──────────────────────────────────────────────

class TestIsSystemdServiceLoaded:
    """Tests for is_systemd_service_loaded() distinct outcomes."""

    def test_returns_absent_when_unit_not_found(self):
        """FileNotFoundError from systemctl should return ABSENT, not PROBE_FAILED."""
        with patch.object(ollama_heal, "is_systemd_available", return_value=True), \
             patch.object(ollama_heal.subprocess, "run", side_effect=FileNotFoundError):
            result = ollama_heal.is_systemd_service_loaded()
            assert result == "absent"

    def test_returns_probe_failed_when_systemctl_times_out(self):
        """TimeoutExpired from systemctl should return PROBE_FAILED."""
        with patch.object(ollama_heal, "is_systemd_available", return_value=True), \
             patch.object(ollama_heal.subprocess, "run",
                         side_effect=ollama_heal.subprocess.TimeoutExpired(cmd="systemctl", timeout=5)):
            result = ollama_heal.is_systemd_service_loaded()
            assert result == "probe_failed"

    def test_returns_disabled_when_rc_1(self):
        """returncode=1 with unit existing = DISABLED."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_check = MagicMock()
        mock_check.returncode = 0
        mock_check.stdout = "[Unit]\n"  # unit exists
        with patch.object(ollama_heal, "is_systemd_available", return_value=True), \
             patch.object(ollama_heal.subprocess, "run", side_effect=[mock_result, mock_check]):
            result = ollama_heal.is_systemd_service_loaded()
            assert result == "disabled"

    def test_returns_healthy_when_rc_0(self):
        """returncode=0 = enabled = HEALTHY."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch.object(ollama_heal, "is_systemd_available", return_value=True), \
             patch.object(ollama_heal.subprocess, "run", return_value=mock_result):
            result = ollama_heal.is_systemd_service_loaded()
            assert result == "healthy"

    def test_returns_healthy_when_rc_3(self):
        """returncode=3 = static = HEALTHY."""
        mock_result = MagicMock()
        mock_result.returncode = 3
        with patch.object(ollama_heal, "is_systemd_available", return_value=True), \
             patch.object(ollama_heal.subprocess, "run", return_value=mock_result):
            result = ollama_heal.is_systemd_service_loaded()
            assert result == "healthy"

    def test_returns_probe_failed_when_systemd_unavailable(self):
        """is_systemd_available()=False -> PROBE_FAILED."""
        with patch.object(ollama_heal, "is_systemd_available", return_value=False):
            result = ollama_heal.is_systemd_service_loaded()
            assert result == "probe_failed"


class TestWaitForRecovery:
    """Tests for monotonic clock in wait_for_recovery()."""

    def test_exhausts_timeout_without_negative_sleep(self):
        """wait_for_recovery must exit cleanly without passing negative to sleep."""
        with patch.object(ollama_heal, "is_healthy", return_value=False), \
             patch.object(ollama_heal.time, "sleep") as mock_sleep, \
             patch.object(ollama_heal.time, "monotonic", side_effect=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]):
            result = ollama_heal.wait_for_recovery(timeout=5)
            assert result is False
            # Verify no negative sleep values
            for call in mock_sleep.call_args_list:
                args, kwargs = call
                assert args[0] >= 0, f"Negative sleep: {args[0]}"

    def test_returns_true_on_recovery(self):
        """Returns True when is_healthy becomes True."""
        with patch.object(ollama_heal, "is_healthy", side_effect=[False, False, True]), \
             patch.object(ollama_heal.time, "sleep"):
            result = ollama_heal.wait_for_recovery(timeout=10)
            assert result is True


class TestHealFunction:
    """Tests for heal() logic."""

    def test_returns_0_when_healthy(self):
        """heal() returns 0 immediately when Ollama is healthy."""
        with patch.object(ollama_heal, "is_healthy", return_value=True):
            assert ollama_heal.heal() == 0

    def test_returns_2_when_systemd_absent(self):
        """heal() returns 2 when systemd service is not found."""
        with patch.object(ollama_heal, "is_healthy", return_value=False), \
             patch.object(ollama_heal, "is_systemd_service_loaded", return_value="absent"):
            assert ollama_heal.heal() == 2

    def test_returns_1_when_restart_fails(self):
        """heal() returns 1 when restart fails."""
        with patch.object(ollama_heal, "is_healthy", return_value=False), \
             patch.object(ollama_heal, "is_systemd_service_loaded", return_value="healthy"), \
             patch.object(ollama_heal, "restart_ollama", return_value=False):
            assert ollama_heal.heal() == 1

    def test_returns_1_when_systemd_unavailable(self):
        """heal() returns 1 when systemd is unavailable (Docker env)."""
        with patch.object(ollama_heal, "is_healthy", return_value=False), \
             patch.object(ollama_heal, "is_systemd_service_loaded", return_value="probe_failed"):
            assert ollama_heal.heal() == 1
