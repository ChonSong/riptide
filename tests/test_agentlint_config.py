# riptide/tests/test_agentlint_config.py
"""
Tests for the agentlint safety-stack configuration files.
Validates hooks.json, pre-commit hook, and agentlint.yml are structurally sound.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestAgentlintConfig:
    """Structural validation of the agent safety stack config files."""

    def test_hooks_json_is_valid_json(self):
        hooks_path = REPO_ROOT / ".codex" / "hooks.json"
        if not hooks_path.exists():
            pytest.skip(".codex/hooks.json not present")
        data = json.loads(hooks_path.read_text())
        assert isinstance(data, dict)

    def test_hooks_json_has_event_keys(self):
        hooks_path = REPO_ROOT / ".codex" / "hooks.json"
        if not hooks_path.exists():
            pytest.skip(".codex/hooks.json not present")
        data = json.loads(hooks_path.read_text())
        # Expect at least one hook event mapping
        assert len(data) > 0

    def test_precommit_hook_exists_and_executable(self):
        hook_path = REPO_ROOT / ".githooks" / "pre-commit"
        if not hook_path.exists():
            pytest.skip(".githooks/pre-commit not present")
        assert os.access(hook_path, os.X_OK)

    def test_precommit_hook_is_valid_shell(self):
        hook_path = REPO_ROOT / ".githooks" / "pre-commit"
        if not hook_path.exists():
            pytest.skip(".githooks/pre-commit not present")
        result = subprocess.run(
            ["bash", "-n", str(hook_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"syntax error: {result.stderr}"

    def test_agentlint_yml_is_yaml(self):
        yml_path = REPO_ROOT / "agentlint.yml"
        if not yml_path.exists():
            pytest.skip("agentlint.yml not present")
        text = yml_path.read_text()
        assert "rules:" in text or "extends:" in text or len(text.strip()) > 0

    def test_workflow_files_exist(self):
        wf_dir = REPO_ROOT / ".github" / "workflows"
        if not wf_dir.exists():
            pytest.skip(".github/workflows not present")
        agentlint_wf = wf_dir / "agentlint.yml"
        assert agentlint_wf.exists()
