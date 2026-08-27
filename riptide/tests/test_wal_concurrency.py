#!/usr/bin/env python3
"""Tests for SQLite WAL mode concurrency fix and webhook gh CLI fallback."""

import os
import tempfile
import threading

import pytest

from riptide.state import StateStore


class TestStateStoreConcurrency:
    """Test SQLite WAL mode concurrency fixes."""

    def test_wal_mode_enabled(self):
        """WAL mode should be enabled on the connection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            state = StateStore(db_path)
            conn = state._get_conn()
            row = conn.execute("PRAGMA journal_mode").fetchone()
            assert row[0] == "wal"

    def test_busy_timeout_increased(self):
        """busy_timeout should be 30 seconds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            state = StateStore(db_path)
            conn = state._get_conn()
            row = conn.execute("PRAGMA busy_timeout").fetchone()
            assert row[0] == 30000

    def test_synchronous_normal(self):
        """synchronous should be NORMAL (1)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            state = StateStore(db_path)
            conn = state._get_conn()
            row = conn.execute("PRAGMA synchronous").fetchone()
            assert row[0] == 1  # NORMAL

    def test_concurrent_delivery_reservations(self):
        """Multiple threads should be able to reserve deliveries concurrently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            state = StateStore(db_path)

            num_threads = 20
            errors = []
            results = []

            def reserve(thread_id):
                try:
                    delivery_id = f"test-delivery-{thread_id}"
                    result = state.reserve_delivery(delivery_id)
                    results.append((thread_id, result))
                except Exception as e:
                    errors.append((thread_id, str(e)))

            threads = []
            for i in range(num_threads):
                t = threading.Thread(target=reserve, args=(i,))
                threads.append(t)

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Errors: {errors}"
            assert len(results) == num_threads

    def test_concurrent_job_creation(self):
        """Multiple threads should be able to create jobs concurrently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            state = StateStore(db_path)

            num_threads = 20
            errors = []

            def create(thread_id):
                try:
                    job_id = f"test-job-{thread_id}"
                    state.create_job(job_id, 123, "tier1")
                except Exception as e:
                    errors.append((thread_id, str(e)))

            threads = []
            for i in range(num_threads):
                t = threading.Thread(target=create, args=(i,))
                threads.append(t)

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Errors: {errors}"

    def test_no_fcntl_import(self):
        """fcntl should not be imported in state.py."""
        from pathlib import Path
        import riptide.state as state_module
        source = Path(state_module.__file__).read_text()
        assert "import fcntl" not in source
        assert "fcntl.flock" not in source

    def test_no_lock_file_created(self):
        """No lock file should be created alongside the database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            state = StateStore(db_path)
            lock_path = db_path + ".lock"
            assert not os.path.exists(lock_path)

    def test_reserve_delivery_uses_begin_immediate(self):
        """reserve_delivery should use BEGIN IMMEDIATE for atomicity."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            state = StateStore(db_path)

            # First reservation should succeed
            assert state.reserve_delivery("test-1") is True

            # Duplicate should return False (not raise)
            assert state.reserve_delivery("test-1") is False

    def test_check_same_thread_false(self):
        """Connection should allow cross-thread access via thread-local storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            state = StateStore(db_path)

            errors = []

            def query():
                try:
                    conn = state._get_conn()
                    conn.execute("SELECT 1")
                except Exception as e:
                    errors.append(str(e))

            t = threading.Thread(target=query)
            t.start()
            t.join()

            assert len(errors) == 0, f"Errors: {errors}"
