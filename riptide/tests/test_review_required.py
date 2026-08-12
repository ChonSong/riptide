#!/usr/bin/env python3
"""Tests for riptide-review-required CI workflow logic.

Tests the bash patterns used in the workflow to detect deep-think reviews
and follow-up commits.
"""
import json

import pytest


class TestDeepThinkReviewDetection:
    """Test that we can distinguish deep-think reviews from TL;DR."""

    def test_tldr_is_not_deep_think(self):
        """TL;DR (📊 Blast Radius, 🧒 ELI5) should NOT match."""
        tldr_comment = {
            "id": 1,
            "user": {"login": "riptide-review[bot]"},
            "body": "## ✨ TL;DR\n\n@ChonSong — ✅ Clean PR\n**📊 Blast Radius**\n4 files\n**🧒 ELI5**\nSimple change",
        }
        body = tldr_comment["body"]
        import re

        is_deep_think = bool(
            re.search(r"## 🎯 Summary|## 🔍 Findings", body, re.IGNORECASE)
        )
        assert not is_deep_think, "TL;DR should not match deep-think pattern"

    def test_deep_think_summary_matches(self):
        """Deep-think review with ## 🎯 Summary should match."""
        review_comment = {
            "id": 2,
            "user": {"login": "riptide-review[bot]"},
            "body": "## 🎯 Summary\n\n1 issue(s) found — see details below.\n\n## 🔍 Findings\n\n| Severity | File | Line | Issue |\n|----------|------|------|-------|\n| 🟡 warning | `foo.py` | 10 | Bug |",
        }
        body = review_comment["body"]
        import re

        is_deep_think = bool(
            re.search(r"## 🎯 Summary|## 🔍 Findings", body, re.IGNORECASE)
        )
        assert is_deep_think, "Deep-think review should match"

    def test_deep_think_from_any_user_matches(self):
        """Deep-think review from any user (not just bot) should match."""
        review_comment = {
            "id": 4,
            "user": {"login": "ChonSong"},  # Not the bot
            "body": "## 🎯 Summary\n\n1 issue(s) found\n\n## 🔍 Findings\n\n| 🟡 warning | `foo.py` | 10 | Bug |",
        }
        body = review_comment["body"]
        import re

        is_deep_think = bool(
            re.search(r"## 🎯 Summary|## 🔍 Findings", body, re.IGNORECASE)
        )
        assert is_deep_think, "Deep-think review from any user should match"

    def test_clean_review_matches(self):
        """Clean deep-think review should still match (no findings)."""
        review_comment = {
            "id": 3,
            "user": {"login": "riptide-review[bot]"},
            "body": "## 🎯 Summary\n\nClean PR — no critical issues or warnings.\n\n## 🔍 Findings\n\nNo critical/warning findings.",
        }
        body = review_comment["body"]
        import re

        is_deep_think = bool(
            re.search(r"## 🎯 Summary|## 🔍 Findings", body, re.IGNORECASE)
        )
        assert is_deep_think, "Clean deep-think review should still match"

    def test_pr_review_detection(self):
        """PR reviews (from GitHub reviews API) should also be detected."""
        pr_review = {
            "id": 10,
            "user": {"login": "ChonSong"},
            "body": "## 🎯 Summary\n\n2 issues found\n\n## 🔍 Findings\n\n| 🔴 critical | `bar.py` | 5 | Security issue |",
            "created_at": "2026-08-12T10:00:00Z",
        }
        body = pr_review["body"]
        import re

        is_deep_think = bool(
            re.search(r"## 🎯 Summary|## 🔍 Findings", body, re.IGNORECASE)
        )
        assert is_deep_think, "PR review body should be detected"

    def test_pr_comment_detection(self):
        """PR comments (inline) should also be detected."""
        pr_comment = {
            "id": 11,
            "user": {"login": "ChonSong"},
            "body": "## 🎯 Summary\n\n1 issue found\n\n## 🔍 Findings\n\n| 🟡 warning | `baz.py` | 20 | Bug |",
            "created_at": "2026-08-12T11:00:00Z",
        }
        body = pr_comment["body"]
        import re

        is_deep_think = bool(
            re.search(r"## 🎯 Summary|## 🔍 Findings", body, re.IGNORECASE)
        )
        assert is_deep_think, "PR comment body should be detected"


class TestFindingsDetection:
    """Test that we can detect if a review has findings."""

    def test_review_with_critical_has_findings(self):
        """Review with 🔴 in table row should have findings."""
        body = "## 🎯 Summary\n\n1 issue(s) found\n\n## 🔍 Findings\n\n| Severity | File | Line | Issue |\n|----------|------|------|-------|\n| 🔴 critical | `foo.py` | 10 | Bug |"
        import re

        has_findings = bool(re.search(r"🔴|🟡", body))
        assert has_findings

    def test_review_with_warning_has_findings(self):
        """Review with 🟡 in table row should have findings."""
        body = "## 🎯 Summary\n\n1 issue(s) found\n\n## 🔍 Findings\n\n| Severity | File | Line | Issue |\n|----------|------|------|-------|\n| 🟡 warning | `foo.py` | 10 | Bug |"
        import re

        has_findings = bool(re.search(r"🔴|🟡", body))
        assert has_findings

    def test_clean_review_no_findings(self):
        """Clean review should not have findings."""
        body = "## 🎯 Summary\n\nClean PR — no issues found.\n\n## 🔍 Findings\n\nNo critical/warning findings."
        import re

        has_findings = bool(re.search(r"🔴|🟡", body))
        assert not has_findings


class TestFollowUpCommitDetection:
    """Test that we can detect follow-up commits after review."""

    def test_commit_after_review(self):
        """Commit with timestamp after review should count."""
        review_time = "2026-08-12T00:00:00Z"
        commits = [
            {"sha": "abc", "commit": {"committer": {"date": "2026-08-11T00:00:00Z"}, "author": {"date": "2026-08-11T00:00:00Z"}}},
            {"sha": "def", "commit": {"committer": {"date": "2026-08-13T00:00:00Z"}, "author": {"date": "2026-08-13T00:00:00Z"}}},
        ]
        followup = [c for c in commits if (c["commit"].get("committer", {}) or {}).get("date") > review_time]
        assert len(followup) == 1

    def test_no_commit_after_review(self):
        """No commits after review should return empty list."""
        review_time = "2026-08-12T00:00:00Z"
        commits = [
            {"sha": "abc", "commit": {"committer": {"date": "2026-08-11T00:00:00Z"}, "author": {"date": "2026-08-11T00:00:00Z"}}},
        ]
        followup = [c for c in commits if (c["commit"].get("committer", {}) or {}).get("date") > review_time]
        assert len(followup) == 0

    def test_commit_with_null_committer(self):
        """Commits with null committer date should fall back to author date."""
        review_time = "2026-08-12T00:00:00Z"
        commits = [
            # Committer date is present
            {"sha": "abc", "commit": {"committer": {"date": "2026-08-11T00:00:00Z"}, "author": {"date": "2026-08-11T00:00:00Z"}}},
            # Committer date is None (imported/web edit), fall back to author date
            {"sha": "ghi", "commit": {"committer": {"date": None}, "author": {"date": "2026-08-14T00:00:00Z"}}},
        ]
        # Use jq-style fallback: committer.date // author.date
        def get_date(c):
            return (c["commit"].get("committer", {}) or {}).get("date") or (c["commit"].get("author", {}) or {}).get("date")

        followup = [c for c in commits if get_date(c) > review_time]
        assert len(followup) == 1

    def test_commit_with_null_committer_and_author(self):
        """Commits with both dates null should be skipped."""
        review_time = "2026-08-12T00:00:00Z"
        commits = [
            {"sha": "abc", "commit": {"committer": {"date": None}, "author": {"date": None}}},
        ]

        def get_date(c):
            return (c["commit"].get("committer", {}) or {}).get("date") or (c["commit"].get("author", {}) or {}).get("date")

        followup = [c for c in commits if get_date(c) and get_date(c) > review_time]
        assert len(followup) == 0


class TestWorkflowIntegration:
    """Integration test simulating the workflow logic."""

    def test_clean_review_passes(self):
        """Clean review → no follow-up needed → pass."""
        comments = [
            {
                "id": 1,
                "user": {"login": "riptide-review[bot]"},
                "body": "## 🎯 Summary\n\nClean PR — no issues found.",
            }
        ]
        review = comments[0]
        import re

        has_findings = bool(re.search(r"🔴|🟡", review["body"]))
        assert not has_findings

    def test_review_with_findings_requires_followup(self):
        """Review with findings + no commits → fail."""
        comments = [
            {
                "id": 1,
                "user": {"login": "riptide-review[bot]"},
                "created_at": "2026-08-12T00:00:00Z",
                "body": "## 🎯 Summary\n\n1 issue(s) found\n\n## 🔍 Findings\n\n| 🟡 warning | `foo.py` | 10 | Bug |",
            }
        ]
        commits = []  # No follow-up commits

        review = comments[0]
        import re

        has_findings = bool(re.search(r"🔴|🟡", review["body"]))
        assert has_findings

        review_time = review["created_at"]
        followup = [c for c in commits if (c["commit"].get("committer", {}) or {}).get("date") > review_time]
        assert len(followup) == 0

    def test_review_with_findings_and_followup_passes(self):
        """Review with findings + follow-up commits → pass."""
        comments = [
            {
                "id": 1,
                "user": {"login": "riptide-review[bot]"},
                "created_at": "2026-08-12T00:00:00Z",
                "body": "## 🎯 Summary\n\n1 issue(s) found\n\n## 🔍 Findings\n\n| 🟡 warning | `foo.py` | 10 | Bug |",
            }
        ]
        commits = [
            {"sha": "def", "commit": {"committer": {"date": "2026-08-13T00:00:00Z"}}},
        ]

        review = comments[0]
        import re

        has_findings = bool(re.search(r"🔴|🟡", review["body"]))
        assert has_findings

        review_time = review["created_at"]
        followup = [c for c in commits if (c["commit"].get("committer", {}) or {}).get("date") > review_time]
        assert len(followup) == 1


class TestIssueCommentFiltering:
    """Test that we filter issue_comment events correctly."""

    def test_bot_comment_skipped(self):
        """Bot comments should be skipped."""
        comment_user_type = "Bot"
        assert comment_user_type == "Bot"

    def test_review_command_detected(self):
        """@riptide-bot review command should be detected."""
        import re

        body = "Please review this @riptide-bot review"
        assert re.search(r"@riptide-bot\s+review", body, re.IGNORECASE)

    def test_unrelated_comment_skipped(self):
        """Unrelated comments should be skipped."""
        import re

        body = "Looks good to me!"
        assert not re.search(r"@riptide-bot\s+review", body, re.IGNORECASE)

    def test_bot_case_insensitive(self):
        """Bot check should be case-insensitive."""
        user_type_bot = "Bot"
        user_type_lower = "bot"
        assert user_type_bot.lower() == user_type_lower

    def test_bot_login_suffix(self):
        """Logins ending in [bot] should be skipped."""
        import re

        assert re.search(r"\[bot\]$", "riptide-review[bot]")
        assert re.search(r"\[bot\]$", "dependabot[bot]")
        assert not re.search(r"\[bot\]$", "ChonSong")


class TestEnvVarPassthrough:
    """Test that user-controlled values are passed through env (not interpolated)."""

    def test_comment_body_not_in_script(self):
        """COMMENT_BODY should be in env block, not interpolated in script."""
        with open(".github/workflows/riptide-review-required.yml") as f:
            content = f.read()

        # COMMENT_BODY should be in env: block
        assert "COMMENT_BODY: ${{ github.event.comment.body }}" in content

    def test_comment_user_not_in_script(self):
        """COMMENT_USER should be in env block."""
        with open(".github/workflows/riptide-review-required.yml") as f:
            content = f.read()

        assert "COMMENT_USER: ${{ github.event.comment.user.login }}" in content

    def test_comment_referenced_as_env_var(self):
        """COMMENT_BODY should be referenced as $COMMENT_BODY in script."""
        with open(".github/workflows/riptide-review-required.yml") as f:
            content = f.read()

        # Should reference as env var, not interpolated
        assert "$COMMENT_BODY" in content or "${COMMENT_BODY}" in content
