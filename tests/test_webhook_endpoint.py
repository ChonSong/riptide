# riptide/tests/test_webhook_endpoint.py
"""
Webhook endpoint: routing, signature validation, idempotency.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


class TestWebhookEndpoint:
    """Test webhook endpoint routing and signature validation."""

    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_invalid_signature_returns_401(self, client, invalid_signature, webhook_body):
        resp = client.post(
            "/webhook/github",
            content=webhook_body,
            headers={
                "X-Hub-Signature-256": invalid_signature,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "delivery-invalid-sig",
            },
        )
        assert resp.status_code == 401

    def test_valid_signature_routes_to_handler(self, client, valid_signature, webhook_body):
        with patch("riptide.webhook.handle_pull_request") as mock_handler:
            mock_handler.return_value = MagicMock(status_code=200)
            resp = client.post(
                "/webhook/github",
                content=webhook_body,
                headers={
                    "X-Hub-Signature-256": valid_signature,
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": "delivery-valid-route-fresh",
                },
            )
            assert resp.status_code == 200
            mock_handler.assert_called_once()

    def test_unhandled_event_returns_200(self, client, valid_signature, webhook_body):
        resp = client.post(
            "/webhook/github",
            content=webhook_body,
            headers={
                "X-Hub-Signature-256": valid_signature,
                "X-GitHub-Event": "status",
                "X-GitHub-Delivery": "delivery-unhandled-fresh",
            },
        )
        assert resp.status_code == 200

    def test_missing_event_header(self, client, valid_signature, webhook_body):
        resp = client.post(
            "/webhook/github",
            content=webhook_body,
            headers={
                "X-Hub-Signature-256": valid_signature,
                "X-GitHub-Delivery": "delivery-missing-event-fresh",
            },
        )
        # Should handle gracefully (either 200 or 400, not 500)
        assert resp.status_code in (200, 400)

    def test_idempotent_delivery_drops_duplicate(self, client, valid_signature, webhook_body):
        """Same X-GitHub-Delivery must not trigger twice."""
        with patch("riptide.webhook.handle_pull_request") as mock_handler:
            mock_handler.return_value = MagicMock(status_code=200)

            # First delivery — should be processed
            resp1 = client.post(
                "/webhook/github",
                content=webhook_body,
                headers={
                    "X-Hub-Signature-256": valid_signature,
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": "delivery-dup-test-1",
                },
            )

            # Same delivery ID again — should be dropped by dedup
            resp2 = client.post(
                "/webhook/github",
                content=webhook_body,
                headers={
                    "X-Hub-Signature-256": valid_signature,
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": "delivery-dup-test-1",
                },
            )

            # Handler called only once — second delivery dropped by dedup
            assert mock_handler.call_count == 1
            assert resp1.status_code == 200
            assert resp2.status_code == 200


class TestPullRequestRunsCompanionFlow:
    """Stage 3: PR events must run the deterministic companion pipeline,
    not the legacy T0 dispatcher."""

    PR_BODY = {
        "action": "opened",
        "installation": {"id": 4242},
        "repository": {"full_name": "owner/repo", "name": "repo"},
        "pull_request": {
            "number": 7,
            "title": "feat: auth",
            "user": {"login": "author"},
        },
    }

    def _post(self, client, signature_factory, delivery):
        import json
        body = json.dumps(self.PR_BODY).encode()
        sig = signature_factory(body)
        return client.post(
            "/webhook/github",
            content=body,
            headers={
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": delivery,
            },
        )

    def test_pr_event_invokes_companion_run_for_pr(self, client, webhook_secret, monkeypatch):
        """Default (no RIPTIDE_T0_FALLBACK): PR path calls companion.run_for_pr."""
        import time
        import json
        from unittest.mock import patch, MagicMock

        # Enable the github-client branch so changed_files are fetched.
        # GITHUB_PRIVATE_KEY_PATH is captured at module import, so patch the
        # module constant directly rather than the env var.
        monkeypatch.setattr("riptide.webhook.GITHUB_PRIVATE_KEY_PATH", "/tmp/test-key.pem")

        called = {}

        def fake_run_for_pr(*args, **kwargs):
            called["args"] = args
            called["kwargs"] = kwargs

        mock_companion = MagicMock()
        mock_companion.is_active_for.return_value = True
        mock_companion.run_for_pr.side_effect = fake_run_for_pr

        mock_github = MagicMock()
        mock_github.get_pr_files.return_value = [{"filename": "a.py", "patch": "+x", "additions": 1, "deletions": 0, "status": "modified"}]
        mock_github.get_pr_details.return_value = {"head": {"sha": "abc123"}}

        with patch("riptide.webhook.get_companion", return_value=mock_companion), \
             patch("riptide.webhook.github_client", return_value=mock_github), \
             patch("riptide.webhook.get_labeler", return_value=None):
            body = json.dumps(self.PR_BODY).encode()
            sig = _sign(body, webhook_secret)
            resp = client.post(
                "/webhook/github",
                content=body,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": "delivery-stage3-run",
                },
            )
            # The companion flow runs on a daemon thread — give it a beat.
            deadline = time.time() + 3
            while time.time() < deadline and not called:
                time.sleep(0.05)

        assert resp.status_code == 200
        assert called, "companion.run_for_pr was never invoked"
        args = called["args"]
        # (installation_id, owner, repo, pr_number, title, author, changed_files)
        assert args[0] == 4242
        assert args[1] == "owner"
        assert args[2] == "repo"
        assert args[3] == 7
        assert args[4] == "feat: auth"
        assert args[5] == "author"
        assert args[6][0]["filename"] == "a.py"
        # Legacy dispatcher must not have been used
        mock_companion.review_pr.assert_not_called()

    def test_t0_fallback_env_routes_to_legacy(self, client, webhook_secret, monkeypatch):
        """RIPTIDE_T0_FALLBACK=1 preserves the legacy dispatcher path."""
        import time
        import json
        from unittest.mock import patch, MagicMock

        monkeypatch.setenv("RIPTIDE_T0_FALLBACK", "1")

        mock_companion = MagicMock()
        mock_companion.is_active_for.return_value = True

        mock_github = MagicMock()
        mock_github.get_pr_files.return_value = []
        mock_github.get_pr_details.return_value = {"head": {"sha": "abc123"}}

        with patch("riptide.webhook.get_companion", return_value=mock_companion), \
             patch("riptide.webhook.github_client", return_value=mock_github), \
             patch("riptide.webhook.get_labeler", return_value=None), \
             patch("riptide.webhook.T0Orchestrator") as mock_t0:
            body = json.dumps(self.PR_BODY).encode()
            sig = _sign(body, webhook_secret)
            resp = client.post(
                "/webhook/github",
                content=body,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": "delivery-stage3-t0",
                },
            )
            # T0 orchestrator runs on a daemon thread.
            deadline = time.time() + 3
            while time.time() < deadline and not mock_t0.return_value.review_pr.called:
                time.sleep(0.05)

        assert resp.status_code == 200
        mock_t0.return_value.review_pr.assert_called_once()
        mock_companion.run_for_pr.assert_not_called()


def _sign(body: bytes, secret: str) -> str:
    """Build a valid X-Hub-Signature-256 for a payload."""
    import hmac
    import hashlib
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestFixCommandGating:
    """
    Stage 4 lock-in: `@riptide-bot fix` is COMMAND-ONLY.

    Nothing in the pipeline writes code except an explicit
    `@riptide-bot fix [description]` comment. These tests prove the webhook
    routes ONLY the exact command to the fixer, and never a casual mention.
    """

    COMMENT_BASE = {
        "action": "created",
        "installation": {"id": 4242},
        "repository": {"full_name": "owner/repo", "name": "repo"},
        "issue": {
            "number": 7,
            "pull_request": {"url": "https://api.github.com/repos/owner/repo/pulls/7"},
            "user": {"login": "author"},
        },
    }

    def _post_comment(self, client, webhook_secret, body, delivery):
        import json
        payload = dict(self.COMMENT_BASE)
        payload["comment"] = {"body": body, "user": {"login": "author", "type": "User"}}
        raw = json.dumps(payload).encode()
        sig = _sign(raw, webhook_secret)
        return client.post(
            "/webhook/github",
            content=raw,
            headers={
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "issue_comment",
                "X-GitHub-Delivery": delivery,
            },
        )

    def test_exact_fix_command_invokes_fixer(self, client, webhook_secret):
        """`@riptide-bot fix` must route to handle_fix_command via work queue."""
        import threading
        from unittest.mock import patch, MagicMock

        gh_instance = MagicMock()
        fix_done = threading.Event()

        def fake_fix(*args, **kwargs):
            fix_done.set()
            return "🛠 Riptide Fix triggered"

        mock_state_class = MagicMock()
        mock_state_instance = MagicMock()
        mock_state_class.return_value = mock_state_instance
        mock_state_instance.enqueue_work.return_value = True

        with patch("riptide.webhook.get_companion", return_value=None), \
             patch("riptide.fixer.handle_fix_command", side_effect=fake_fix) as fix, \
             patch("riptide.webhook.github_client", return_value=gh_instance), \
             patch("riptide.webhook.StateStore", mock_state_class):
            resp = self._post_comment(
                client, webhook_secret,
                "@riptide-bot fix add tests for the new endpoint",
                "delivery-fix-exact",
            )
            assert fix_done.wait(timeout=3), "handle_fix_command was not called"
        assert resp.status_code == 200
        assert fix.call_count == 1
        args = fix.call_args
        assert args.args[1] == 4242          # installation_id
        assert args.args[2] == "owner"       # owner
        assert args.args[3] == "repo"        # repo
        assert args.args[4] == 7             # pr_number
        assert args.args[5] == "author"      # commenter
        assert args.args[6] == "add tests for the new endpoint"  # description
        gh_instance.post_pr_comment.assert_called_once()

    def test_casual_fix_mention_does_not_invoke_fixer(self, client, webhook_secret):
        """'please fix this' WITHOUT the command must NOT write code."""
        from unittest.mock import patch

        with patch("riptide.webhook.get_companion", return_value=None), \
             patch("riptide.fixer.handle_fix_command") as fix, \
             patch("riptide.webhook.github_client") as gh:
            resp = self._post_comment(
                client, webhook_secret,
                "could you please fix this bug when you get a chance?",
                "delivery-fix-casual",
            )
        assert resp.status_code == 200
        fix.assert_not_called()
        gh.return_value.post_pr_comment.assert_not_called()

    def test_other_bot_command_does_not_invoke_fixer(self, client, webhook_secret):
        """`@riptide-bot review` (read-only) must NOT route to the fixer."""
        import threading
        from unittest.mock import patch, MagicMock

        review_done = threading.Event()

        def fake_review(*args, **kwargs):
            review_done.set()
            return None

        mock_state_class = MagicMock()
        mock_state_instance = MagicMock()
        mock_state_class.return_value = mock_state_instance
        mock_state_instance.enqueue_work.return_value = True

        with patch("riptide.webhook.get_companion", return_value=None), \
             patch("riptide.fixer.handle_fix_command") as fix, \
             patch("riptide.deepthink.handle_review_command", side_effect=fake_review) as review, \
             patch("riptide.webhook.github_client") as gh, \
             patch("riptide.webhook.StateStore", mock_state_class):
            resp = self._post_comment(
                client, webhook_secret,
                "@riptide-bot review",
                "delivery-fix-review",
            )
            assert review_done.wait(timeout=3), "handle_review_command was not called"
        assert resp.status_code == 200
        fix.assert_not_called()
        review.assert_called_once()

    def test_fix_command_by_bot_is_skipped(self, client, webhook_secret, monkeypatch):
        """Bot's own comments must never re-trigger fix (no self-loop)."""
        import json
        from unittest.mock import patch

        monkeypatch.setenv("GITHUB_APP_SLUG", "riptide")

        payload = dict(self.COMMENT_BASE)
        payload["comment"] = {
            "body": "@riptide-bot fix add tests",
            "user": {"login": "riptide[bot]", "type": "Bot"},
        }
        raw = json.dumps(payload).encode()
        sig = _sign(raw, webhook_secret)
        with patch("riptide.webhook.get_companion", return_value=None), \
             patch("riptide.fixer.handle_fix_command") as fix, \
             patch("riptide.webhook.github_client") as gh:
            resp = client.post(
                "/webhook/github",
                content=raw,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "issue_comment",
                    "X-GitHub-Delivery": "delivery-fix-self",
                },
            )
        assert resp.status_code == 200
        fix.assert_not_called()
        gh.return_value.post_pr_comment.assert_not_called()


import time
import tempfile
import os


class TestWorkQueueRecovery:
    """Tests for work_queue startup recovery."""

    def test_recover_pending_work_marks_old_items_failed(self):
        """recover_pending_work() marks items older than 5 minutes as failed."""
        from riptide.state import StateStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_state.db")
            store = StateStore(db_path)

            # Insert an old item (10 minutes old)
            store.enqueue_work("old-review-123", "review", {"pr_number": 1})
            conn = store._get_conn()
            conn.execute(
                "UPDATE work_queue SET created_at = ? WHERE id = ?",
                (time.time() - 600, "old-review-123"),
            )
            conn.commit()

            # Recover should mark it as stale and return empty list
            pending = store.recover_pending_work()
            assert len(pending) == 0

            # Verify it was marked as stale
            conn = store._get_conn()
            row = conn.execute(
                "SELECT status, error FROM work_queue WHERE id = ?",
                ("old-review-123",),
            ).fetchone()
            assert row[0] == "failed"
            assert row[1] == "stale"

    def test_recover_pending_work_returns_recent_items(self):
        """recover_pending_work() returns items younger than 5 minutes."""
        from riptide.state import StateStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_state.db")
            store = StateStore(db_path)

            # Insert a recent item
            store.enqueue_work("recent-review-456", "review", {"pr_number": 2})

            # Recover should return it
            pending = store.recover_pending_work()
            assert len(pending) == 1
            assert pending[0]["id"] == "recent-review-456"
            assert pending[0]["kind"] == "review"
            assert pending[0]["payload"]["pr_number"] == 2

    def test_enqueue_work_is_idempotent(self):
        """enqueue_work() returns False for duplicate work_id."""
        import uuid
        from riptide.state import StateStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_state.db")
            store = StateStore(db_path)
            
            unique_id = f"work-{uuid.uuid4()}"

            # First enqueue should succeed
            result1 = store.enqueue_work(unique_id, "review", {"pr_number": 1})
            assert result1 is True

            # Second enqueue with same ID should return False
            result2 = store.enqueue_work(unique_id, "review", {"pr_number": 1})
            assert result2 is False

    def test_complete_work_only_transitions_pending(self):
        """complete_work() only transitions pending rows."""
        import uuid
        from riptide.state import StateStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_state.db")
            store = StateStore(db_path)
            
            unique_id = f"work-{uuid.uuid4()}"

            store.enqueue_work(unique_id, "fix", {"pr_number": 3})

            # First complete should succeed
            result1 = store.complete_work(unique_id)
            assert result1 is True

            # Second complete should return False (already completed)
            result2 = store.complete_work(unique_id)
            assert result2 is False

    def test_complete_work_with_error_stores_traceback(self):
        """complete_work() with error stores both error message and traceback."""
        from riptide.state import StateStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_state.db")
            store = StateStore(db_path)

            store.enqueue_work("work-789", "review", {"pr_number": 4})

            error_msg = "Something went wrong"
            traceback_text = "Traceback (most recent call last):\n  File ..."
            store.complete_work("work-789", error=error_msg, traceback_str=traceback_text)

            conn = store._get_conn()
            row = conn.execute(
                "SELECT status, error, traceback FROM work_queue WHERE id = ?",
                ("work-789",),
            ).fetchone()
            assert row[0] == "failed"
            assert row[1] == error_msg
            assert row[2] == traceback_text
