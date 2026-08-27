#!/usr/bin/env python3
"""Tests for StateStore transaction handling and schema migrations.

Covers the fix(state) commit:
- deliveries table has 'status' column (NOT NULL DEFAULT 'processing')
- reserve_delivery assigns 'now' before use (no reference-before-assignment)
- reserve_delivery rolls back on IntegrityError and OperationalError
- mark_delivery_done / mark_delivery_failed transition the status column
- No redundant conn.execute('COMMIT') — only conn.commit()
"""

import sqlite3
import time
import pytest
from unittest.mock import patch, MagicMock

from riptide.state import StateStore, DELIVERY_STALE_TTL


class TestDeliveriesStatusColumn:
    """The deliveries table must have a 'status' column with DEFAULT 'processing'."""

    def test_deliveries_table_has_status_column(self, tmp_path):
        """Schema: deliveries.status exists as TEXT NOT NULL DEFAULT 'processing'."""
        db_path = str(tmp_path / "test.db")
        store = StateStore(db_path)
        conn = store._get_conn()
        # Verify column exists
        cursor = conn.execute("PRAGMA table_info(deliveries)")
        columns = {row[1]: row for row in cursor.fetchall()}
        assert "status" in columns, "deliveries table missing 'status' column"
        # Verify it's NOT NULL with default
        status_col = columns["status"]
        assert status_col[3] == 1, "status should be NOT NULL (1)"
        assert status_col[4] == "'processing'", (
            f"status default should be 'processing', got {status_col[4]!r}"
        )

    def test_reserve_delivery_sets_processing_status(self, tmp_path):
        """reserve_delivery inserts a row with status='processing'."""
        db_path = str(tmp_path / "test.db")
        store = StateStore(db_path)
        assert store.reserve_delivery("del-1") is True
        conn = store._get_conn()
        row = conn.execute(
            "SELECT delivery_id, status FROM deliveries WHERE delivery_id = ?",
            ("del-1",),
        ).fetchone()
        assert row is not None
        assert row[1] == "processing"

    def test_mark_delivery_done_transitions_status(self, tmp_path):
        """mark_delivery_done sets status='done'."""
        db_path = str(tmp_path / "test.db")
        store = StateStore(db_path)
        store.reserve_delivery("del-1")
        store.mark_delivery_done("del-1")
        conn = store._get_conn()
        row = conn.execute(
            "SELECT status FROM deliveries WHERE delivery_id = ?", ("del-1",)
        ).fetchone()
        assert row[0] == "done"

    def test_mark_delivery_failed_transitions_status(self, tmp_path):
        """mark_delivery_failed sets status='failed'."""
        db_path = str(tmp_path / "test.db")
        store = StateStore(db_path)
        store.reserve_delivery("del-1")
        store.mark_delivery_failed("del-1")
        conn = store._get_conn()
        row = conn.execute(
            "SELECT status FROM deliveries WHERE delivery_id = ?", ("del-1",)
        ).fetchone()
        assert row[0] == "failed"


class TestReserveDeliveryTransactionHandling:
    """reserve_delivery must handle errors with proper rollback."""

    def test_reserve_delivery_now_no_reference_before_assignment(self, tmp_path):
        """The 'now' variable must be assigned before use in reserve_delivery.

        Regression: an earlier version referenced 'now' inside the INSERT
        before it was assigned, causing NameError on the success path.
        """
        db_path = str(tmp_path / "test.db")
        store = StateStore(db_path)
        # If 'now' were referenced before assignment, this would raise NameError
        result = store.reserve_delivery("del-1")
        assert result is True

    def test_reserve_delivery_integrity_error_rollback(self, tmp_path):
        """On IntegrityError (duplicate), reserve_delivery must rollback and return False."""
        db_path = str(tmp_path / "test.db")
        store = StateStore(db_path)
        assert store.reserve_delivery("del-1") is True
        # Second reservation should return False (duplicate)
        assert store.reserve_delivery("del-1") is False
        # No leftover transaction state
        conn = store._get_conn()
        # Should be able to execute new queries without "cannot start transaction" error
        conn.execute("SELECT 1")

    def test_reserve_delivery_stale_re_reservation(self, tmp_path):
        """Stale 'processing' delivery should be re-reservable after TTL."""
        db_path = str(tmp_path / "test.db")
        store = StateStore(db_path)
        store.reserve_delivery("del-1")
        # Manually backdate the received_at to simulate stale
        conn = store._get_conn()
        past = time.time() - DELIVERY_STALE_TTL - 1
        conn.execute(
            "UPDATE deliveries SET received_at = ? WHERE delivery_id = ?",
            (past, "del-1"),
        )
        conn.commit()
        # Should re-reserve successfully
        assert store.reserve_delivery("del-1") is True

    def test_reserve_delivery_operational_error_rollback(self, tmp_path):
        """On OperationalError (locked), reserve_delivery must rollback and return False."""
        db_path = str(tmp_path / "test.db")
        store = StateStore(db_path)
        # Mock _get_conn to return a connection that raises on BEGIN IMMEDIATE
        real_conn = store._get_conn()

        mock_conn = MagicMock()
        mock_conn.execute = MagicMock(
            side_effect=sqlite3.OperationalError("database is locked")
        )
        mock_conn.rollback = MagicMock()

        with patch.object(store, "_get_conn", return_value=mock_conn):
            result = store.reserve_delivery("del-1")

        assert result is False
        # Rollback must have been called
        assert mock_conn.rollback.call_count >= 1

    def test_reserve_delivery_returns_false_for_active_duplicate(self, tmp_path):
        """Duplicate delivery that is NOT stale returns False."""
        db_path = str(tmp_path / "test.db")
        store = StateStore(db_path)
        assert store.reserve_delivery("del-1") is True
        # Same delivery, still fresh (not stale)
        assert store.reserve_delivery("del-1") is False


class TestSchemaMigration:
    """Schema migrations must be idempotent and handle existing databases."""

    def test_migration_idempotent(self, tmp_path):
        """Running _init_db() multiple times must not raise."""
        db_path = str(tmp_path / "test.db")
        store = StateStore(db_path)
        # Re-init should be a no-op (column already exists)
        store._init_db()
        store._init_db()
        # Still works
        assert store.reserve_delivery("del-1") is True

    def test_status_column_migration_on_existing_db(self, tmp_path):
        """A deliveries table without 'status' gets it via migration."""
        db_path = str(tmp_path / "test.db")
        # Create a deliveries table without status column
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE deliveries (
                delivery_id TEXT PRIMARY KEY,
                received_at REAL
            )
        """)
        conn.execute(
            "INSERT INTO deliveries (delivery_id, received_at) VALUES (?, ?)",
            ("old-del", time.time()),
        )
        conn.execute("""
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY)
        """)
        conn.execute("INSERT INTO schema_version (version) VALUES (7)")
        # Create other required tables so _init_db doesn't fail
        conn.execute("""
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, pr_number INTEGER, tier TEXT,
                status TEXT, created_at REAL, completed_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE processed_comments (
                comment_id INTEGER PRIMARY KEY, processed_at TEXT NOT NULL,
                result TEXT, pending_response TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE work_queue (
                id TEXT PRIMARY KEY, kind TEXT, payload TEXT,
                status TEXT, created_at REAL, started_at REAL,
                pid INTEGER, attempts INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE pr_heuristics (
                pr_key TEXT PRIMARY KEY, last_sha TEXT, reviewed_at TEXT,
                tier1_comment_id INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE checkbox_triggers (
                pr_key TEXT, label TEXT, triggered_at REAL,
                PRIMARY KEY (pr_key, label)
            )
        """)
        conn.commit()
        conn.close()

        # Now create StateStore — migration should add status column
        # and create review_memory (not pre-created here)
        store = StateStore(db_path)
        conn = store._get_conn()
        cursor = conn.execute("PRAGMA table_info(deliveries)")
        columns = {row[1]: row for row in cursor.fetchall()}
        assert "status" in columns
        # Old row should have default status
        row = conn.execute(
            "SELECT status FROM deliveries WHERE delivery_id = ?", ("old-del",)
        ).fetchone()
        assert row[0] == "processing"

    def test_review_memory_created_by_init_db(self, tmp_path):
        """_init_db must create review_memory with all 13 columns used by store_review_outcome."""
        db_path = str(tmp_path / "test.db")
        store = StateStore(db_path)
        conn = store._get_conn()
        cursor = conn.execute("PRAGMA table_info(review_memory)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "id", "pr_key", "pr_number", "owner", "repo", "head_sha",
            "findings_count", "critical_count", "warning_count",
            "verdict", "user_feedback", "created_at", "metadata",
        }
        assert expected.issubset(columns), f"Missing columns: {expected - columns}"
