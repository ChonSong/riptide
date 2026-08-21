"""Tests for ollama-heal systemd service file."""
import os


def test_ollama_heal_service_exists():
    """The ollama-heal.service file must exist."""
    assert os.path.isfile("ollama-heal.service"), "ollama-heal.service not found"


def test_ollama_heal_service_has_unit_section():
    """The service file must have a [Unit] section."""
    with open("ollama-heal.service") as f:
        content = f.read()
    assert "[Unit]" in content, "Missing [Unit] section"
    assert "[Service]" in content, "Missing [Service] section"
    assert "[Install]" in content, "Missing [Install] section"


def test_ollama_heal_service_auto_restart():
    """The service must auto-restart on failure."""
    with open("ollama-heal.service") as f:
        content = f.read()
    assert "Restart=on-failure" in content, "Must auto-restart on failure"
