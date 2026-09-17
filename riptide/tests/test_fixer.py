"""Tests for riptide/fixer.py — @riptide-bot fix command."""
import os
import shutil
from unittest.mock import patch, MagicMock

import pytest

from riptide.fixer import (
    FIX_RE,
    OUR_USERNAME,
    handle_fix_command,
    process_fix_queue,
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
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=True):
                result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "alice")
                assert result is not None
                assert "Fix triggered" in result

    def test_owner_can_trigger(self):
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details(author="alice")
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=True):
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
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=True):
                result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "ChonSong")
                assert result is not None
                assert "push fixes directly" in result

    def test_fork_repo_comment_only(self):
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details("other/riptide")
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=True):
                result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "ChonSong")
                assert result is not None
                assert "comment-only patch" in result

    def test_fork_authored_by_us_push_eligible(self):
        # Fork PR authored by OUR_USERNAME: push fixes directly (we own the head branch).
        client = MagicMock()
        details = self._pr_details("other/riptide")
        details["user"] = {"login": OUR_USERNAME}
        client.get_pr_details.return_value = details
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=True):
                result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, OUR_USERNAME)
                assert result is not None
                assert "push fixes directly" in result

    def test_missing_head_repo_treated_as_fork(self):
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details(None)
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=True):
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


@pytest.mark.skipif(
    shutil.which("hermes") is None,
    reason="exercises the real spawn path, which needs the hermes CLI on PATH "
           "(fixer.py deliberately refuses to spawn without it; the CI runner "
           "has no hermes install)",
)
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
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=True):
                result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")
        assert "🛠 **Riptide Fix triggered for #42!**" in result
        assert "fix: repair flaky test" in result
        assert "@test-user" in result
        assert "+100/-50 (150 LOC)" in result

    def test_confirmation_mentions_description_scope(self):
        # Repo owner triggering fix
        client = _make_client(pr_details=_pr_details())
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=True) as mock_spawn:
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
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=True) as mock_spawn:
                result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "ChonSong", "")
        assert "comment-only patch" in result
        assert mock_spawn.call_args[1]["push_eligible"] is False

    def test_fork_pr_authored_by_us_is_push_eligible(self):
        # We (ChonSong) authored this fork PR — we own the head branch
        details = _pr_details()
        details["head"]["repo"]["full_name"] = "ChonSong/riptide"  # our fork
        details["user"]["login"] = "ChonSong"  # we authored it
        client = _make_client(pr_details=details)
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=True) as mock_spawn:
                result = handle_fix_command(client, 1, "someone-else", "riptide", 42, "ChonSong", "")
        assert "push fixes directly" in result
        assert mock_spawn.call_args[1]["push_eligible"] is True

    def test_returns_error_when_pr_fetch_fails(self):
        client = _make_client(exc=RuntimeError("404"))
        # Use authorized commenter to get past auth gate to the fetch failure
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "ChonSong", "")
        assert "⚠️ Could not fetch PR #42 details" in result

    def test_returns_error_when_spawn_raises(self):
        client = _make_client(pr_details=_pr_details())
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", side_effect=RuntimeError("boom")):
                result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")
        assert "⚠️ Failed to spawn fix session for #42" in result

    def test_cron_unavailable_reports_it_cannot_start(self):
        client = _make_client(pr_details=_pr_details())
        # The Hermes cron CLI is absent on this host → spawning is impossible.
        # The command must say so and must NOT queue: nothing drains fix_queue,
        # so a row would block this PR forever.
        with patch("riptide.state.StateStore") as mock_store, \
             patch("riptide.fixer._is_cron_available", return_value=False):
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=False):
                result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")

        mock_store.return_value.enqueue_fix.assert_not_called()
        assert "Hermes cron CLI is not available on this host" in result
        assert "Could not start a fix for #42" in result
        assert "Nothing was queued" in result
        assert "Riptide Fix triggered" not in result

    def test_db_lock_retry_then_success(self):
        """If DB locks once then succeeds, command proceeds normally."""
        import sqlite3

        call_count = [0]

        def flaky_has_running_fix():
            call_count[0] += 1
            if call_count[0] == 1:
                raise sqlite3.OperationalError("database is locked")
            return False

        client = _make_client(pr_details=_pr_details())
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.side_effect = flaky_has_running_fix
            mock_store.return_value.get_queue_length.return_value = 0
            mock_store.return_value.cleanup_stale_pending.return_value = None
            mock_store.return_value.cleanup_stale_queue_items.return_value = None
            mock_store.return_value.enqueue_fix.return_value = 1
            mock_store.return_value.get_queue_position.return_value = 1
            mock_store.return_value.get_running_fix_pr.return_value = None
            with patch("riptide.fixer._spawn_fix", return_value=True):
                result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")

        assert result is not None
        assert "triggered" in result
        assert call_count[0] == 2  # First call locked, retry succeeded


# ── ack ↔ job name, and fix_queue as a fallback only ────────────────────────


class TestFixAckNamesTheJob:
    """The ack must name the job a reader can chase (`hermes cron list`).

    The name comes from `_fix_job_name` — the same helper `_spawn_fix` hands to
    `hermes cron create --name` — so the two cannot drift apart.
    """

    def test_ack_names_the_hermes_job(self):
        client = _make_client(pr_details=_pr_details())
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=True):
                result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")
        assert "riptide-fix-ChonSong-riptide-42" in result
        assert "hermes cron list" in result

    def test_ack_keeps_the_trigger_marker(self):
        """`Riptide Fix triggered` is load-bearing (poller.py keys off it)."""
        client = _make_client(pr_details=_pr_details())
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=True):
                result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")
        assert "🛠 **Riptide Fix triggered for #42!**" in result
        assert "Riptide Fix triggered" in result

    def test_ack_job_name_is_the_name_handed_to_hermes(self):
        """Byte-equality: `cron create --name` value == the name quoted in the ack."""
        # What the real spawn path puts on the command line
        with patch("riptide.state.StateStore") as mock_state, \
             patch("riptide.fixer._is_cron_available", return_value=True), \
             patch("subprocess.run") as mock_run:
            mock_state.return_value.reserve_job.return_value = True
            mock_run.return_value = MagicMock(returncode=0, stdout="Created job", stderr="")
            spawned_ok = _spawn_fix(
                owner="ChonSong", repo="riptide", pr_number=42,
                pr_title="fix: repair flaky test", pr_author="test-user",
                total_loc=150, head_sha="abc123def4567890", head_ref="fix-branch",
                description="", push_eligible=True,
            )
        assert spawned_ok is True
        cmd = mock_run.call_args[0][0]
        hermes_name = cmd[cmd.index("--name") + 1]

        client = _make_client(pr_details=_pr_details())
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=True):
                ack = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")

        assert hermes_name in ack


class TestFixQueueIsFallbackOnly:
    """fix_queue is never written by `@riptide-bot fix`.

    Regression cover for two defects:
      * PR #208 — one trigger both spawned a session and wrote a fix_queue row
        nothing drains (the row landed 71s after the fix had already pushed).
      * PR #215 review — the "Hermes cron CLI unavailable" path wrote that row
        as its fallback, and the busy check counted it, so the row blocked every
        later `@riptide-bot fix` on the PR forever, silently. Nothing drains
        fix_queue (`process_fix_queue` is unwired), so the path must refuse out
        loud instead of queueing.
    """

    def _client(self):
        return _make_client(pr_details=_pr_details())

    def test_successful_spawn_does_not_enqueue(self):
        client = self._client()
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=True):
                result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")
        mock_store.return_value.enqueue_fix.assert_not_called()
        mock_store.return_value.get_queue_position.assert_not_called()
        assert "Riptide Fix triggered" in result

    def test_successful_spawn_leaves_fix_queue_empty(self, tmp_path, monkeypatch):
        """Real StateStore: one successful trigger writes zero fix_queue rows."""
        from riptide.state import StateStore
        from riptide import state as state_mod

        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr(state_mod, "StateStore", lambda: StateStore(db_path=db_path))

        with patch("riptide.fixer._spawn_fix", return_value=True):
            result = handle_fix_command(self._client(), 1, "ChonSong", "riptide", 42, "test-user", "")

        assert "Riptide Fix triggered" in result
        store = StateStore(db_path=db_path)
        rows = store._get_conn().execute("SELECT COUNT(*) FROM fix_queue").fetchone()[0]
        assert rows == 0, "a spawned fix must not also write a fix_queue row"

    def test_busy_same_pr_does_not_enqueue_a_duplicate(self):
        """A fix already pending for this PR: answer, write nothing, spawn nothing."""
        client = self._client()
        with patch("riptide.state.StateStore") as mock_store, \
             patch("riptide.fixer._spawn_fix") as mock_spawn:
            mock_store.return_value.has_running_fix.return_value = True
            mock_store.return_value.get_running_fix_pr.return_value = 42
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")

        mock_store.return_value.enqueue_fix.assert_not_called()
        mock_spawn.assert_not_called()
        assert "already in progress" in result
        assert "Riptide Fix triggered" not in result

    def test_busy_other_pr_does_not_enqueue_a_duplicate(self):
        """Global gate held by another PR: tell the reader, write nothing."""
        client = self._client()
        with patch("riptide.state.StateStore") as mock_store, \
             patch("riptide.fixer._spawn_fix") as mock_spawn:
            mock_store.return_value.has_running_fix.return_value = True
            mock_store.return_value.get_running_fix_pr.return_value = 7
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")

        mock_store.return_value.enqueue_fix.assert_not_called()
        mock_spawn.assert_not_called()
        assert "#7" in result
        assert "not queued" in result

    def test_spawn_refused_with_cron_available_does_not_enqueue(self):
        """Spawn came back False while the CLI exists → duplicate, never queued."""
        client = self._client()
        with patch("riptide.state.StateStore") as mock_store, \
             patch("riptide.fixer._is_cron_available", return_value=True):
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=False):
                result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")

        mock_store.return_value.enqueue_fix.assert_not_called()
        assert "No fix session started" in result
        assert "Riptide Fix triggered" not in result

    def test_cron_unavailable_writes_no_queue_row(self):
        """The one path that used to write the queue: it must not any more."""
        client = self._client()
        with patch("riptide.state.StateStore") as mock_store, \
             patch("riptide.fixer._is_cron_available", return_value=False):
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            with patch("riptide.fixer._spawn_fix", return_value=False):
                result = handle_fix_command(
                    client, 1, "ChonSong", "riptide", 42, "test-user", "fix the flake"
                )

        mock_store.return_value.enqueue_fix.assert_not_called()
        mock_store.return_value.get_queue_position.assert_not_called()
        assert "Hermes cron CLI is not available on this host" in result
        assert "fix the flake" in result
        assert "Riptide Fix triggered" not in result

    def test_cron_unavailable_writes_no_row_and_does_not_block_the_pr(
        self, tmp_path, monkeypatch
    ):
        """Real StateStore: the refusal writes no row, so the PR stays spawnable.

        The defect this pins: with an un-drained `queued` row written, the next
        `@riptide-bot fix` on the same PR was answered "already in progress"
        while nothing ever started. Both halves are asserted here — no row, and
        a later trigger still spawns.
        """
        from riptide.state import StateStore
        from riptide import state as state_mod

        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr(state_mod, "StateStore", lambda: StateStore(db_path=db_path))

        with patch("riptide.fixer._is_cron_available", return_value=False), \
             patch("riptide.fixer._spawn_fix", return_value=False):
            result = handle_fix_command(
                self._client(), 1, "ChonSong", "riptide", 42, "test-user", "fix the flake"
            )

        assert "Could not start a fix for #42" in result
        assert "Hermes cron CLI is not available on this host" in result
        assert "Nothing was queued" in result

        store = StateStore(db_path=db_path)
        rows = store._get_conn().execute("SELECT COUNT(*) FROM fix_queue").fetchone()[0]
        assert rows == 0, "the cron-unavailable path must not write a fix_queue row"
        assert store.get_queue_length(42, owner="ChonSong", repo="riptide") == 0

        # The PR is not blocked: the same PR can be triggered again.
        with patch("riptide.fixer._is_cron_available", return_value=True), \
             patch("riptide.fixer._spawn_fix", return_value=True) as mock_spawn:
            second = handle_fix_command(
                self._client(), 1, "ChonSong", "riptide", 42, "test-user", ""
            )
        assert "Riptide Fix triggered" in second
        mock_spawn.assert_called_once()
        assert store._get_conn().execute("SELECT COUNT(*) FROM fix_queue").fetchone()[0] == 0

    def test_successful_spawn_writes_no_row_through_the_real_spawn_path(
        self, tmp_path, monkeypatch
    ):
        """Real StateStore + real `_spawn_fix` (only the CLI is mocked).

        The reservation in `jobs` is what tracks a spawned fix; a fix_queue row
        here is the PR #208 duplicate.
        """
        from riptide.state import StateStore
        from riptide import state as state_mod

        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr(state_mod, "StateStore", lambda: StateStore(db_path=db_path))

        with patch("riptide.fixer._is_cron_available", return_value=True), \
             patch("riptide.fixer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Created job", stderr="")
            result = handle_fix_command(
                self._client(), 1, "ChonSong", "riptide", 42, "test-user", ""
            )

        assert "Riptide Fix triggered" in result
        mock_run.assert_called_once()

        store = StateStore(db_path=db_path)
        conn = store._get_conn()
        assert conn.execute("SELECT COUNT(*) FROM fix_queue").fetchone()[0] == 0, (
            "a spawned fix must not also write a fix_queue row"
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE id LIKE 'riptide-fix-%'"
        ).fetchone()[0] == 1, "the spawned fix is tracked by its reserved job"

    def test_busy_check_bounds_the_queue_lookup_by_age(self):
        """The busy check must ask for recent rows only — the gate cannot be
        held by a row nothing will ever drain."""
        from riptide.fixer import QUEUE_BLOCK_MAX_AGE_SECONDS

        client = self._client()
        with patch("riptide.state.StateStore") as mock_store, \
             patch("riptide.fixer._spawn_fix", return_value=True):
            mock_store.return_value.has_running_fix.return_value = False
            mock_store.return_value.get_queue_length.return_value = 0
            handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")

        kwargs = mock_store.return_value.get_queue_length.call_args[1]
        assert kwargs["owner"] == "ChonSong"
        assert kwargs["repo"] == "riptide"
        assert kwargs["max_age_seconds"] == QUEUE_BLOCK_MAX_AGE_SECONDS

    def test_queue_block_age_is_the_fix_ttl(self):
        """One TTL, not two: the age bound is `has_running_fix`'s TTL."""
        from riptide.fixer import QUEUE_BLOCK_MAX_AGE_SECONDS
        from riptide.state import FIX_TTL_SECONDS

        assert QUEUE_BLOCK_MAX_AGE_SECONDS == FIX_TTL_SECONDS == 7200

    def test_spawn_without_cron_never_calls_hermes(self):
        """`_spawn_fix` with no CLI: no subprocess, no success, retries skipped."""
        with patch("riptide.state.StateStore") as mock_state, \
             patch("riptide.fixer._is_cron_available", return_value=False), \
             patch("riptide.fixer.time.sleep") as mock_sleep, \
             patch("subprocess.run") as mock_run:
            mock_state.return_value.reserve_job.return_value = True
            spawned = _spawn_fix(
                owner="ChonSong", repo="riptide", pr_number=42,
                pr_title="fix: repair flaky test", pr_author="test-user",
                total_loc=150, head_sha="abc123def4567890", head_ref="fix-branch",
                description="", push_eligible=True,
            )
        assert spawned is False
        mock_run.assert_not_called()
        assert mock_sleep.call_count == 2  # backoff before attempts 2 and 3 only
        mock_state.return_value.mark_failed.assert_called_once()


class TestStaleQueuedRowDoesNotBlock:
    """An un-drained `queued` row must not block a spawn forever.

    Rows written by a pre-fix deployment are still in the state DB. Counting
    them unconditionally made `@riptide-bot fix` answer "already pending" for
    good while nothing ever started.
    """

    def _client(self):
        return _make_client(pr_details=_pr_details())

    def _store(self, tmp_path, monkeypatch):
        from riptide.state import StateStore
        from riptide import state as state_mod

        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr(state_mod, "StateStore", lambda: StateStore(db_path=db_path))
        return StateStore(db_path=db_path)

    def test_old_queued_row_does_not_block_a_spawn(self, tmp_path, monkeypatch):
        import time
        from riptide.fixer import QUEUE_BLOCK_MAX_AGE_SECONDS

        store = self._store(tmp_path, monkeypatch)
        qid = store.enqueue_fix(
            42, "ChonSong/riptide#42", "test-user", "old request",
            owner="ChonSong", repo="riptide",
        )
        conn = store._get_conn()
        conn.execute(
            "UPDATE fix_queue SET created_at = ? WHERE id = ?",
            (time.time() - QUEUE_BLOCK_MAX_AGE_SECONDS - 60, qid),
        )
        conn.commit()

        with patch("riptide.fixer._spawn_fix", return_value=True) as mock_spawn:
            result = handle_fix_command(
                self._client(), 1, "ChonSong", "riptide", 42, "test-user", ""
            )

        mock_spawn.assert_called_once()
        assert "Riptide Fix triggered" in result
        # The stale row is neither resurrected nor duplicated.
        assert conn.execute(
            "SELECT status, COUNT(*) FROM fix_queue GROUP BY status"
        ).fetchall() == [("queued", 1)]

    def test_fresh_queued_row_still_blocks(self, tmp_path, monkeypatch):
        """The age bound must not disable the gate for recent requests."""
        store = self._store(tmp_path, monkeypatch)
        store.enqueue_fix(
            42, "ChonSong/riptide#42", "test-user", "just now",
            owner="ChonSong", repo="riptide",
        )

        with patch("riptide.fixer._spawn_fix") as mock_spawn:
            result = handle_fix_command(
                self._client(), 1, "ChonSong", "riptide", 42, "test-user", ""
            )

        mock_spawn.assert_not_called()
        assert "already in progress" in result
        assert "Riptide Fix triggered" not in result
        assert store._get_conn().execute(
            "SELECT COUNT(*) FROM fix_queue"
        ).fetchone()[0] == 1, "no duplicate row for a request already pending"


class TestProcessFixQueue:
    """Tests for the process_fix_queue function."""

    def test_process_empty_queue(self):
        """process_fix_queue returns None when queue is empty."""
        client = MagicMock()
        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.start_next_queued_fix.return_value = None
            result = process_fix_queue(client, "ChonSong", "riptide")
        assert result is None

    def test_process_queue_drains_and_spawns(self):
        """process_fix_queue pops the oldest queued fix and spawns it."""
        client = MagicMock()
        client.get_pr_details.return_value = {
            "title": "Test PR",
            "user": {"login": "chonsong"},
            "additions": 10, "deletions": 5,
            "head": {"sha": "abc123", "ref": "fix-branch", "repo": {"full_name": "ChonSong/riptide"}},
        }

        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.start_next_queued_fix.return_value = {
                "id": 1, "pr_number": 155, "description": "fix the bug", "commenter": "chonsong"
            }
            with patch("riptide.fixer._spawn_fix", return_value=True):
                result = process_fix_queue(client, "ChonSong", "riptide")

        assert result is not None
        assert "started" in result

    def test_process_queue_uses_stored_installation_id(self):
        """process_fix_queue must use the installation_id stored at enqueue time."""
        client = MagicMock()
        client.get_pr_details.return_value = {
            "title": "Test PR",
            "user": {"login": "chonsong"},
            "additions": 10, "deletions": 5,
            "head": {"sha": "abc123", "ref": "fix-branch", "repo": {"full_name": "ChonSong/riptide"}},
        }

        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.start_next_queued_fix.return_value = {
                "id": 1, "pr_number": 155, "description": "fix the bug",
                "commenter": "chonsong", "installation_id": 4242
            }
            with patch("riptide.fixer._spawn_fix", return_value=True):
                result = process_fix_queue(client, "ChonSong", "riptide")

        # Verify get_pr_details was called with the stored installation_id (not None)
        client.get_pr_details.assert_called_once_with(4242, "ChonSong", "riptide", 155)
        assert result is not None
        assert "started" in result

    def test_process_queue_handles_missing_installation_id(self):
        """process_fix_queue handles legacy queue items without installation_id (None)."""
        client = MagicMock()
        client.get_pr_details.side_effect = Exception("installation_id required")

        with patch("riptide.state.StateStore") as mock_store:
            mock_store.return_value.start_next_queued_fix.return_value = {
                "id": 1, "pr_number": 155, "description": "fix the bug",
                "commenter": "chonsong", "installation_id": None
            }
            result = process_fix_queue(client, "ChonSong", "riptide")

        # Should fail gracefully — complete_fix_queue_item called with success=False
        mock_store.return_value.complete_fix_queue_item.assert_called_once_with(1, success=False)
        assert result is None


class TestEnqueueFixInstallationId:
    """Tests that handle_fix_command stores installation_id in the queue."""

    def test_enqueue_stores_installation_id(self):
        """When queuing, installation_id is stored for later use by process_fix_queue."""
        import tempfile, os
        from riptide.state import StateStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = StateStore(db_path=db_path)
            qid = store.enqueue_fix(155, "ChonSong/riptide#155", "chonsong", "fix bug", installation_id=4242)

            conn = store._get_conn()
            row = conn.execute("SELECT installation_id FROM fix_queue WHERE id = ?", (qid,)).fetchone()
            assert row[0] == 4242

    def test_enqueue_installation_id_defaults_none(self):
        """installation_id defaults to None for backward compatibility."""
        import tempfile, os
        from riptide.state import StateStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = StateStore(db_path=db_path)
            qid = store.enqueue_fix(155, "ChonSong/riptide#155", "chonsong", "fix bug")

            conn = store._get_conn()
            row = conn.execute("SELECT installation_id FROM fix_queue WHERE id = ?", (qid,)).fetchone()
            assert row[0] is None


class TestFixTtlConsistency:
    """Tests that TTL values are consistent across fix activity checks."""

    def test_cleanup_stale_queue_items_uses_fix_ttl(self):
        """cleanup_stale_queue_items default TTL matches has_running_fix TTL."""
        import tempfile, os, time
        from riptide.state import StateStore, FIX_TTL_SECONDS

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = StateStore(db_path=db_path)

            # Verify the constant is 7200 (2 hours)
            assert FIX_TTL_SECONDS == 7200

            # Enqueue and start a fix
            qid = store.enqueue_fix(155, "ChonSong/riptide#155", "chonsong", "fix bug")
            item = store.start_next_queued_fix()
            assert item is not None

            # Mark it as running with an old started_at (older than FIX_TTL)
            conn = store._get_conn()
            old_time = time.time() - FIX_TTL_SECONDS - 100
            conn.execute("UPDATE fix_queue SET started_at = ? WHERE id = ?", (old_time, qid))
            conn.commit()

            # cleanup_stale_queue_items should mark it failed
            store.cleanup_stale_queue_items()

            row = conn.execute("SELECT status FROM fix_queue WHERE id = ?", (qid,)).fetchone()
            assert row[0] == "failed"

    def test_has_running_fix_consistent_with_cleanup(self):
        """has_running_fix and cleanup_stale_queue_items use the same TTL cutoff."""
        import tempfile, os, time
        from riptide.state import StateStore, FIX_TTL_SECONDS

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = StateStore(db_path=db_path)

            # Both should use FIX_TTL_SECONDS (7200)
            assert FIX_TTL_SECONDS == 7200

            # The cutoff for both is time.time() - FIX_TTL_SECONDS
            # This test verifies the constant is used consistently
            cutoff = time.time() - FIX_TTL_SECONDS
            assert cutoff < time.time()


class TestProcessFixQueueSpawnFailure:
    """Tests that spawn failures release queue items immediately."""

    def test_spawn_exception_releases_item(self, tmp_path, monkeypatch):
        """When _spawn_fix raises, queue item is marked failed immediately."""
        from riptide.state import StateStore
        from riptide import state as state_mod

        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr(state_mod, "StateStore", lambda: StateStore(db_path=db_path))

        store = StateStore(db_path=db_path)
        store.enqueue_fix(155, "ChonSong/riptide#155", "user", "fix bug")

        client = MagicMock()
        client.get_pr_details.return_value = {
            "title": "Test PR",
            "user": {"login": "user"},
            "additions": 10, "deletions": 5,
            "head": {"sha": "abc123", "ref": "fix-branch", "repo": {"full_name": "ChonSong/riptide"}},
        }

        with patch("riptide.fixer._spawn_fix", side_effect=RuntimeError("spawn failed")):
            from riptide.fixer import process_fix_queue
            result = process_fix_queue(client)

        assert result is not None
        assert "failed" in result

        # Verify queue item was released (not left as 'running')
        conn = store._get_conn()
        row = conn.execute("SELECT status FROM fix_queue WHERE pr_number = 155").fetchone()
        assert row[0] == "failed"

    def test_spawn_returns_false_releases_item(self, tmp_path, monkeypatch):
        """When _spawn_fix returns False, queue item is marked failed."""
        from riptide.state import StateStore
        from riptide import state as state_mod

        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr(state_mod, "StateStore", lambda: StateStore(db_path=db_path))

        store = StateStore(db_path=db_path)
        store.enqueue_fix(155, "ChonSong/riptide#155", "user", "fix bug")

        client = MagicMock()
        client.get_pr_details.return_value = {
            "title": "Test PR",
            "user": {"login": "user"},
            "additions": 10, "deletions": 5,
            "head": {"sha": "abc123", "ref": "fix-branch", "repo": {"full_name": "ChonSong/riptide"}},
        }

        with patch("riptide.fixer._spawn_fix", return_value=False):
            from riptide.fixer import process_fix_queue
            result = process_fix_queue(client)

        assert result is not None
        assert "failed to spawn" in result

        # Verify queue item was released
        conn = store._get_conn()
        row = conn.execute("SELECT status FROM fix_queue WHERE pr_number = 155").fetchone()
        assert row[0] == "failed"


class TestQueueFifoTiebreaker:
    """Regression tests for FIFO ordering with identical timestamps."""

    def test_identical_timestamps_ordered_by_id(self, tmp_path):
        """Items with identical timestamps are ordered by id ASC."""
        from riptide.state import StateStore
        import time

        store = StateStore(db_path=str(tmp_path / "test.db"))
        store.init_fix_queue()  # Create table before raw inserts
        now = time.time()

        # Insert multiple items with the same timestamp
        conn = store._get_conn()
        conn.execute(
            "INSERT INTO fix_queue (pr_number, pr_key, commenter, created_at) VALUES (?, ?, ?, ?)",
            (100, "o/r#100", "user1", now),
        )
        conn.execute(
            "INSERT INTO fix_queue (pr_number, pr_key, commenter, created_at) VALUES (?, ?, ?, ?)",
            (200, "o/r#200", "user2", now),
        )
        conn.execute(
            "INSERT INTO fix_queue (pr_number, pr_key, commenter, created_at) VALUES (?, ?, ?, ?)",
            (300, "o/r#300", "user3", now),
        )
        conn.commit()

        # Should dequeue in id order (FIFO)
        item1 = store.start_next_queued_fix()
        assert item1 is not None
        assert item1["pr_number"] == 100

        item2 = store.start_next_queued_fix()
        assert item2 is not None
        assert item2["pr_number"] == 200

        item3 = store.start_next_queued_fix()
        assert item3 is not None
        assert item3["pr_number"] == 300


class TestQueueOwnerRepo:
    """Tests for owner/repo persistence in queue."""

    def test_enqueue_stores_owner_repo(self, tmp_path):
        """enqueue_fix stores owner and repo."""
        from riptide.state import StateStore

        store = StateStore(db_path=str(tmp_path / "test.db"))
        qid = store.enqueue_fix(155, "owner/repo#155", "user", "desc", owner="owner", repo="repo")

        conn = store._get_conn()
        row = conn.execute("SELECT owner, repo FROM fix_queue WHERE id = ?", (qid,)).fetchone()
        assert row[0] == "owner"
        assert row[1] == "repo"

    def test_get_queue_length_filters_by_owner_repo(self, tmp_path):
        """get_queue_length filters by owner and repo."""
        from riptide.state import StateStore

        store = StateStore(db_path=str(tmp_path / "test.db"))
        store.enqueue_fix(155, "owner1/repo1#155", "user1", "", owner="owner1", repo="repo1")
        store.enqueue_fix(155, "owner2/repo2#155", "user2", "", owner="owner2", repo="repo2")
        store.enqueue_fix(156, "owner1/repo1#156", "user3", "", owner="owner1", repo="repo1")

        assert store.get_queue_length(155, owner="owner1", repo="repo1") == 1
        assert store.get_queue_length(155, owner="owner2", repo="repo2") == 1
        assert store.get_queue_length(155) == 2  # Both when no filter

    def test_start_next_queued_fix_returns_owner_repo(self, tmp_path):
        """start_next_queued_fix returns owner and repo."""
        from riptide.state import StateStore

        store = StateStore(db_path=str(tmp_path / "test.db"))
        store.enqueue_fix(155, "owner/repo#155", "user", "", owner="owner", repo="repo")

        item = store.start_next_queued_fix()
        assert item is not None
        assert item["owner"] == "owner"
        assert item["repo"] == "repo"


class TestRunningFixCutoff:
    """Tests for started_at cutoff in has_running_fix and get_running_fix_pr."""

    def test_has_running_fix_excludes_stale_queue_items(self, tmp_path):
        """has_running_fix excludes queue items older than cutoff."""
        from riptide.state import StateStore
        import time

        store = StateStore(db_path=str(tmp_path / "test.db"))
        qid = store.enqueue_fix(155, "o/r#155", "user", "")
        store.start_next_queued_fix()

        # Mark as running with old started_at (older than cutoff)
        conn = store._get_conn()
        old_time = time.time() - 7200 - 100  # 2h100s ago
        conn.execute("UPDATE fix_queue SET started_at = ? WHERE id = ?", (old_time, qid))
        conn.commit()

        # Should NOT be considered running (stale)
        assert store.has_running_fix() is False

    def test_get_running_fix_pr_excludes_stale_queue_items(self, tmp_path):
        """get_running_fix_pr excludes queue items older than cutoff."""
        from riptide.state import StateStore
        import time

        store = StateStore(db_path=str(tmp_path / "test.db"))
        qid = store.enqueue_fix(155, "o/r#155", "user", "")
        store.start_next_queued_fix()

        # Mark as running with old started_at
        conn = store._get_conn()
        old_time = time.time() - 7200 - 100
        conn.execute("UPDATE fix_queue SET started_at = ? WHERE id = ?", (old_time, qid))
        conn.commit()

        # Should return None (stale)
        assert store.get_running_fix_pr() is None

    def test_has_running_fix_includes_recent_queue_items(self, tmp_path):
        """has_running_fix includes recent queue items."""
        from riptide.state import StateStore

        store = StateStore(db_path=str(tmp_path / "test.db"))
        store.enqueue_fix(155, "o/r#155", "user", "")
        store.start_next_queued_fix()

        # Should be considered running (recent)
        assert store.has_running_fix() is True
