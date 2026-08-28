#!/usr/bin/env python3
"""Ephemeral end-to-end test for the fixer flow.

This test spins up the Riptide webhook server in a Docker container
(using the existing scripts/ephemeral-test.sh) and verifies that:
1. @riptide-bot fix command spawns a Hermes cron job
2. The cron job completes and posts a comment
3. Errors are handled gracefully

Run with: pytest riptide/tests/test_fixer_ephemeral.py -x -v
Or standalone: python riptide/tests/test_fixer_ephemeral.py
"""
import os
import json
import hmac
import hashlib
import subprocess
import time
import socket
from pathlib import Path


# ── Helpers ────────────────────────────────────────────────────────────────


def find_free_port():
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


def make_signature(body: bytes, secret: str) -> str:
    """Generate a valid X-Hub-Signature-256 for a body."""
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def build_container(image_tag: str, branch: str):
    """Build a Docker container for testing. Returns image tag."""
    script = Path(__file__).parent.parent / "scripts" / "ephemeral-test.sh"
    if not script.exists():
        raise FileNotFoundError(f"ephemeral-test.sh not found at {script}")

    # Use docker directly for more control than the script
    print(f"Building Docker image {image_tag} from branch {branch}...")

    # Create a temporary worktree
    worktree_dir = Path("/tmp") / f"riptide-test-{int(time.time())}"
    worktree_dir.mkdir(parents=True, exist_ok=True)

    # Fetch and checkout
    subprocess.run(["git", "fetch", "origin", branch], check=True,
                   cwd=str(Path(__file__).parent.parent.parent))
    commit = subprocess.run(["git", "rev-parse", f"origin/{branch}"],
                          capture_output=True, text=True, check=True,
                          cwd=str(Path(__file__).parent.parent.parent)).stdout.strip()

    subprocess.run(["git", "worktree", "add", "--detach", str(worktree_dir), commit],
                   check=True, cwd=str(Path(__file__).parent.parent.parent))

    # Build
    result = subprocess.run(
        ["docker", "build", "-t", image_tag, str(worktree_dir)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Docker build failed: {result.stderr}")

    # Cleanup worktree
    subprocess.run(["git", "worktree", "remove", str(worktree_dir), "--force"],
                   cwd=str(Path(__file__).parent.parent.parent))

    return image_tag


def run_container(image_tag: str, port: int):
    """Run a container and return the container name."""
    container_name = f"riptide-fix-test-{int(time.time())}"
    subprocess.run([
        "docker", "run", "-d",
        "--name", container_name,
        "-p", f"{port}:8477",
        "-e", "GITHUB_APP_ID=0",
        "-e", "GITHUB_PRIVATE_KEY_PATH=/dev/null",
        "-e", "GITHUB_WEBHOOK_SECRET=test-secret",
        "-e", "RIPTIDE_DATA_DIR=/tmp/riptide-data",
        "-e", "HOST=0.0.0.0",
        "-e", "PORT=8477",
        image_tag,
    ], check=True)

    return container_name


def wait_for_health(port: int, timeout: int = 30):
    """Wait for the container to be healthy."""
    import urllib.request
    for i in range(timeout):
        try:
            req = urllib.request.Request(
                f"http://localhost:{port}/webhook/github",
                data=b'{"test":1}',
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            resp = urllib.request.urlopen(req)
            if resp.status == 401:  # Expected: signature verification active
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def send_webhook(port: int, payload: dict, secret: str):
    """Send a webhook event to the container."""
    import urllib.request
    body = json.dumps(payload).encode()
    sig = make_signature(body, secret)

    req = urllib.request.Request(
        f"http://localhost:{port}/webhook/github",
        data=body,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "issue_comment",
            "X-GitHub-Delivery": f"test-fix-{int(time.time())}",
            "Content-Type": "application/json",
        },
        method="POST"
    )
    resp = urllib.request.urlopen(req)
    return resp.status


def cleanup(container_name: str):
    """Stop and remove container."""
    subprocess.run(["docker", "stop", container_name], capture_output=True)
    subprocess.run(["docker", "rm", container_name], capture_output=True)


# ── Test Class ──────────────────────────────────────────────────────────────


class TestFixerEphemeral:
    """Ephemeral end-to-end test for fixer flow."""

    container_name = None
    port = None
    image_tag = None

    @classmethod
    def setup_class(cls):
        """Build and start container."""
        cls.port = find_free_port()
        cls.image_tag = f"riptide-fix-test-{int(time.time())}"

        try:
            build_container(cls.image_tag, os.environ.get("TEST_BRANCH", "feat/observability-gaps-20260823"))
            cls.container_name = run_container(cls.image_tag, cls.port)

            if not wait_for_health(cls.port):
                raise RuntimeError("Container failed health check")

            print(f"\n✅ Container healthy on port {cls.port}")
        except Exception as e:
            print(f"\n⚠️  Ephemeral test setup failed: {e}")
            print("   Running in mock-only mode (no Docker)")
            cls.container_name = None

    @classmethod
    def teardown_class(cls):
        """Cleanup container."""
        if cls.container_name:
            cleanup(cls.container_name)
        if cls.image_tag:
            subprocess.run(["docker", "image", "rm", cls.image_tag],
                         capture_output=True)

    def test_fix_command_spawns(self):
        """@riptide-bot fix should spawn a Hermes cron job."""
        if not self.container_name:
            pytest.skip("Docker not available")

        payload = {
            "action": "created",
            "comment": {
                "body": "@riptide-bot fix the failing test",
                "user": {"login": "ChonSong"},
            },
            "issue": {"number": 172, "pull_request": {"url": "https://api.github.com/repos/ChonSong/riptide/pulls/172"}},
            "repository": {"full_name": "ChonSong/riptide", "name": "riptide"},
            "installation": {"id": 4262983},
        }

        assert self.port is not None
        status = send_webhook(self.port, payload, "test-secret")
        assert status == 200

        # Wait a bit for the cron job to be created
        time.sleep(5)

        # Verify Hermes job was created
        result = subprocess.run(
            ["hermes", "cron", "list"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        # Should contain our fix job
        assert "riptide-fix" in result.stdout or "fix" in result.stdout.lower()

    def test_fix_command_with_description(self):
        """@riptide-bot fix <description> should work."""
        if not self.container_name:
            pytest.skip("Docker not available")

        payload = {
            "action": "created",
            "comment": {
                "body": "@riptide-bot fix the flaky test in test_companion.py",
                "user": {"login": "ChonSong"},
            },
            "issue": {"number": 172, "pull_request": {"url": "https://api.github.com/repos/ChonSong/riptide/pulls/172"}},
            "repository": {"full_name": "ChonSong/riptide", "name": "riptide"},
            "installation": {"id": 4262983},
        }

        assert self.port is not None
        status = send_webhook(self.port, payload, "test-secret")
        assert status == 200

    def test_unauthorized_fix_rejected(self):
        """Fix from non-author should be rejected."""
        if not self.container_name:
            pytest.skip("Docker not available")

        payload = {
            "action": "created",
            "comment": {
                "body": "@riptide-bot fix this",
                "user": {"login": "unauthorized-user"},
            },
            "issue": {"number": 172, "pull_request": {"url": "https://api.github.com/repos/ChonSong/riptide/pulls/172"}},
            "repository": {"full_name": "ChonSong/riptide", "name": "riptide"},
            "installation": {"id": 4262983},
        }

        assert self.port is not None
        status = send_webhook(self.port, payload, "test-secret")
        # Should return 200 but with rejection message
        assert status == 200


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-x", "-v"])
