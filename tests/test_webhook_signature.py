# riptide/tests/test_webhook_signature.py
"""
HMAC SHA-256 webhook signature verification.
Critical: invalid signatures MUST drop the request immediately.
"""

import pytest
import hmac
import hashlib
from riptide.github_app import verify_webhook_signature


class TestWebhookSignature:
    """Test HMAC signature verification against X-Hub-Signature-256."""

    def test_valid_signature(self, webhook_secret):
        payload = b'{"test": true}'
        sig = hmac.new(
            webhook_secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        assert verify_webhook_signature(payload, f"sha256={sig}", webhook_secret) is True

    def test_invalid_signature(self, webhook_secret):
        payload = b'{"test": true}'
        assert verify_webhook_signature(payload, "sha256=invalid", webhook_secret) is False

    def test_missing_signature(self, webhook_secret):
        assert verify_webhook_signature(b"data", "", webhook_secret) is False

    def test_none_signature(self, webhook_secret):
        assert verify_webhook_signature(b"data", "", webhook_secret) is False

    def test_missing_secret(self):
        assert verify_webhook_signature(b"data", "sha256=abc", "") is False

    def test_none_secret(self):
        assert verify_webhook_signature(b"data", "sha256=abc", "") is False

    def test_tampered_payload(self, webhook_secret):
        """Signature of original payload must not match tampered payload."""
        payload = b'{"test": true}'
        sig = hmac.new(
            webhook_secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        tampered = b'{"test": false}'
        assert verify_webhook_signature(tampered, f"sha256={sig}", webhook_secret) is False

    def test_signature_without_prefix(self, webhook_secret):
        """Signature without sha256= prefix should fail."""
        payload = b'{"test": true}'
        sig = hmac.new(
            webhook_secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        assert verify_webhook_signature(payload, sig, webhook_secret) is False

    def test_empty_payload(self, webhook_secret):
        """Empty payload should still have valid signature."""
        payload = b""
        sig = hmac.new(
            webhook_secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        assert verify_webhook_signature(payload, f"sha256={sig}", webhook_secret) is True
