# riptide/tests/test_github_app.py
"""
Tests for Riptide GitHub App client.
Covers JWT generation (iat, exp, iss claims) and API client methods.
"""

import json
import base64
import time
from unittest.mock import patch, MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from riptide.github_app import jwt_token, GitHubAppClient


# ── Helpers ──────────────────────────────────────────────────────────────────


def _generate_test_key(tmp_path):
    """Generate a test RSA private key and return the path."""
    key_path = tmp_path / "test.pem"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return str(key_path)


def _decode_jwt_payload(token):
    """Decode the payload from a JWT token (base64url encoded)."""
    parts = token.split(".")
    assert len(parts) == 3, f"JWT should have 3 parts, got {len(parts)}"
    payload_b64 = parts[1] + "="
    while len(payload_b64) % 4 != 0:
        payload_b64 += "="
    return json.loads(base64.urlsafe_b64decode(payload_b64))


# ── JWT Generation Tests ────────────────────────────────────────────────────


class TestJWTGeneration:
    """Test JWT token generation with required claims."""

    def test_jwt_contains_required_claims(self, tmp_path):
        key_path = _generate_test_key(tmp_path)
        token = jwt_token(12345, key_path, 600)
        payload = _decode_jwt_payload(token)
        assert "iat" in payload
        assert "exp" in payload
        assert "iss" in payload

    def test_jwt_iss_equals_app_id(self, tmp_path):
        key_path = _generate_test_key(tmp_path)
        token = jwt_token(12345, key_path, 600)
        payload = _decode_jwt_payload(token)
        # iss is stored as string per GitHub App JWT spec
        assert payload["iss"] == "12345"

    def test_jwt_expiry_equals_expiry_seconds(self, tmp_path):
        key_path = _generate_test_key(tmp_path)
        token = jwt_token(12345, key_path, 600)
        payload = _decode_jwt_payload(token)
        assert payload["exp"] - payload["iat"] == 600

    def test_jwt_custom_expiry(self, tmp_path):
        key_path = _generate_test_key(tmp_path)
        token = jwt_token(12345, key_path, 300)
        payload = _decode_jwt_payload(token)
        assert payload["exp"] - payload["iat"] == 300

    def test_jwt_iat_is_current_time(self, tmp_path):
        key_path = _generate_test_key(tmp_path)
        before = int(time.time())
        token = jwt_token(12345, key_path, 600)
        after = int(time.time())
        payload = _decode_jwt_payload(token)
        assert before <= payload["iat"] <= after + 1

    def test_jwt_has_three_parts(self, tmp_path):
        key_path = _generate_test_key(tmp_path)
        token = jwt_token(12345, key_path, 600)
        parts = token.split(".")
        assert len(parts) == 3


# ── GitHub App Client Tests ─────────────────────────────────────────────────


class TestGitHubAppClient:
    """Test GitHubAppClient API methods with mocked HTTP requests."""

    @pytest.fixture
    def client(self, tmp_path):
        key_path = _generate_test_key(tmp_path)
        client = GitHubAppClient(app_id=12345, private_key_path=key_path)
        client._token_cache = MagicMock()
        client._token_cache.get_token.return_value = "test-installation-token"
        return client

    def test_post_pr_comment_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": 1, "body": "Test comment"}
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response):
            result = client.post_pr_comment(999, "test", "repo", 1, "Test comment")
            assert result["id"] == 1
            assert result["body"] == "Test comment"

    def test_post_pr_comment_returns_dict(self, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 42, "body": "hello"}
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response):
            result = client.post_pr_comment(999, "test", "repo", 1, "hello")
            assert isinstance(result, dict)
            assert result["id"] == 42

    def test_get_pr_files_success(self, client):
        mock_files = [
            {"filename": "foo.py", "patch": "x"},
            {"filename": "bar.py", "patch": "y"},
        ]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_files
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            result = client.get_pr_files(999, "test", "repo", 1)
            assert len(result) == 2
            assert result[0]["filename"] == "foo.py"
            assert result[1]["filename"] == "bar.py"

    def test_get_pr_files_empty(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            result = client.get_pr_files(999, "test", "repo", 1)
            assert result == []

    def test_get_pr_files_paginates(self, client):
        page1 = [{"filename": f"file{i}.py"} for i in range(100)]
        page2 = [{"filename": "last.py"}]

        responses = []
        for page in [page1, page2]:
            mock = MagicMock()
            mock.status_code = 200
            mock.json.return_value = page
            mock.raise_for_status = MagicMock()
            responses.append(mock)

        with patch("requests.get", side_effect=responses):
            result = client.get_pr_files(999, "test", "repo", 1)
            assert len(result) == 101
            assert result[-1]["filename"] == "last.py"

    def test_post_pr_comment_sends_correct_body(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": 1}
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response) as mock_post:
            client.post_pr_comment(999, "test", "repo", 42, "My review comment")
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"] == {"body": "My review comment"}

    def test_update_pr_comment_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 7, "body": "Updated"}
        mock_response.raise_for_status = MagicMock()

        with patch("requests.patch", return_value=mock_response) as mock_patch:
            result = client.update_pr_comment(999, "test", "repo", 7, "Updated body")
            assert result["id"] == 7
            assert result["body"] == "Updated"
            call_kwargs = mock_patch.call_args[1]
            assert call_kwargs["json"] == {"body": "Updated body"}

    def test_update_pr_comment_uses_correct_url(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 123}
        mock_response.raise_for_status = MagicMock()

        with patch("requests.patch", return_value=mock_response) as mock_patch:
            client.update_pr_comment(999, "owner", "repo", 123, "body")
            called_url = mock_patch.call_args[0][0]
            assert called_url == "https://api.github.com/repos/owner/repo/issues/comments/123"

    def test_update_pr_comment_sends_content_type_header(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 5}
        mock_response.raise_for_status = MagicMock()

        with patch("requests.patch", return_value=mock_response) as mock_patch:
            client.update_pr_comment(999, "test", "repo", 5, "body")
            call_kwargs = mock_patch.call_args[1]
            headers = call_kwargs["headers"]
            assert headers["Content-Type"] == "application/json"
            assert "Bearer test-installation-token" in headers["Authorization"]
