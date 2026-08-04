"""Tests for riptide/fixer.py — @riptide-bot fix command."""
from unittest.mock import patch, MagicMock

import pytest

from riptide.fixer import (
    FIX_RE,
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
        with patch("riptide.fixer._spawn_fix", return_value=True):
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "alice")
            assert result is not None
            assert "Fix triggered" in result

    def test_owner_can_trigger(self):
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details(author="alice")
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
        with patch("riptide.fixer._spawn_fix", return_value=True):
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "ChonSong")
            assert result is not None
            assert "push fixes directly" in result

    def test_fork_repo_comment_only(self):
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details("other/riptide")
        with patch("riptide.fixer._spawn_fix", return_value=True):
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "ChonSong")
            assert result is not None
            assert "comment-only patch" in result

    def test_missing_head_repo_treated_as_fork(self):
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details(None)
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
        with patch("riptide.orchestrator.StateStore") as mock_state, \
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
        with patch("riptide.orchestrator.StateStore") as mock_state, \
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
        with patch("riptide.orchestrator.StateStore") as mock_state, \
             patch("subprocess.run") as mock_run:
            mock_state.return_value.reserve_job.return_value = True
            mock_run.return_value = MagicMock(returncode=0)
            _spawn_fix(**self._kwargs(push_eligible=False))
        prompt = mock_run.call_args[0][0][4]
        assert "Do NOT push" in prompt

    def test_spawn_skips_when_job_already_pending(self):
        with patch("riptide.orchestrator.StateStore") as mock_state, \
             patch("subprocess.run") as mock_run:
            mock_state.return_value.reserve_job.return_value = False
            result = _spawn_fix(**self._kwargs())
        assert result is False
        mock_run.assert_not_called()

    def test_spawn_retries_on_failure(self):
        with patch("riptide.orchestrator.StateStore") as mock_state, \
             patch("subprocess.run") as mock_run, \
             patch("time.sleep"):
            mock_state.return_value.reserve_job.return_value = True
            mock_run.return_value = MagicMock(returncode=1)
            result = _spawn_fix(**self._kwargs())
        assert result is False
        assert mock_run.call_count == 3

    def test_spawn_marks_failed_on_prompt_build_error(self):
        with patch("riptide.orchestrator.StateStore") as mock_state, \
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
        with patch("riptide.fixer._spawn_fix", return_value=True):
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")
        assert "🛠 **Riptide Fix triggered for #42!**" in result
        assert "fix: repair flaky test" in result
        assert "@test-user" in result
        assert "+100/-50 (150 LOC)" in result

    def test_confirmation_mentions_description_scope(self):
        # Repo owner triggering fix
        client = _make_client(pr_details=_pr_details())
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
        with patch("riptide.fixer._spawn_fix", return_value=True) as mock_spawn:
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
        with patch("riptide.fixer._spawn_fix", side_effect=RuntimeError("boom")):
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")
        assert "⚠️ Failed to spawn fix session for #42" in result

    def test_returns_error_when_spawn_not_reserved(self):
        client = _make_client(pr_details=_pr_details())
        with patch("riptide.fixer._spawn_fix", return_value=False):
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "test-user", "")
        assert "already be pending" in result
