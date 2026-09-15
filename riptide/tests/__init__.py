"""Basic Riptide test suite.

This package __init__ is executed by pytest before riptide/tests/conftest.py, and
its ``from riptide.webhook import app`` below is the first riptide import of the
process.  Hermetic state isolation therefore has to be applied here, before that
import, or the module-level state paths bound by webhook/state/review_memory/
orchestrator/labeler would point at the developer's real HOME.  See
riptide/tests/_hermetic_state.py for the full list of isolated paths.
"""

from riptide.tests import _hermetic_state

_hermetic_state.apply()

from fastapi.testclient import TestClient
from riptide.webhook import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_health_response_structure():
    """Health endpoint returns expected fields."""
    resp = client.get("/health")
    data = resp.json()
    assert "status" in data


def test_unknown_route_returns_404():
    resp = client.get("/nonexistent")
    assert resp.status_code == 404
