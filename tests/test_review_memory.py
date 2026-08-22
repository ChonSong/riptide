#!/usr/bin/env python3
"""Tests for review_memory module and integration with state.py."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure we can import riptide modules
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from riptide.state import StateStore
from riptide.review_memory import (
    get_memory_context,
    store_review_outcome,
    get_review_profile,
)


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary StateStore for testing."""
    db_path = str(tmp_path / "test_state.db")
    store = StateStore(db_path=db_path)
    return store


@pytest.fixture
def tmp_db_path(tmp_path):
    """Return a temporary DB path string."""
    return str(tmp_path / "test_state.db")


class TestStateStoreReviewMemory:
    """Tests for StateStore review memory methods."""

    def test_store_review_outcome_creates_row(self, tmp_db):
        """store_review_outcome inserts a row into review_memory."""
        tmp_db.store_review_outcome(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            head_sha="abc123def456",
            findings_count=5,
            critical_count=2,
            warning_count=3,
            verdict="warn",
        )
        conn = tmp_db._get_conn()
        row = conn.execute("SELECT COUNT(*) FROM review_memory").fetchone()
        assert row[0] == 1

    def test_store_review_outcome_data_integrity(self, tmp_db):
        """store_review_outcome stores all fields correctly."""
        tmp_db.store_review_outcome(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            head_sha="abc123def456",
            findings_count=5,
            critical_count=2,
            warning_count=3,
            verdict="warn",
            metadata={"source": "test"},
        )
        conn = tmp_db._get_conn()
        row = conn.execute(
            "SELECT pr_key, pr_number, owner, repo, head_sha, "
            "findings_count, critical_count, warning_count, verdict, "
            "user_feedback, metadata FROM review_memory"
        ).fetchone()
        assert row[0] == "ChonSong/riptide#42"
        assert row[1] == 42
        assert row[2] == "ChonSong"
        assert row[3] == "riptide"
        assert row[4] == "abc123def456"
        assert row[5] == 5
        assert row[6] == 2
        assert row[7] == 3
        assert row[8] == "warn"
        assert row[9] == 0  # user_feedback default
        assert json.loads(row[10]) == {"source": "test"}

    def test_store_review_outcome_updates_profile(self, tmp_db):
        """store_review_outcome creates/updates review_profiles."""
        tmp_db.store_review_outcome(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            head_sha="abc123",
            findings_count=3,
            critical_count=1,
            warning_count=2,
            verdict="warn",
        )
        profile = tmp_db.get_review_profile("riptide")
        assert profile is not None
        assert profile["total_reviews"] == 1
        assert profile["repo"] == "riptide"

    def test_store_multiple_reviews_increments_counter(self, tmp_db):
        """Multiple store_review_outcome calls increment total_reviews."""
        for i in range(3):
            tmp_db.store_review_outcome(
                owner="ChonSong",
                repo="riptide",
                pr_number=40 + i,
                head_sha=f"sha{i}",
                findings_count=i + 1,
                critical_count=i,
                warning_count=1,
                verdict="pass",
            )
        profile = tmp_db.get_review_profile("riptide")
        assert profile["total_reviews"] == 3

    def test_get_review_profile_no_history(self, tmp_db):
        """get_review_profile returns None when no history exists."""
        profile = tmp_db.get_review_profile("nonexistent")
        assert profile is None

    def test_get_review_profile_returns_dict(self, tmp_db):
        """get_review_profile returns a properly structured dict."""
        tmp_db.store_review_outcome(
            owner="ChonSong",
            repo="riptide",
            pr_number=1,
            head_sha="sha1",
            findings_count=0,
            critical_count=0,
            warning_count=0,
            verdict="pass",
        )
        profile = tmp_db.get_review_profile("riptide")
        assert isinstance(profile, dict)
        assert "repo" in profile
        assert "total_reviews" in profile
        assert "common_findings" in profile
        assert "last_review_at" in profile
        assert "updated_at" in profile

    def test_get_memory_context_no_history(self, tmp_db):
        """get_memory_context returns empty string when no history."""
        ctx = tmp_db.get_memory_context("ChonSong", "riptide")
        assert ctx == ""

    def test_get_memory_context_with_history(self, tmp_db):
        """get_memory_context returns context string with history."""
        tmp_db.store_review_outcome(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            head_sha="abc123",
            findings_count=5,
            critical_count=2,
            warning_count=3,
            verdict="warn",
        )
        ctx = tmp_db.get_memory_context("ChonSong", "riptide")
        assert isinstance(ctx, str)
        assert len(ctx) > 0
        assert "Review History" in ctx
        assert "Total reviews: 1" in ctx

    def test_get_memory_context_contains_stats(self, tmp_db):
        """get_memory_context includes aggregate stats."""
        tmp_db.store_review_outcome(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            head_sha="abc123",
            findings_count=10,
            critical_count=3,
            warning_count=5,
            verdict="warn",
        )
        ctx = tmp_db.get_memory_context("ChonSong", "riptide")
        assert "critical rate" in ctx.lower() or "critical rate" in ctx
        assert "warning rate" in ctx.lower() or "warning rate" in ctx

    def test_get_memory_context_multiple_reviews(self, tmp_db):
        """get_memory_context shows multiple recent reviews."""
        for i in range(3):
            tmp_db.store_review_outcome(
                owner="ChonSong",
                repo="riptide",
                pr_number=40 + i,
                head_sha=f"sha{i}",
                findings_count=i + 1,
                critical_count=i,
                warning_count=1,
                verdict="pass" if i % 2 == 0 else "warn",
            )
        ctx = tmp_db.get_memory_context("ChonSong", "riptide")
        assert "Total reviews: 3" in ctx

    def test_review_memory_id_is_unique(self, tmp_db):
        """Each review outcome gets a unique ID."""
        tmp_db.store_review_outcome(
            owner="ChonSong", repo="riptide", pr_number=1,
            head_sha="sha1", findings_count=0, critical_count=0,
            warning_count=0, verdict="pass",
        )
        tmp_db.store_review_outcome(
            owner="ChonSong", repo="riptide", pr_number=2,
            head_sha="sha2", findings_count=0, critical_count=0,
            warning_count=0, verdict="pass",
        )
        conn = tmp_db._get_conn()
        rows = conn.execute("SELECT id FROM review_memory").fetchall()
        assert len(rows) == 2
        assert rows[0][0] != rows[1][0]

    def test_review_memory_created_at_is_iso8601(self, tmp_db):
        """created_at is stored as ISO 8601 string."""
        tmp_db.store_review_outcome(
            owner="ChonSong", repo="riptide", pr_number=1,
            head_sha="sha1", findings_count=0, critical_count=0,
            warning_count=0, verdict="pass",
        )
        conn = tmp_db._get_conn()
        row = conn.execute("SELECT created_at FROM review_memory").fetchone()
        # Should be parseable as ISO 8601
        dt = datetime.fromisoformat(row[0])
        assert dt.tzinfo is not None

    def test_review_profiles_updated_at_changes(self, tmp_db):
        """review_profiles updated_at changes on each store."""
        tmp_db.store_review_outcome(
            owner="ChonSong", repo="riptide", pr_number=1,
            head_sha="sha1", findings_count=0, critical_count=0,
            warning_count=0, verdict="pass",
        )
        profile1 = tmp_db.get_review_profile("riptide")
        updated_at_1 = profile1["updated_at"]

        # Store another review
        tmp_db.store_review_outcome(
            owner="ChonSong", repo="riptide", pr_number=2,
            head_sha="sha2", findings_count=0, critical_count=0,
            warning_count=0, verdict="pass",
        )
        profile2 = tmp_db.get_review_profile("riptide")
        updated_at_2 = profile2["updated_at"]

        # updated_at should be the same or later (timestamps may be identical in fast tests)
        assert updated_at_2 >= updated_at_1

    def test_store_review_outcome_with_metadata_dict(self, tmp_db):
        """store_review_outcome serializes metadata dict to JSON."""
        tmp_db.store_review_outcome(
            owner="ChonSong", repo="riptide", pr_number=1,
            head_sha="sha1", findings_count=0, critical_count=0,
            warning_count=0, verdict="pass",
            metadata={"key": "value", "nested": {"a": 1}},
        )
        conn = tmp_db._get_conn()
        row = conn.execute("SELECT metadata FROM review_memory").fetchone()
        assert row[0] is not None
        meta = json.loads(row[0])
        assert meta["key"] == "value"
        assert meta["nested"] == {"a": 1}

    def test_store_review_outcome_without_metadata(self, tmp_db):
        """store_review_outcome works without metadata."""
        tmp_db.store_review_outcome(
            owner="ChonSong", repo="riptide", pr_number=1,
            head_sha="sha1", findings_count=0, critical_count=0,
            warning_count=0, verdict="pass",
        )
        conn = tmp_db._get_conn()
        row = conn.execute("SELECT metadata FROM review_memory").fetchone()
        assert row[0] is None

    def test_review_memory_indexed_by_pr_key(self, tmp_db):
        """review_memory has an index on pr_key for fast lookups."""
        tmp_db.store_review_outcome(
            owner="ChonSong", repo="riptide", pr_number=1,
            head_sha="sha1", findings_count=0, critical_count=0,
            warning_count=0, verdict="pass",
        )
        conn = tmp_db._get_conn()
        # Check index exists
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_review_memory_pr_key'"
        ).fetchone()
        assert row is not None

    def test_review_memory_indexed_by_repo(self, tmp_db):
        """review_memory has an index on repo for fast lookups."""
        tmp_db.store_review_outcome(
            owner="ChonSong", repo="riptide", pr_number=1,
            head_sha="sha1", findings_count=0, critical_count=0,
            warning_count=0, verdict="pass",
        )
        conn = tmp_db._get_conn()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_review_memory_repo'"
        ).fetchone()
        assert row is not None


class TestReviewMemoryModule:
    """Tests for the review_memory module functions."""

    @patch("riptide.review_memory.StateStore")
    def test_get_memory_context_calls_state_store(self, MockStateStore, tmp_db_path):
        """get_memory_context delegates to StateStore.get_memory_context."""
        mock_store = MagicMock()
        mock_store.get_memory_context.return_value = "test context"
        MockStateStore.return_value = mock_store

        result = get_memory_context("ChonSong", "riptide")
        assert result == "test context"
        mock_store.get_memory_context.assert_called_once_with("ChonSong", "riptide")

    @patch("riptide.review_memory.StateStore")
    def test_store_review_outcome_calls_state_store(self, MockStateStore, tmp_db_path):
        """store_review_outcome delegates to StateStore.store_review_outcome."""
        mock_store = MagicMock()
        MockStateStore.return_value = mock_store

        store_review_outcome(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            head_sha="abc123",
            findings_count=5,
            critical_count=2,
            warning_count=3,
            verdict="warn",
            metadata={"source": "test"},
        )
        mock_store.store_review_outcome.assert_called_once_with(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            head_sha="abc123",
            findings_count=5,
            critical_count=2,
            warning_count=3,
            verdict="warn",
            metadata='{"source": "test"}',
        )

    @patch("riptide.review_memory.StateStore")
    def test_get_review_profile_calls_state_store(self, MockStateStore, tmp_db_path):
        """get_review_profile delegates to StateStore.get_review_profile."""
        mock_store = MagicMock()
        mock_store.get_review_profile.return_value = {"total_reviews": 5}
        MockStateStore.return_value = mock_store

        result = get_review_profile("riptide")
        assert result == {"total_reviews": 5}
        mock_store.get_review_profile.assert_called_once_with("riptide")


class TestReviewMemoryIntegration:
    """Integration tests for review memory with real StateStore."""

    def test_full_lifecycle(self, tmp_db):
        """Test full lifecycle: store → profile → context."""
        # Initially no profile
        assert tmp_db.get_review_profile("riptide") is None
        assert tmp_db.get_memory_context("ChonSong", "riptide") == ""

        # Store first review
        tmp_db.store_review_outcome(
            owner="ChonSong", repo="riptide", pr_number=1,
            head_sha="sha1", findings_count=3, critical_count=1,
            warning_count=2, verdict="warn",
        )

        # Profile exists
        profile = tmp_db.get_review_profile("riptide")
        assert profile["total_reviews"] == 1

        # Context is non-empty
        ctx = tmp_db.get_memory_context("ChonSong", "riptide")
        assert len(ctx) > 0
        assert "Review History" in ctx

        # Store second review
        tmp_db.store_review_outcome(
            owner="ChonSong", repo="riptide", pr_number=2,
            head_sha="sha2", findings_count=0, critical_count=0,
            warning_count=0, verdict="pass",
        )

        # Profile updated
        profile = tmp_db.get_review_profile("riptide")
        assert profile["total_reviews"] == 2

    def test_separate_repos_independent(self, tmp_db):
        """Review memory for different repos is independent."""
        tmp_db.store_review_outcome(
            owner="ChonSong", repo="repo-a", pr_number=1,
            head_sha="sha1", findings_count=1, critical_count=0,
            warning_count=1, verdict="warn",
        )
        tmp_db.store_review_outcome(
            owner="ChonSong", repo="repo-b", pr_number=1,
            head_sha="sha1", findings_count=5, critical_count=2,
            warning_count=3, verdict="fail",
        )

        profile_a = tmp_db.get_review_profile("repo-a")
        profile_b = tmp_db.get_review_profile("repo-b")

        assert profile_a["total_reviews"] == 1
        assert profile_b["total_reviews"] == 1

        ctx_a = tmp_db.get_memory_context("ChonSong", "repo-a")
        ctx_b = tmp_db.get_memory_context("ChonSong", "repo-b")

        # Contexts should be different
        assert ctx_a != ctx_b

    def test_memory_context_in_prompt_format(self, tmp_db):
        """Memory context is formatted for prompt injection."""
        tmp_db.store_review_outcome(
            owner="ChonSong", repo="riptide", pr_number=1,
            head_sha="sha1", findings_count=3, critical_count=1,
            warning_count=2, verdict="warn",
        )
        ctx = tmp_db.get_memory_context("ChonSong", "riptide")
        # Should contain markdown-style headers
        assert "##" in ctx
        # Should mention historical patterns
        assert "historical patterns" in ctx.lower() or "common in the past" in ctx.lower()


class TestSchemaMigration:
    """Tests for schema migration to v7."""

    def test_v7_tables_exist(self, tmp_db):
        """review_memory and review_profiles tables exist after init."""
        conn = tmp_db._get_conn()
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "review_memory" in tables
        assert "review_profiles" in tables

    def test_v7_columns_exist(self, tmp_db):
        """review_memory has all expected columns."""
        conn = tmp_db._get_conn()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(review_memory)").fetchall()}
        expected = {
            "id", "pr_key", "pr_number", "owner", "repo", "head_sha",
            "findings_count", "critical_count", "warning_count",
            "verdict", "user_feedback", "created_at", "metadata",
        }
        assert expected.issubset(cols)

    def test_v7_profile_columns_exist(self, tmp_db):
        """review_profiles has all expected columns."""
        conn = tmp_db._get_conn()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(review_profiles)").fetchall()}
        expected = {"repo", "total_reviews", "common_findings", "last_review_at", "updated_at"}
        assert expected.issubset(cols)

    def test_v7_schema_version(self, tmp_db):
        """Schema version is 7 after init."""
        conn = tmp_db._get_conn()
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] == 7


class TestWebhookMergeStorage:
    """Tests for webhook merge detection storing review outcomes."""

    @patch("riptide.webhook.store_review_outcome")
    @patch("riptide.webhook.subprocess.Popen")
    @patch("riptide.webhook.shutil.which", return_value="/usr/bin/systemd-run")
    def test_merge_stores_outcome(self, mock_which, mock_popen, mock_store, tmp_path):
        """PR merge triggers store_review_outcome."""
        from riptide.webhook import handle_pull_request

        # Create a mock payload for a merged PR
        payload = {
            "action": "closed",
            "pull_request": {
                "number": 42,
                "merged": True,
                "head": {"sha": "abc123def456"},
                "base": {"ref": "main"},
            },
            "repository": {
                "full_name": "ChonSong/riptide",
                "name": "riptide",
                "default_branch": "main",
            },
            "installation": {"id": 12345},
        }

        # Mock the deploy script path
        with patch("riptide.webhook.Path") as mock_path_cls:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = False  # Skip deploy
            mock_path_cls.return_value = mock_path_instance

            import asyncio
            asyncio.run(handle_pull_request(payload, "test-delivery-id"))

        # store_review_outcome should have been called
        mock_store.assert_called_once()
        call_kwargs = mock_store.call_args[1]
        assert call_kwargs["owner"] == "ChonSong"
        assert call_kwargs["repo"] == "riptide"
        assert call_kwargs["pr_number"] == 42
        assert call_kwargs["verdict"] == "merged"

    @patch("riptide.webhook.store_review_outcome")
    def test_non_merge_no_store(self, mock_store, tmp_path):
        """PR closed without merge does NOT store outcome."""
        from riptide.webhook import handle_pull_request

        payload = {
            "action": "closed",
            "pull_request": {
                "number": 42,
                "merged": False,
                "head": {"sha": "abc123"},
                "base": {"ref": "main"},
            },
            "repository": {
                "full_name": "ChonSong/riptide",
                "name": "riptide",
                "default_branch": "main",
            },
            "installation": {"id": 12345},
        }

        import asyncio
        asyncio.run(handle_pull_request(payload, "test-delivery-id"))

        mock_store.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])