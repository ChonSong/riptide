# riptide/tests/conftest.py
"""
Shared test infrastructure for Riptide.
Mocks GitHub API, Ollama, Hermes cron, and external CLIs.

Test runs are hermetic with respect to every ambient state path the package
uses: the autouse ``hermetic_state`` fixture below (with ``hermetic_state_root``
for the session root) keeps the suite from reading or writing the developer's
real state, so the set of failing node IDs no longer depends on what happens to
be in ``~/.hermes/state/riptide-work-state.json`` on this machine.
"""

import os
import json
import hmac
import hashlib
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from riptide.tests import _hermetic_state

# ── Hermetic state isolation ────────────────────────────────────────────────
#
# The isolation itself lives in riptide/tests/_hermetic_state.py, which carries
# the full inventory of ambient paths and why each one is reached.  Two facts
# matter here:
#
# 1. It is applied at *import time*, not in a fixture.  Nearly every state path
#    in the package is resolved from the ambient environment when the module is
#    imported — as a module-level constant (work_state.WORK_STATE_PATH,
#    state.DEFAULT_DB_PATH, state.POLLER_DB_PATH, deepthink.CRON_JOBS_PATH,
#    webhook.DATA_DIR), as a class default (StateStore.__init__'s bound
#    ``db_path=DEFAULT_DB_PATH``) or as a default argument.  A fixture runs after
#    collection, i.e. after every test module has already imported riptide.*, so
#    it could not fix those bindings.
#
# 2. It must therefore be applied before the first riptide import of the
#    process.  That import does not happen in this file: pytest executes the
#    package ``riptide/tests/__init__.py`` before this conftest, and that module
#    does ``from riptide.webhook import app``, pulling in riptide.webhook →
#    riptide.state → riptide.review_memory → riptide.orchestrator →
#    riptide.labeler.  Hence ``_hermetic_state.apply()`` is called from
#    ``riptide/tests/__init__.py`` first, and re-applied here (idempotent) as a
#    belt-and-braces measure for anything that imports riptide even earlier.
#
# ``_hermetic_state.verify()`` below asserts that every already-imported module
# constant really does point inside the session temp root, so a future change to
# the import order fails loudly instead of silently leaking developer state
# back into the suite.
#
# One class of leak cannot be prevented from here: ``test_deepthink_config.py``
# (lines 16-35) and ``test_fixer.py`` (TestFixerDefaults, lines 62-94) reconfigure
# their module by reloading it inside ``patch.dict(os.environ, {}, clear=True)``,
# which re-binds its module-level paths from a *cleared* environment — i.e. back
# to the developer's real defaults.  The autouse fixture therefore calls
# ``_hermetic_state.heal()`` before each test to repoint any constant that
# drifted outside the temp root; see that function for the details.
#
# Not redirected (documented in _hermetic_state.py too):
#   * /tmp/custom_riptide_test.db — hardcoded in test_state_store_path.py:42; a
#     fixed, machine-independent path, so it cannot make the failure set depend
#     on ambient state.  Left untouched on purpose.
#   * /tmp/riptide-review-<run>-<suffix> (conductor.py:57, scribe.py:142) — no
#     env override exists for them, so they are not reachable from the test side
#     without editing production code.
#   * ``tests/`` at the repo root is outside ``pyproject.toml``'s ``testpaths``
#     and is not collected by this suite.

_hermetic_state.apply()
_hermetic_state.verify()

SESSION_TMP = _hermetic_state.SESSION_TMP
_ISOLATED_HOME = _hermetic_state.ISOLATED_HOME


@pytest.fixture(scope="session", autouse=True)
def hermetic_state_root():
    """Autouse: session-wide throwaway state root, deleted at session end.

    Every state path the suite can touch (work-state JSON, state.db + .lock and
    -wal/-shm sidecars, metadata.db, documentarian.db, the Hermes cron jobs.json,
    DATA_DIR, the workspace roots) resolves inside this directory instead of the
    developer's real HOME.  See riptide/tests/_hermetic_state.py.
    """
    _hermetic_state.apply()
    yield SESSION_TMP
    _hermetic_state.cleanup()


@pytest.fixture(autouse=True)
def hermetic_state(hermetic_state_root):
    """Autouse: re-assert state isolation around every test.

    The binding happens at import time (see the header note); this guard
    re-applies the environment per test, repairs any module constant that a
    previous test's ``importlib.reload(...)`` under ``clear=True`` re-bound to a
    developer default (see ``_hermetic_state.heal``), and then verifies that
    every already-imported state path resolves inside the session temp root.
    """
    _hermetic_state.apply()
    _hermetic_state.heal()
    _hermetic_state.verify()
    assert os.environ["HOME"] == str(_ISOLATED_HOME), (
        "test run is not hermetic: HOME is not the session temp root"
    )
    yield hermetic_state_root


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
    """Mock hermes cron create calls."""
    with patch("subprocess.run") as mock:
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
