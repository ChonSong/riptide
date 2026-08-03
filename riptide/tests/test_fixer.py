"""Tests for riptide/fixer.py — @riptide-bot fix command."""
from unittest.mock import patch, MagicMock

import pytest

from riptide.fixer import (
    FIX_RE,
    handle_fix_command,
    _is_push_eligible,
    _is_cron_available,
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

    def test_does_not_match_review(self):
        assert FIX_RE.search("@riptide-bot review") is None

    def test_does_not_match_companion_skip(self):
        assert FIX_RE.search("@riptide-bot companion skip") is None


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

    def test_stranger_blocked(self):
        client = MagicMock()
        client.get_pr_details.return_value = self._pr_details(author="alice")
        result = handle_fix_command(client, 1, "ChonSong", "riptide", 42, "eve")
        assert result is not None
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


# ── Prompt builder ───────────────────────────────────────────────────────────


class TestBuildFixPrompt:
    def test_prompt_contains_verification_gate(self):
        prompt = _build_fix_prompt("ChonSong", "riptide", 42, "title", "author", 15,
                                   "abc123", "feat/x", "", True)
        assert "Verification gate" in prompt
        assert "NEVER edit" in prompt
        assert "NO force-push" in prompt

    def test_prompt_with_description(self):
        prompt = _build_fix_prompt("ChonSong", "riptide", 42, "title", "author", 15,
                                   "abc123", "feat/x", "fix the bug", True)
        assert "fix the bug" in prompt

    def test_prompt_push_eligible(self):
        prompt = _build_fix_prompt("ChonSong", "riptide", 42, "title", "author", 15,
                                   "abc123", "feat/x", "", True)
        assert "git push" in prompt
        assert "comment-only" not in prompt

    def test_prompt_not_push_eligible(self):
        prompt = _build_fix_prompt("other", "repo", 42, "title", "author", 15,
                                   "abc123", "feat/x", "", False)
        assert "Do NOT push" in prompt
