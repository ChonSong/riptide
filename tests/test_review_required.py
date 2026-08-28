#!/usr/bin/env python3
"""Tests for riptide-review-required CI gate.

Simplified rule: if a review comment has ## 🔍 Findings AND 🔴/🟡 in a
table row, then at least one commit must exist after the review timestamp.
"""

from __future__ import annotations

import re

import pytest


# ── Helpers (mirror workflow logic) ──────────────────────────────────────────

def find_review_with_findings(comments: list[dict]) -> dict | None:
    """Find the most recent comment containing '## 🔍 Findings'."""
    matches = [c for c in comments if "## 🔍 Findings" in (c.get("body") or "")]
    if not matches:
        return None
    return sorted(matches, key=lambda c: c.get("created_at", ""))[-1]


def has_findings(review: dict | None) -> bool:
    """Check if review has 🔴 or 🟡 in a table row."""
    if not review:
        return False
    body = review.get("body") or ""
    return bool(re.search(r"\|[\s]*🔴|\|[\s]*🟡", body))


def followup_commit_exists(commits: list[dict], review_time: str) -> bool:
    """Check if any commit is newer than the review."""
    for c in commits:
        date = c.get("commit", {}).get("committer", {}).get("date") or \
               c.get("commit", {}).get("author", {}).get("date")
        if date and date > review_time:
            return True
    return False


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestFindReview:
    """Test finding the most recent review with findings."""

    def test_no_review(self):
        """No review comments → no check needed."""
        comments: list[dict] = []
        assert find_review_with_findings(comments) is None

    def test_no_findings_section(self):
        """Comment without ## 🔍 Findings is not a review."""
        comments = [
            {"id": 1, "body": "LGTM!", "created_at": "2026-08-12T10:00:00Z"}
        ]
        assert find_review_with_findings(comments) is None

    def test_review_with_findings_section(self):
        """Comment with ## 🔍 Findings is detected."""
        comments = [
            {"id": 1, "body": "## 🔍 Findings\n\n| 🔴 | `a.py` | 1 | bug |", "created_at": "2026-08-12T10:00:00Z"}
        ]
        review = find_review_with_findings(comments)
        assert review is not None
        assert review["id"] == 1

    def test_most_review_selected(self):
        """When multiple reviews exist, the most recent is selected."""
        comments = [
            {"id": 1, "body": "## 🔍 Findings\n\n| 🔴 | `a.py` | 1 | bug |", "created_at": "2026-08-12T10:00:00Z"},
            {"id": 2, "body": "## 🔍 Findings\n\n| 🟡 | `b.py` | 2 | warn |", "created_at": "2026-08-12T11:00:00Z"},
        ]
        review = find_review_with_findings(comments)
        assert review is not None and review["id"] == 2


class TestFindingsDetection:
    """Test detection of 🔴/🟡 in table rows."""

    def test_critical_finding(self):
        body = "## 🔍 Findings\n\n| Severity | File | Line | Issue |\n|----------|------|------|-------|\n| 🔴 critical | `foo.py` | 10 | Bug |"
        assert has_findings({"body": body}) is True

    def test_warning_finding(self):
        body = "## 🔍 Findings\n\n| Severity | File | Line | Issue |\n|----------|------|------|-------|\n| 🟡 warning | `bar.py` | 5 | Warn |"
        assert has_findings({"body": body}) is True

    def test_clean_review(self):
        body = "## 🔍 Findings\n\n| Severity | File | Line | Issue |\n|----------|------|------|-------|\n| 🔵 info | `baz.py` | 1 | nothing |"
        assert has_findings({"body": body}) is False

    def test_no_findings_in_prose_only(self):
        """🔴 in prose (not table row) should not count."""
        body = "## 🔍 Findings\n\nNo 🔴 issues found."
        assert has_findings({"body": body}) is False


class TestFollowupCommit:
    """Test follow-up commit detection."""

    def test_commit_after_review(self):
        commits = [
            {"commit": {"committer": {"date": "2026-08-12T10:00:00Z"}, "author": {"date": "2026-08-12T10:00:00Z"}}},
            {"commit": {"committer": {"date": "2026-08-12T12:00:00Z"}, "author": {"date": "2026-08-12T12:00:00Z"}}},
        ]
        assert followup_commit_exists(commits, "2026-08-12T11:00:00Z") is True

    def test_no_commit_after_review(self):
        commits = [
            {"commit": {"committer": {"date": "2026-08-12T10:00:00Z"}, "author": {"date": "2026-08-12T10:00:00Z"}}},
        ]
        assert followup_commit_exists(commits, "2026-08-12T11:00:00Z") is False

    def test_commit_with_null_committer(self):
        """Commits with null committer date should use author date."""
        commits = [
            {"commit": {"committer": {"date": None}, "author": {"date": "2026-08-12T12:00:00Z"}}},
        ]
        assert followup_commit_exists(commits, "2026-08-12T11:00:00Z") is True


class TestEndToEnd:
    """Integration-style tests mirroring workflow logic."""

    def test_clean_review_passes(self):
        """Review with no findings → no follow-up needed."""
        comments = [
            {"id": 1, "body": "## 🔍 Findings\n\n| 🔵 info | `a.py` | 1 | nothing |", "created_at": "2026-08-12T10:00:00Z"}
        ]
        review = find_review_with_findings(comments)
        assert has_findings(review) is False

    def test_findings_without_followup_fails(self):
        """Review with findings + no follow-up commit → should fail."""
        comments = [
            {"id": 1, "body": "## 🔍 Findings\n\n| 🔴 critical | `a.py` | 1 | bug |", "created_at": "2026-08-12T10:00:00Z"}
        ]
        commits = [
            {"commit": {"committer": {"date": "2026-08-12T09:00:00Z"}, "author": {"date": "2026-08-12T09:00:00Z"}}},
        ]
        review = find_review_with_findings(comments)
        assert review is not None
        assert has_findings(review) is True
        assert followup_commit_exists(commits, review["created_at"]) is False

    def test_findings_with_followup_passes(self):
        """Review with findings + follow-up commit → passes."""
        comments = [
            {"id": 1, "body": "## 🔍 Findings\n\n| 🔴 critical | `a.py` | 1 | bug |", "created_at": "2026-08-12T10:00:00Z"}
        ]
        commits = [
            {"commit": {"committer": {"date": "2026-08-12T09:00:00Z"}, "author": {"date": "2026-08-12T09:00:00Z"}}},
            {"commit": {"committer": {"date": "2026-08-12T11:00:00Z"}, "author": {"date": "2026-08-12T11:00:00Z"}}},
        ]
        review = find_review_with_findings(comments)
        assert review is not None
        assert has_findings(review) is True
        assert followup_commit_exists(commits, review["created_at"]) is True

    def test_no_review_fails_check(self):
        """No review comment → gate fails (fail-closed behavior)."""
        comments = [
            {"id": 1, "body": "LGTM!", "created_at": "2026-08-12T10:00:00Z"}
        ]
        review = find_review_with_findings(comments)
        assert review is None
        # Fail-closed: when no review is found, the CI gate exits with status 1
        # (see .github/workflows/riptide-review-required.yml)
