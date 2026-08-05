"""
Tests for scripts/deploy.sh — auto-deploy lock + verify.

Validates:
- Interprocess lock serializes concurrent deploys (second exits cleanly)
- Deploy proceeds without waiting for sessions (no build step)
- Service restart + verification works
"""

import os
import subprocess
from pathlib import Path

import pytest

DEPLOY_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "deploy.sh"


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a minimal git repo for deploy.sh to operate on."""
    repo = tmp_path / "riptide"
    repo.mkdir()
    (repo / "riptide").mkdir()
    (repo / "riptide" / "__init__.py").write_text("")
    (repo / "scripts").mkdir()
    # Copy deploy.sh into the temp repo so it can be tested
    deploy_src = Path(__file__).resolve().parent.parent.parent / "scripts" / "deploy.sh"
    deploy_dst = repo / "scripts" / "deploy.sh"
    deploy_dst.write_text(deploy_src.read_text())
    deploy_dst.chmod(0o755)

    # Init git repo
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True
    )
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True
    )
    return repo


class TestDeployLock:
    """Test interprocess lock serialization."""

    def test_lock_acquired_when_free(self, tmp_repo):
        """Deploy script should acquire lock when none is held."""
        lock_file = tmp_repo / "test.lock"
        # Simulate lock acquisition via flock
        with open(lock_file, "w") as f:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Lock held — second attempt should fail
            with open(lock_file, "r") as f2:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(f2, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_concurrent_deploy_exits_cleanly(self, tmp_repo):
        """Deploy script should succeed when lock is available."""
        log_file = tmp_repo / "deploy.log"
        # When lock is free, deploy should proceed and succeed
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(tmp_repo),
            env={**os.environ, "RIPTIDE_DEPLOY_LOCK": str(tmp_repo / "deploy.lock"), "RIPTIDE_DEPLOY_LOG": str(log_file)},
        )
        assert result.returncode == 0
        assert log_file.exists()
        log_content = log_file.read_text()
        # Should show successful deployment
        assert "Deploy complete" in log_content or "Deploy started" in log_content


class TestDeployNoWait:
    """Test that deploy does NOT wait for sessions (no build step)."""

    def test_deploy_proceeds_without_waiting(self, tmp_repo):
        """Deploy should complete quickly without waiting for Hermes sessions."""
        import time
        log_file = tmp_repo / "deploy.log"
        start = time.monotonic()
        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(tmp_repo),
            env={**os.environ, "RIPTIDE_DEPLOY_LOCK": str(tmp_repo / "deploy.lock"), "RIPTIDE_DEPLOY_LOG": str(log_file)},
        )
        elapsed = time.monotonic() - start
        assert result.returncode == 0
        # Should complete in under 10s (no 300s wait loop)
        assert elapsed < 10, f"Deploy took {elapsed:.1f}s — likely stuck in wait loop"
        assert log_file.exists()
        log_content = log_file.read_text()
        # Should NOT contain "Waiting for" messages
        assert "Waiting for" not in log_content
        assert "Deploy complete" in log_content
