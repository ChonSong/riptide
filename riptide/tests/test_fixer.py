"""Tests for riptide/fixer.py — @riptide-bot fix command."""
import os
from unittest.mock import patch, MagicMock

import pytest

from riptide.fixer import (
    FIX_RE,
    OUR_USERNAME,
    handle_fix_command,
    _is_push_eligible,
    _is_fork_push_eligible,
    _is_cron_available,
    _spawn_fix,
    _build_fix_prompt,
)


# ── FIX_RE ──────────────────────────────────────────────────────────────────


class TestFixRe:
    def test_matches_bare_fix(self):
        m = FIX_RE.search("@riptide-bot fix")
        assert m is not None
        assert m.group(1).strip() == ""

    def test_matches_fix_with_description(self):
        m = FIX_RE.search("@riptide-bot fix the flaky test in test_foo.py")
        assert m is not None
        assert m.group(1).strip() == "the flaky test in test_foo.py"

    def test_matches_multiline_description(self):
        m = FIX_RE.search("@riptide-bot fix\nline two of description")
        assert m is not None
        assert "line two" in m.group(1)

    def test_case_insensitive(self):
        assert FIX_RE.search("@Riptide-Bot FIX this") is not None

    def test_does_not_match_review(self):
        assert FIX_RE.search("@riptide-bot review") is None

    def test_does_not_match_companion_skip(self):
        assert FIX_RE.search("@riptide-bot companion skip") is None

    def test_does_not_match_deepthink(self):
        assert FIX_RE.search("@riptide-bot deepthink") is None

    def test_rejects_prefix_words(self):
        # "prefix" contains "fix" but must not match (\b word boundary)
        assert FIX_RE.search("@riptide-bot prefix") is None


# ── Fixer Defaults ──────────────────────────────────────────────────────────


class TestFixerDefaults:
    """Verify fixer defaults route to LongCat, not OpenRouter."""

    def teardown_method(self):
        """Restore riptide.fixer module state after each test."""
        import importlib
        import riptide.fixer
        importlib.reload(riptide.fixer)

    def test_default_fix_provider_is_longcat(self):
        """Default provider must be 'longcat', not 'custom'."""
        with patch.dict(os.environ, {}, clear=True):
            import importlib
            import riptide.fixer
            importlib.reload(riptide.fixer)
            assert riptide.fixer.FIX_PROVIDER == "longcat", (
                f"Expected FIX_PROVIDER='longcat', got '{riptide.fixer.FIX_PROVIDER}'. "
                f"provider='custom' resolves to OpenRouter, not LongCat."
            )

    def test_default_fix_model_is_longcat(self):
        """Default model must be 'LongCat-2.0' without 'custom:' prefix."""
        with patch.dict(os.environ, {}, clear=True):
            import importlib
            import riptide.fixer
            importlib.reload(riptide.fixer)
            assert riptide.fixer.FIX_MODEL == "LongCat-2.0", (
                f"Expected FIX_MODEL='LongCat-2.0', got '{riptide.fixer.FIX_MODEL}'"
            )

    def test_fix_provider_env_override(self):
        """Env vars override defaults."""
        with patch.dict(os.environ, {"RIPTIDE_FIX_PROVIDER": "custom", "RIPTIDE_FIX_MODEL": "custom:LongCat-2.0"}, clear=True):
            import importlib
            import riptide.fixer
            importlib.reload(riptide.fixer)
            assert riptide.fixer.FIX_PROVIDER == "custom"
            assert riptide.fixer.FIX_MODEL == "custom:LongCat-2.0"


# ── Authorization gate ───────────────────────────────────────────────────────


class TestHandleFixCommandAuth:
    def _pr_details(self, author="ChonSong", head_ref="feat/x",
                    head_sha="abc123", head_repo="ChonSong/riptide"):
        return {
            "title": "Test PR",
            "user": {"login": author},
            "additions": 10, "deletions": 5,
            "head": {"sha": head_sha, "ref": head_ref,
                     "repo": {"full_name": head_repo}},
        }

    def test_author_can_trigger(self):
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details(author="alice")
        with patch("riptide.fixer._spawn_fix", return_value=True), \
             patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "alice")
            assert result is not None
            assert "Fix triggered" in result

    def test_owner_can_trigger(self):
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details(author="alice")
        with patch("riptide.fixer._spawn_fix", return_value=True), \
             patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "ChonSong")
            assert result is not None
            assert "Fix triggered" in result

    def test_other_user_cannot_trigger(self):
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details(author="alice")
        result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "bob")
        assert "Not authorized" in result


# ── Push eligibility ─────────────────────────────────────────────────────────


class TestIsPushEligible:
    def test_owned_repo_eligible(self):
        assert _is_push_eligible("ChonSong", "riptide", "alice") is True

    def test_author_pr_eligible(self):
        assert _is_push_eligible("other", "repo", "ChonSong") is True

    def test_foreign_repo_ineligible(self):
        assert _is_push_eligible("other", "repo", "alice") is False


# ── Fork push eligibility ────────────────────────────────────────────────────


class TestIsForkPushEligible:
    def test_same_repo_always_eligible(self):
        assert _is_fork_push_eligible(False, "alice") is True

    def test_fork_authored_by_us_eligible(self):
        assert _is_fork_push_eligible(True, OUR_USERNAME) is True

    def test_fork_authored_by_stranger_ineligible(self):
        assert _is_fork_push_eligible(True, "stranger") is False


# ── Fork detection ───────────────────────────────────────────────────────────


class TestForkDetection:
    """Tests for fork/same-repo detection and push eligibility."""

    def _pr_details(self, head_repo=None):
        head = {"sha": "abc", "ref": "feat/x"}
        if head_repo:
            head["repo"] = {"full_name": head_repo}
        return {"title": "t", "user": {"login": "a"}, "additions": 1, "deletions": 0, "head": head}

    def test_same_repo_not_fork(self):
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details("ChonSong/riptide")
        with patch("riptide.fixer._spawn_fix", return_value=True), \
             patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "ChonSong")
            assert result is not None
            assert "push fixes directly" in result

    def test_fork_repo_comment_only(self):
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details("other/riptide")
        with patch("riptide.fixer._spawn_fix", return_value=True), \
             patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "ChonSong")
            assert result is not None
            assert "comment-only patch" in result

    def test_fork_authored_by_us_push_eligible(self):
        # Fork PR authored by OUR_USERNAME: push fixes directly (we own the head branch).
        client = MagicMock()
        details = self._pr_details("other/riptide")
        details["user"] = {"login": OUR_USERNAME}
        client.get_pr_details.return_value = details
        with patch("riptide.fixer._spawn_fix", return_value=True), \
             patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            result = handle_fix_command(client, 1, "someone-else", "riptide", 42, OUR_USERNAME)
            assert result is not None
            assert "push fixes directly" in result

    def test_missing_head_repo_treated_as_fork(self):
        """If head.repo is missing (deleted fork), treat as fork (comment-only)."""
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details(None)
        with patch("riptide.fixer._spawn_fix", return_value=True), \
             patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "ChonSong")
            assert result is not None
            assert "comment-only patch" in result


# ── _is_cron_available ───────────────────────────────────────────────────────


class TestIsCronAvailable:
    def test_hermes_available(self):
        with patch("shutil.which", return_value="/usr/bin/hermes"):
            assert _is_cron_available() is True

    def test_hermes_not_available(self):
        with patch("shutil.which", return_value=None):
            assert _is_cron_available() is False


# ── _spawn_fix ───────────────────────────────────────────────────────────────


class TestSpawnFix:
    def _kwargs(self, **overrides) -> dict:
        base: dict = dict(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_title="fix: repair flaky test",
            pr_author="test-user",
            total_loc=150,
            head_sha="abc123def4567890",
            head_ref="fix-branch",
            description="",
            push_eligible=True,
        )
        base.update(overrides)
        return base

    def test_spawn_builds_correct_command(self):
        with patch("riptide.state.StateStore") as mock_state, \
             patch("subprocess.run") as mock_run:
            mock_state.return_value.reserve_job.return_value = True
            mock_run.return_value = MagicMock(returncode=0)
            result = _spawn_fix(**self._kwargs())
        assert result is True
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["hermes", "cron", "create"]
        assert "riptide-fix-ChonSong-riptide-42" in cmd
        assert "github-pr-lifecycle" in cmd
        assert "deep-think" in cmd
        assert "riptide-fix" in cmd

    def test_spawn_prompt_embeds_pr_context(self):
        with patch("riptide.state.StateStore") as mock_state, \
             patch("subprocess.run") as mock_run:
            mock_state.return_value.reserve_job.return_value = True
            mock_run.return_value = MagicMock(returncode=0)
            _spawn_fix(**self._kwargs(description="the flaky webhook test"))
        prompt = mock_run.call_args[0][0][4]
        assert "#42" in prompt
        assert "ChonSong/riptide" in prompt
        assert "abc123def45" in prompt
        assert "fix-branch" in prompt
        assert "the flaky webhook test" in prompt

    def test_spawn_not_push_eligible_uses_patch_path(self):
        with patch("riptide.state.StateStore") as mock_state, \
             patch("subprocess.run") as mock_run:
            mock_state.return_value.reserve_job.return_value = True
            mock_run.return_value = MagicMock(returncode=0)
            _spawn_fix(**self._kwargs(push_eligible=False))
        prompt = mock_run.call_args[0][0][4]
        assert "Do NOT push" in prompt

    def test_spawn_skips_when_job_already_pending(self):
        with patch("riptide.state.StateStore") as mock_state, \
             patch("subprocess.run") as mock_run:
            mock_state.return_value.reserve_job.return_value = False
            result = _spawn_fix(**self._kwargs())
        assert result is False
        mock_run.assert_not_called()

    def test_spawn_retries_on_failure(self):
        with patch("riptide.state.StateStore") as mock_state, \
             patch("subprocess.run") as mock_run, \
             patch("time.sleep"):
            mock_state.return_value.reserve_job.return_value = True
            mock_run.return_value = MagicMock(returncode=1)
            result = _spawn_fix(**self._kwargs())
        assert result is False
        assert mock_run.call_count == 3

    def test_spawn_marks_failed_on_prompt_build_error(self):
        with patch("riptide.state.StateStore") as mock_state, \
             patch("riptide.fixer._build_fix_prompt", side_effect=ValueError("boom")), \
             patch("subprocess.run"):
            mock_state.return_value.reserve_job.return_value = True
            result = _spawn_fix(**self._kwargs())
        assert result is False
        mock_state.return_value.mark_failed.assert_called_once()


# ── _build_fix_prompt ────────────────────────────────────────────────────────


class TestBuildFixPrompt:
    def _kwargs(self, **overrides) -> dict:
        base: dict = dict(
            owner="ChonSong",
            repo="riptide",
            pr_number=7,
            pr_title="feat: thing",
            pr_author="alice",
            total_loc=200,
            head_sha="deadbeefcafe1234",
            head_ref="feat-thing",
            description="",
            push_eligible=True,
            job_id="test-job-id",
        )
        base.update(overrides)
        return base

    def test_bare_fix_scopes_to_all_findings(self):
        prompt = _build_fix_prompt(**self._kwargs())
        assert "ALL outstanding findings" in prompt

    def test_description_scopes_to_described_problem(self):
        prompt = _build_fix_prompt(**self._kwargs(description="the N+1 query"))
        assert "ONLY the problem described" in prompt
        assert "the N+1 query" in prompt

    def test_prompt_contains_safety_constraints(self):
        prompt = _build_fix_prompt(**self._kwargs())
        assert "NEVER edit github-private-key.pem" in prompt
        assert "NO force-push" in prompt
        assert "No push on red tests" in prompt

    def test_prompt_contains_verification_gate(self):
        prompt = _build_fix_prompt(**self._kwargs())
        assert "skip-already-addressed" in prompt
        assert "skip-stale-false-positive" in prompt

    def test_prompt_push_eligible_includes_push_instructions(self):
        prompt = _build_fix_prompt(**self._kwargs(push_eligible=True))
        assert "git push origin HEAD:feat-thing" in prompt

    def test_prompt_not_push_eligible_says_do_not_push(self):
        prompt = _build_fix_prompt(**self._kwargs(push_eligible=False))
        assert "Do NOT push" in prompt


# ── handle_fix_command integration ───────────────────────────────────────────


def _make_client(pr_details=None, exc=None):
    client = MagicMock()
    if exc:
        client.get_pr_details.side_effect = exc
    else:
        client.get_pr_details.return_value = pr_details
    return client


def _pr_details(**overrides):
    base = {
        "title": "fix: repair flaky test",
        "user": {"login": "test-user"},
        "additions": 100,
        "deletions": 50,
        "head": {
            "sha": "abc123def4567890",
            "ref": "fix-branch",
            "repo": {"full_name": "ChonSong/riptide"},
        },
    }
    base.update(overrides)
    return base


class TestHandleFixCommand:
    def test_returns_confirmation_on_success(self):
        # PR author triggering their own fix
        client = _make_client(pr_details=_pr_details())
        with patch("riptide.fixer._spawn_fix", return_value=True), \
             patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")
        assert "🛠 **Riptide Fix triggered for #42!**" in result
        assert "fix: repair flaky test" in result
        assert "@test-user" in result
        assert "+100/-50 (150 LOC)" in result

    def test_confirmation_mentions_description_scope(self):
        # Repo owner triggering fix
        client = _make_client(pr_details=_pr_details())
        with patch("riptide.fixer._spawn_fix", return_value=True) as mock_spawn, \
             patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            result = handle_fix_command(
                client, 1, "ChonSong", "riptide", 42, "ChonSong", "the flaky webhook test"
            )
        assert "the flaky webhook test" in result
        assert mock_spawn.call_args[1]["description"] == "the flaky webhook test"

    def test_fork_pr_gets_patch_mode(self):
        details = _pr_details()
        details["head"]["repo"]["full_name"] = "external-user/riptide"
        client = _make_client(pr_details=details)
        # Foreign repo, foreign author, ChonSong commenting — fork blocks push
        with patch("riptide.fixer._spawn_fix", return_value=True) as mock_spawn, \
             patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "ChonSong", "")
        assert "comment-only patch" in result
        assert mock_spawn.call_args[1]["push_eligible"] is False

    def test_fork_pr_authored_by_us_is_push_eligible(self):
        # We (ChonSong) authored this fork PR — we own the head branch
        details = _pr_details()
        details["head"]["repo"]["full_name"] = "ChonSong/riptide"  # our fork
        details["user"]["login"] = "ChonSong"  # we authored it
        client = _make_client(pr_details=details)
        with patch("riptide.fixer._spawn_fix", return_value=True) as mock_spawn, \
             patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            result = handle_fix_command(client, 1, "someone-else", "riptide", 42, "ChonSong", "")
        assert "push fixes directly" in result
        assert mock_spawn.call_args[1]["push_eligible"] is True

    def test_returns_error_when_pr_fetch_fails(self):
        client = _make_client(exc=RuntimeError("404"))
        # Use authorized commenter to get past auth gate to the fetch failure
        result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "ChonSong", "")
        assert "⚠️ Could not fetch PR #42 details" in result

    def test_returns_error_when_spawn_raises(self):
        client = _make_client(pr_details=_pr_details())
        with patch("riptide.fixer._spawn_fix", side_effect=RuntimeError("boom")), \
             patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")
        assert "⚠️ Failed to spawn fix session for #42" in result


# ── Fix Queue ─────────────────────────────────────────────────────────────


class TestFixQueue:
    """Fix queue provides serialization and queuing when fixes are busy."""

    def test_enqueue_fix_returns_id(self, tmp_path):
        """Enqueuing a fix returns a valid queue id."""
        from riptide.state import StateStore
        store = StateStore(db_path=str(tmp_path / "test.db"))
        qid = store.enqueue_fix(155, "o/r#155", "chonsong", "test fix")
        assert isinstance(qid, int)
        assert qid > 0

    def test_queue_length_counts_only_queued(self, tmp_path):
        """get_queue_length counts only items with status='queued'."""
        from riptide.state import StateStore
        store = StateStore(db_path=str(tmp_path / "test.db"))
        store.enqueue_fix(155, "o/r#155", "user1", "")
        store.enqueue_fix(155, "o/r#155", "user2", "")
        assert store.get_queue_length(155) == 2

    def test_start_next_queued_fix_fifo(self, tmp_path):
        """start_next_queued_fix pops in FIFO order."""
        from riptide.state import StateStore
        store = StateStore(db_path=str(tmp_path / "test.db"))
        store.enqueue_fix(155, "o/r#155", "first", "desc1")
        store.enqueue_fix(156, "o/r#156", "second", "desc2")

        item = store.start_next_queued_fix()
        assert item is not None
        assert item["pr_number"] == 155
        assert item["commenter"] == "first"

        item2 = store.start_next_queued_fix()
        assert item2 is not None
        assert item2["pr_number"] == 156

    def test_start_next_queued_fix_empty(self, tmp_path):
        """start_next_queued_fix returns None when queue is empty."""
        from riptide.state import StateStore
        store = StateStore(db_path=str(tmp_path / "test.db"))
        assert store.start_next_queued_fix() is None

    def test_queue_position(self, tmp_path):
        """get_queue_position returns 1-based position."""
        from riptide.state import StateStore
        store = StateStore(db_path=str(tmp_path / "test.db"))
        qid1 = store.enqueue_fix(155, "o/r#155", "first", "")
        qid2 = store.enqueue_fix(156, "o/r#156", "second", "")

        assert store.get_queue_position(qid1) == 1
        assert store.get_queue_position(qid2) == 2

    def test_queue_position_none_after_start(self, tmp_path):
        """Once started, item no longer has a queue position."""
        from riptide.state import StateStore
        store = StateStore(db_path=str(tmp_path / "test.db"))
        qid = store.enqueue_fix(155, "o/r#155", "user", "")
        store.start_next_queued_fix()
        assert store.get_queue_position(qid) is None

    def test_complete_fix_queue_item(self, tmp_path):
        """complete_fix_queue_item updates status to completed/failed."""
        from riptide.state import StateStore
        store = StateStore(db_path=str(tmp_path / "test.db"))
        qid = store.enqueue_fix(155, "o/r#155", "user", "")
        store.start_next_queued_fix()
        store.complete_fix_queue_item(qid, success=True)

        conn = store._get_conn()
        row = conn.execute("SELECT status FROM fix_queue WHERE id = ?", (qid,)).fetchone()
        assert row[0] == "completed"

    def test_has_running_fix_by_job(self, tmp_path):
        """has_running_fix returns True when a pending job exists."""
        from riptide.state import StateStore
        store = StateStore(db_path=str(tmp_path / "test.db"))
        assert store.has_running_fix() is False
        store.create_job("riptide-fix-o-r-155-abc", 155, "t1")
        assert store.has_running_fix() is True

    def test_has_running_fix_by_queue(self, tmp_path):
        """has_running_fix returns True when a queue item is 'running'."""
        from riptide.state import StateStore
        store = StateStore(db_path=str(tmp_path / "test.db"))
        qid = store.enqueue_fix(155, "o/r#155", "user", "")
        store.start_next_queued_fix()
        assert store.has_running_fix() is True

    def test_get_running_fix_pr(self, tmp_path):
        """get_running_fix_pr returns the PR of the active fix."""
        from riptide.state import StateStore
        store = StateStore(db_path=str(tmp_path / "test.db"))
        assert store.get_running_fix_pr() is None
        store.create_job("riptide-fix-o-r-155-abc", 155, "t1")
        assert store.get_running_fix_pr() == 155

    def test_cleanup_stale_queue_items(self, tmp_path):
        """Stale 'running' items are marked failed after max_age."""
        import time
        from riptide.state import StateStore
        store = StateStore(db_path=str(tmp_path / "test.db"))
        qid = store.enqueue_fix(155, "o/r#155", "user", "")
        store.start_next_queued_fix()
        # Manually backdate started_at to simulate crash
        conn = store._get_conn()
        conn.execute("UPDATE fix_queue SET started_at = ? WHERE id = ?", (time.time() - 7200, qid))
        conn.commit()
        store.cleanup_stale_queue_items(max_age_seconds=3600)
        row = conn.execute("SELECT status FROM fix_queue WHERE id = ?", (qid,)).fetchone()
        assert row[0] == "failed"


class TestHandleFixCommandQueue:
    """handle_fix_command queues instead of silently rejecting when busy."""

    @patch("riptide.fixer._spawn_fix")
    def test_first_request_spawns_immediately(self, mock_spawn, tmp_path, monkeypatch):
        """When no fix is running, the first request spawns."""
        mock_spawn.return_value = True
        monkeypatch.chdir(tmp_path)
        from riptide.state import StateStore
        from riptide import state as state_mod
        monkeypatch.setattr(state_mod, "StateStore", lambda: StateStore(db_path=str(tmp_path / "test.db")))

        client = MagicMock()
        client.get_pr_details.return_value = {
            "title": "Test PR",
            "user": {"login": "chonsong"},
            "additions": 10,
            "deletions": 5,
            "head": {"sha": "abc123", "ref": "fix-branch", "repo": {"full_name": "chonsong/riptide"}},
        }
        result = handle_fix_command(client, 123, "chonsong", "riptide", 155, "chonsong")
        assert result is not None
        assert "triggered" in result

    def test_second_request_same_pr_queued(self, tmp_path, monkeypatch):
        """When a fix is already running, the second request is queued."""
        monkeypatch.chdir(tmp_path)
        from riptide.state import StateStore
        from riptide import state as state_mod
        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr(state_mod, "StateStore", lambda: StateStore(db_path=db_path))

        client = MagicMock()
        client.get_pr_details.return_value = {
            "title": "Test PR",
            "user": {"login": "chonsong"},
            "additions": 10,
            "deletions": 5,
            "head": {"sha": "abc123", "ref": "fix-branch", "repo": {"full_name": "chonsong/riptide"}},
        }

        # Simulate a running fix (as if _spawn_fix created a job)
        store = StateStore(db_path=db_path)
        store.create_job("riptide-fix-chonsong-riptide-155-abc", 155, "t1")

        # New fix request should be queued
        result = handle_fix_command(client, 123, "chonsong", "riptide", 155, "chonsong")
        assert result is not None
        assert "queued" in result

    def test_second_request_different_pr_queued(self, tmp_path, monkeypatch):
        """When a fix is running for another PR, queue with global message."""
        monkeypatch.chdir(tmp_path)
        from riptide.state import StateStore
        from riptide import state as state_mod
        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr(state_mod, "StateStore", lambda: StateStore(db_path=db_path))

        client = MagicMock()
        client.get_pr_details.return_value = {
            "title": "Test PR",
            "user": {"login": "chonsong"},
            "additions": 10,
            "deletions": 5,
            "head": {"sha": "abc123", "ref": "fix-branch", "repo": {"full_name": "chonsong/riptide"}},
        }

        # Simulate a running fix for PR 155
        store = StateStore(db_path=db_path)
        store.create_job("riptide-fix-chonsong-riptide-155-abc", 155, "t1")

        # Request for PR 156 should be queued globally
        result = handle_fix_command(client, 123, "chonsong", "riptide", 156, "chonsong")
        assert result is not None
        assert "queued" in result

    def test_unauthorized_returns_message(self):
        """Unauthorized attempts always get a response."""
        client = MagicMock()
        client.get_pr_details.return_value = {
            "title": "Test",
            "user": {"login": "someone-else"},
            "additions": 1,
            "deletions": 0,
            "head": {"sha": "x", "ref": "f"},
        }
        result = handle_fix_command(client, 123, "chonsong", "riptide", 155, "random-hacker")
        assert result is not None
        assert "Not authorized" in result

    def test_pr_fetch_failure_returns_message(self):
        """If PR details can't be fetched, user gets an error message."""
        client = MagicMock()
        client.get_pr_details.side_effect = Exception("API rate limit")
        result = handle_fix_command(client, 123, "chonsong", "riptide", 155, "chonsong")
        assert result is not None
        assert "Could not fetch" in result


# ── Fork detection (legacy name collision tests) ─────────────────────────────


class TestForkDetectionLegacy:
    """Legacy fork detection tests — kept for backward compatibility."""

    def _pr_details(self, head_repo=None):
        head = {"sha": "abc", "ref": "feat/x"}
        if head_repo:
            head["repo"] = {"full_name": head_repo}
        return {"title": "t", "user": {"login": "a"}, "additions": 1, "deletions": 0, "head": head}

    def test_same_repo_not_fork_legacy(self):
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details("ChonSong/riptide")
        with patch("riptide.fixer._spawn_fix", return_value=True), \
             patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "ChonSong")
            assert result is not None

    def test_fork_repo_comment_only_legacy(self):
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details("other/riptide")
        with patch("riptide.fixer._spawn_fix", return_value=True), \
             patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "ChonSong")
            assert result is not None
            assert "comment-only" in result or "push" in result
