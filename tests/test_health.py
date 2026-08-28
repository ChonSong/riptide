"""
Riptide basic health endpoint test.
"""

from fastapi.testclient import TestClient
from riptide.webhook import app

client = TestClient(app)


def test_health():
    """GET /health returns status: ok."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
