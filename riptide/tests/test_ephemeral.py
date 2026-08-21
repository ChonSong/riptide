"""Tests for ephemeral testing infrastructure."""
import os
import stat


def test_ephemeral_test_script_exists():
    """The ephemeral test script must exist and be executable."""
    script = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "ephemeral-test.sh")
    assert os.path.isfile(script), f"ephemeral-test.sh not found at {script}"
    mode = os.stat(script).st_mode
    assert mode & stat.S_IXUSR, "ephemeral-test.sh must be executable"


def test_dockerignore_exists():
    """The .dockerignore file must exist for ephemeral builds."""
    dockerignore = os.path.join(os.path.dirname(__file__), "..", "..", ".dockerignore")
    assert os.path.isfile(dockerignore), ".dockerignore not found"


def test_dockerignore_excludes_git():
    """.dockerignore must exclude .git to keep builds lean."""
    dockerignore = os.path.join(os.path.dirname(__file__), "..", "..", ".dockerignore")
    with open(dockerignore) as f:
        content = f.read()
    assert ".git" in content, ".dockerignore must exclude .git"
    assert "__pycache__" in content, ".dockerignore must exclude __pycache__"
    assert ".env" in content, ".dockerignore must exclude .env"
