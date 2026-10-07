"""Tests for the GET / landing page (riptide.codeovertcp.com)."""

from fastapi.testclient import TestClient


class TestLandingPage:
    """GET / serves the self-contained landing page; must never shadow
    the webhook POST route or the /health contract."""

    def test_root_serves_html(self):
        from riptide.webhook import app
        client = TestClient(app)
        r = client.get("/")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        body = r.text
        # structural contract (single-file-html-apps skill): tag order
        assert body.index("<!DOCTYPE html>") < body.index("</html>")
        assert body.rstrip().endswith("</html>")
        # it is the landing page, not an error page
        assert "Riptide" in body
        assert "riptide.codeovertcp.com" in body or "Riptide Review Required" in body

    def test_root_does_not_shadow_webhook_post(self):
        """GET / must not swallow the webhook: POST /webhook/github must still
        route into the signature-check path. The handler returns 200 on bad
        signatures by design (graceful failure — logged, cron poller covers);
        what matters is the delivery is consumed (idempotency reserve), never
        processed."""
        from riptide.webhook import app
        client = TestClient(app)
        r = client.post(
            "/webhook/github",
            json={"zen": "test"},
            headers={"x-github-event": "pull_request", "x-github-delivery": "test-landing-1"},
        )
        assert r.status_code == 200  # graceful-reject contract, not 404

    def test_health_contract_unchanged(self):
        from riptide.webhook import app
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_homepage_module_is_dependency_free(self):
        """HOME_PAGE must reference no external asset — the page is served
        from a webhook server that must not gain CDN dependencies."""
        from riptide.homepage import HOME_PAGE as H
        for forbidden in ("http://cdn", "https://cdn", "googleapis", "unpkg",
                          "jsdelivr", "integrity=", "<script src="):
            assert forbidden not in H, f"external dependency found: {forbidden}"
