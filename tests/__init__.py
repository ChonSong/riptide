"""
Basic Riptide test suite.
"""

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
