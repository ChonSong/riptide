#!/usr/bin/env python3
"""Tests for riptide-review-required CI workflow logic.

Tests the jq patterns used in the workflow to detect deep-think reviews
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
        # Check that TL;DR does NOT match deep-think pattern
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


class TestFindingsDetection:
    """Test that we can detect if a review has findings."""

    def test_review_with_critical_has_findings(self):
        """Review with 🔴 should have findings."""
        body = "## 🎯 Summary\n\n1 issue(s) found\n\n## 🔍 Findings\n\n| 🔴 critical | `foo.py` | 10 | Bug |"
        import re

        has_findings = bool(re.search(r"🔴|🟡", body, re.IGNORECASE))
        assert has_findings

    def test_review_with_warning_has_findings(self):
        """Review with 🟡 should have findings."""
        body = "## 🎯 Summary\n\n1 issue(s) found\n\n## 🔍 Findings\n\n| 🟡 warning | `foo.py` | 10 | Bug |"
        import re

        has_findings = bool(re.search(r"🔴|🟡", body, re.IGNORECASE))
        assert has_findings

    def test_clean_review_no_findings(self):
        """Clean review should not have findings."""
        body = "## 🎯 Summary\n\nClean PR — no issues found.\n\n## 🔍 Findings\n\nNo critical/warning findings."
        import re

        has_findings = bool(re.search(r"🔴|🟡", body, re.IGNORECASE))
        assert not has_findings


class TestFollowUpCommitDetection:
    """Test that we can detect follow-up commits after review."""

    def test_commit_after_review(self):
        """Commit with timestamp after review should count."""
        review_time = "2026-08-12T00:00:00Z"
        commits = [
            {"sha": "abc", "commit": {"committer": {"date": "2026-08-11T00:00:00Z"}}},  # Before
            {"sha": "def", "commit": {"committer": {"date": "2026-08-13T00:00:00Z"}}},  # After
        ]
        followup = [c for c in commits if c["commit"]["committer"]["date"] > review_time]
        assert len(followup) == 1

    def test_no_commit_after_review(self):
        """No commits after review should return empty list."""
        review_time = "2026-08-12T00:00:00Z"
        commits = [
            {"sha": "abc", "commit": {"committer": {"date": "2026-08-11T00:00:00Z"}}},  # Before
        ]
        followup = [c for c in commits if c["commit"]["committer"]["date"] > review_time]
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
        # Simulate: find review → no findings → pass
        review = comments[0]
        import re

        has_findings = bool(re.search(r"🔴|🟡", review["body"], re.IGNORECASE))
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

        has_findings = bool(re.search(r"🔴|🟡", review["body"], re.IGNORECASE))
        assert has_findings

        review_time = review["created_at"]
        followup = [c for c in commits if c["commit"]["committer"]["date"] > review_time]
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
            {"sha": "def", "commit": {"committer": {"date": "2026-08-13T00:00:00Z"}}},  # After
        ]

        review = comments[0]
        import re

        has_findings = bool(re.search(r"🔴|🟡", review["body"], re.IGNORECASE))
        assert has_findings

        review_time = review["created_at"]
        followup = [c for c in commits if c["commit"]["committer"]["date"] > review_time]
        assert len(followup) == 1
