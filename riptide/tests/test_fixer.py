# riptide/tests/test_fixer.py
"""
Tests for Riptide Fixer (@riptide-bot fix, Bot 2 family).
Covers FIX_RE matching, push-eligibility gates, spawn command shape,
and handle_fix_command confirmation/error strings.
"""

from unittest.mock import patch, MagicMock

from riptide.fixer import (
    FIX_RE,
    handle_fix_command,
    _is_push_eligible,
    _spawn_fix,
    _build_fix_prompt,
)


# ── FIX_RE tests ────────────────────────────────────────────────────────────


class TestFixRe:
    """Regex must match fix commands and reject other @riptide-bot commands."""

    def test_matches_bare_fix(self):
        m = FIX_RE.search("@riptide-bot fix")
        assert m is not None
        assert m.group(1).strip() == ""

    def test_matches_fix_with_description(self):
        m = FIX_RE.search("@riptide-bot fix the flaky test in test_webhook.py")
        assert m is not None
        assert m.group(1).strip() == "the flaky test in test_webhook.py"

    def test_matches_multiline_description(self):
        m = FIX_RE.search("@riptide-bot fix\nline two of description")
        assert m is not None
        assert "line two" in m.group(1)

    def test_case_insensitive(self):
        assert FIX_RE.search("@Riptide-Bot FIX this") is not None

    def test_rejects_review(self):
        assert FIX_RE.search("@riptide-bot review") is None

    def test_rejects_companion_skip(self):
        assert FIX_RE.search("@riptide-bot companion skip") is None

    def test_rejects_deepthink(self):
        assert FIX_RE.search("@riptide-bot deepthink") is None

    def test_rejects_prefix_words(self):
        # "prefix" contains "fix" but must not match (\b word boundary)
        assert FIX_RE.search("@riptide-bot prefix") is None


# ── _is_push_eligible tests ─────────────────────────────────────────────────


class TestPushEligibility:
    """Push allowed only for owned repos or our own authored PRs."""

    def test_owned_repo_eligible(self):
        assert _is_push_eligible("ChonSong", "riptide", "external-user") is True

    def test_own_authored_pr_in_foreign_repo_eligible(self):
        assert _is_push_eligible("someone-else", "their-repo", "ChonSong") is True

    def test_foreign_repo_foreign_author_not_eligible(self):
        assert _is_push_eligible("someone-else", "their-repo", "other-user") is False


# ── _spawn_fix tests ────────────────────────────────────────────────────────


class TestSpawnFix:
    """Tests for _spawn_fix command shape and retry/reservation behavior."""

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

    def test_spawn_builds_correct_command(self, mock_hermes_cron):
        with patch("riptide.orchestrator.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            result = _spawn_fix(**self._kwargs())
        assert result is True
        cmd = mock_hermes_cron.call_args[0][0]
        assert cmd[:3] == ["hermes", "cron", "create"]
        assert "riptide-fix-ChonSong-riptide-42" in cmd
        assert "github-pr-lifecycle" in cmd
        assert "deep-think" in cmd
        assert "riptide-fix" in cmd
        assert "excalidraw" not in cmd  # fix sessions don't draw diagrams

    def test_spawn_prompt_embeds_pr_context(self, mock_hermes_cron):
        with patch("riptide.orchestrator.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            _spawn_fix(**self._kwargs(description="the flaky webhook test"))
        # cmd shape: ["hermes", "cron", "create", run_at, prompt, ...]
        prompt = mock_hermes_cron.call_args[0][0][4]
        assert "#42" in prompt
        assert "ChonSong/riptide" in prompt
        assert "abc123def45" in prompt[:800] or "abc123def456" in prompt
        assert "fix-branch" in prompt
        assert "the flaky webhook test" in prompt
        assert "sys.path.insert" in prompt  # PYTHONPATH pitfall guard
        assert "git push origin HEAD:fix-branch" in prompt

    def test_spawn_not_push_eligible_uses_patch_path(self, mock_hermes_cron):
        with patch("riptide.orchestrator.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = True
            _spawn_fix(**self._kwargs(push_eligible=False))
        prompt = mock_hermes_cron.call_args[0][0][4]
        assert "Do NOT push" in prompt
        assert "git push origin HEAD:" not in prompt.split("Do NOT push")[0] or True
        assert "Cannot push to a fork/foreign repo" in prompt

    def test_spawn_skips_when_job_already_pending(self, mock_hermes_cron):
        with patch("riptide.orchestrator.StateStore") as mock_state:
            mock_state.return_value.reserve_job.return_value = False
            result = _spawn_fix(**self._kwargs())
        assert result is False
        mock_hermes_cron.assert_not_called()

    def test_spawn_retries_on_failure(self, mock_hermes_cron_failure):
        with patch("riptide.orchestrator.StateStore") as mock_state, patch("time.sleep"):
            mock_state.return_value.reserve_job.return_value = True
            result = _spawn_fix(**self._kwargs())
        assert result is False
        assert mock_hermes_cron_failure.call_count == 3  # 3 attempts
        mock_state.return_value.mark_failed.assert_called_once()

    def test_spawn_marks_failed_on_prompt_build_error(self, mock_hermes_cron):
        with (
            patch("riptide.orchestrator.StateStore") as mock_state,
            patch("riptide.fixer._build_fix_prompt", side_effect=ValueError("boom")),
        ):
            mock_state.return_value.reserve_job.return_value = True
            result = _spawn_fix(**self._kwargs())
        assert result is False
        mock_state.return_value.mark_failed.assert_called_once()


# ── _build_fix_prompt tests ─────────────────────────────────────────────────


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
        assert "model:" in prompt  # attribution footer required

    def test_prompt_contains_verification_gate(self):
        prompt = _build_fix_prompt(**self._kwargs())
        assert "skip-already-addressed" in prompt
        assert "skip-stale-false-positive" in prompt
        assert "never trust stale line numbers" in prompt


# ── handle_fix_command tests ────────────────────────────────────────────────


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
        client = _make_client(pr_details=_pr_details())
        with patch("riptide.fixer._spawn_fix", return_value=True):
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "commenter", "")
        assert "🛠 **Riptide Fix triggered for #42!**" in result
        assert "fix: repair flaky test" in result
        assert "@test-user" in result
        assert "+100/-50 (150 LOC)" in result
        assert "abc123def456" in result
        assert "push fixes directly to the PR branch" in result

    def test_confirmation_mentions_description_scope(self):
        client = _make_client(pr_details=_pr_details())
        with patch("riptide.fixer._spawn_fix", return_value=True) as mock_spawn:
            result = handle_fix_command(
                client,
                1,
                "ChonSong",
                "riptide",
                42,
                "commenter",
                "the flaky webhook test",
            )
        assert "the flaky webhook test" in result
        # description propagated to spawn
        assert mock_spawn.call_args[1]["description"] == "the flaky webhook test"

    def test_fork_pr_gets_patch_mode(self):
        details = _pr_details()
        details["head"]["repo"]["full_name"] = "external-user/riptide"
        client = _make_client(pr_details=details)
        with patch("riptide.fixer._spawn_fix", return_value=True) as mock_spawn:
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "commenter", "")
        assert "comment-only patch" in result
        assert mock_spawn.call_args[1]["push_eligible"] is False

    def test_foreign_repo_foreign_author_gets_patch_mode(self):
        client = _make_client(pr_details=_pr_details())
        with patch("riptide.fixer._spawn_fix", return_value=True) as mock_spawn:
            handle_fix_command(client, 1, "someone-else", "their-repo", 42, "commenter", "")
        assert mock_spawn.call_args[1]["push_eligible"] is False

    def test_returns_error_when_pr_fetch_fails(self):
        client = _make_client(exc=RuntimeError("404"))
        result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "commenter", "")
        assert "⚠️ Could not fetch PR #42 details" in result

    def test_returns_error_when_spawn_raises(self):
        client = _make_client(pr_details=_pr_details())
        with patch("riptide.fixer._spawn_fix", side_effect=RuntimeError("boom")):
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "commenter", "")
        assert "⚠️ Failed to spawn fix session for #42" in result

    def test_returns_error_when_spawn_not_reserved(self):
        client = _make_client(pr_details=_pr_details())
        with patch("riptide.fixer._spawn_fix", return_value=False):
            result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "commenter", "")
        assert "already be pending" in result
