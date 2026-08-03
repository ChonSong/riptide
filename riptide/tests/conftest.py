# riptide/tests/conftest.py
"""
Shared test infrastructure for Riptide.
Mocks GitHub API, Ollama, Hermes cron, and external CLIs.
"""

import os
import json
import hmac
import hashlib
import pytest
import tempfile
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# ── Webhook Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def webhook_secret():
    """Test webhook secret."""
    return "test-secret-123"


@pytest.fixture
def webhook_body():
    """Sample webhook body."""
    return b'{"action":"opened","pull_request":{"number":1,"title":"test"}}'


@pytest.fixture
def valid_signature(webhook_body, webhook_secret):
    """Generate a valid X-Hub-Signature-256 header value."""
    sig = hmac.new(
        webhook_secret.encode(), webhook_body, hashlib.sha256
    ).hexdigest()
    return f"sha256={sig}"


@pytest.fixture
def invalid_signature():
    """Invalid signature for negative tests."""
    return "sha256=deadbeef"


@pytest.fixture
def webhook_delivery_id():
    """Unique webhook delivery ID."""
    return "test-delivery-abc123"


# ── Client Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def client(webhook_secret, tmp_path):
    """TestClient with WEBHOOK_SECRET set and isolated state store."""
    os.environ["GITHUB_WEBHOOK_SECRET"] = webhook_secret
    os.environ["RIPTIDE_DATA_DIR"] = str(tmp_path)
    os.environ["RIPTIDE_STATE_DB"] = str(tmp_path / "riptide_state.db")
    # Clear module cache to get fresh app with new secret
    import importlib
    import riptide.webhook
    importlib.reload(riptide.webhook)
    from riptide.webhook import app
    return TestClient(app)


# ── GitHub App Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def mock_github_app():
    """Mock GitHubAppClient for all API interactions."""
    with patch("riptide.webhook.GitHubAppClient") as mock:
        instance = MagicMock()
        instance.post_pr_comment.return_value = True
        instance.get_pr_files.return_value = [
            {"filename": "foo.py", "patch": "x"},
        ]
        mock.return_value = instance
        yield instance


# ── Ollama Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_ollama():
    """Mock Ollama /api/generate with valid response."""
    with patch("requests.post") as mock:
        mock.return_value.status_code = 200
        mock.return_value.json.return_value = {
            "response": "This PR adds foo functionality."
        }
        yield mock


@pytest.fixture
def mock_ollama_failure():
    """Mock Ollama returning HTTP error."""
    with patch("requests.post") as mock:
        mock.return_value.status_code = 500
        mock.return_value.text = "Internal Server Error"
        yield mock


@pytest.fixture
def mock_ollama_malformed():
    """Mock Ollama returning malformed JSON."""
    with patch("requests.post") as mock:
        mock.return_value.status_code = 200
        mock.return_value.json.side_effect = ValueError("bad json")
        yield mock


@pytest.fixture
def mock_ollama_refusal():
    """Mock Ollama returning a refusal/invalid response."""
    with patch("requests.post") as mock:
        mock.return_value.status_code = 200
        mock.return_value.json.return_value = {
            "response": "I cannot summarize this PR."
        }
        yield mock


@pytest.fixture
def mock_ollama_truncated():
    """Mock Ollama returning truncated output."""
    with patch("requests.post") as mock:
        mock.return_value.status_code = 200
        mock.return_value.json.return_value = {
            "response": "This PR adds a"  # truncated
        }
        yield mock


# ── Hermes Cron Fixtures ────────────────────────────────────────────────────

@pytest.fixture
def mock_hermes_cron():
    """Mock hermes cron create calls and pre_generate_diagram."""
    with patch("subprocess.run") as mock, \
         patch("riptide.grafiphy.orchestrator.pre_generate_diagram", return_value=None):
        mock.return_value.returncode = 0
        mock.return_value.stdout = "cron-id-123"
        mock.return_value.stderr = ""
        yield mock


@pytest.fixture
def mock_hermes_cron_failure():
    """Mock hermes cron create failure."""
    with patch("subprocess.run") as mock:
        mock.return_value.returncode = 1
        mock.return_value.stdout = ""
        mock.return_value.stderr = "hermes not found"
        yield mock


# ── PR Payload Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def sample_pr_payload():
    """Complete GitHub PR opened payload."""
    return {
        "action": "opened",
        "number": 42,
        "pull_request": {
            "number": 42,
            "title": "feat: add bar",
            "body": "This adds bar",
            "head": {"sha": "abc123", "ref": "feat/bar"},
            "base": {"sha": "def456", "ref": "main"},
            "user": {"login": "test-author"},
            "html_url": "https://github.com/test/repo/pull/42",
            "created_at": "2026-07-31T00:00:00Z",
            "updated_at": "2026-07-31T00:00:00Z",
        },
        "repository": {
            "full_name": "test/repo",
            "owner": {"login": "test"},
        },
        "installation": {"id": 12345},
    }


@pytest.fixture
def sample_pr_files():
    """Sample PR files list."""
    return [
        {"filename": "src/components/Button.tsx", "patch": "..."},
        {"filename": "src/styles/main.css", "patch": "..."},
        {"filename": "src/utils/helper.py", "patch": "..."},
    ]


@pytest.fixture
def sample_pr_files_large():
    """Large PR with many files."""
    return [
        {"filename": f"src/module{i}/file.py", "patch": "..."}
        for i in range(10)
    ]


# ── Classification Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def pr_title_feature():
    return "feat: add new button component"


@pytest.fixture
def pr_title_fix():
    return "fix: resolve crash on startup"


@pytest.fixture
def pr_title_refactor():
    return "refactor: extract helper function"


@pytest.fixture
def pr_title_docs():
    return "docs: update README"


# ── Environment Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def mock_env():
    """Mock environment variables."""
    env_vars = {
        "GITHUB_APP_ID": "4262983",
        "WEBHOOK_SECRET": "test-secret",
        "RIPTIDE_DATA_DIR": "/tmp/riptide_test",
        "RIPTIDE_WATCHED_REPOS": "test/repo",
        "RIPTIDE_OUR_USERNAME": "ChonSong",
        "RIPTIDE_OUR_ORG": "ChonSong",
        "RIPTIDE_MIN_LOC_CHANGED": "100",
        "RIPTIDE_STALENESS_MINUTES": "30",
    }
    with patch.dict(os.environ, env_vars, clear=False):
        yield
