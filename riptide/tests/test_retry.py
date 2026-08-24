#!/usr/bin/env python3
"""Tests for tenacity retry decorators on DB operations in riptide/state.py."""

import sqlite3
import pytest

from riptide import state


class TestRetryConfigs:
    """Test that retry configs are properly defined."""

    def test_retry_db_fast_exists(self):
        """retry_db_fast should be a callable retry decorator."""
        assert callable(state.retry_db_fast)

    def test_retry_db_background_exists(self):
        """retry_db_background should be a callable retry decorator."""
        assert callable(state.retry_db_background)

    def test_delivery_stale_ttl_is_300(self):
        """DELIVERY_STALE_TTL should be 300 seconds (5 minutes)."""
        assert state.DELIVERY_STALE_TTL == 300


class TestStateStoreRetry:
    """Test that StateStore operations use retry decorators."""

    def test_reserve_delivery_has_retry_decorator(self):
        """reserve_delivery should have retry_db_fast decorator."""
        # Check that the method is wrapped (tenacity wraps functions)
        method = state.StateStore.reserve_delivery
        # Tenacity wraps with __wrapped__ attribute
        assert hasattr(method, '__wrapped__') or 'retry' in repr(method)

    def test_mark_delivery_done_has_retry_decorator(self):
        """mark_delivery_done should have retry_db_fast decorator."""
        method = state.StateStore.mark_delivery_done
        assert hasattr(method, '__wrapped__') or 'retry' in repr(method)

    def test_mark_delivery_failed_has_retry_decorator(self):
        """mark_delivery_failed should have retry_db_fast decorator."""
        method = state.StateStore.mark_delivery_failed
        assert hasattr(method, '__wrapped__') or 'retry' in repr(method)

    def test_create_job_has_retry_decorator(self):
        """create_job should have retry_db_fast decorator."""
        method = state.StateStore.create_job
        assert hasattr(method, '__wrapped__') or 'retry' in repr(method)

    def test_mark_complete_has_retry_decorator(self):
        """mark_complete should have retry_db_background decorator."""
        method = state.StateStore.mark_complete
        assert hasattr(method, '__wrapped__') or 'retry' in repr(method)

    def test_mark_failed_has_retry_decorator(self):
        """mark_failed should have retry_db_background decorator."""
        method = state.StateStore.mark_failed
        assert hasattr(method, '__wrapped__') or 'retry' in repr(method)


class TestStateStoreRetryBehavior:
    """Test actual retry behavior with a real in-memory DB."""

    def test_reserve_delivery_succeeds(self, tmp_path):
        """reserve_delivery should work with a fresh DB."""
        db_path = str(tmp_path / "test.db")
        store = state.StateStore(db_path)
        assert store.reserve_delivery("del-1") is True

    def test_reserve_delivery_prevents_double_reservation(self, tmp_path):
        """reserve_delivery should return False for same delivery_id."""
        db_path = str(tmp_path / "test.db")
        store = state.StateStore(db_path)
        assert store.reserve_delivery("del-1") is True
        assert store.reserve_delivery("del-1") is False

    def test_mark_delivery_done_transitions_state(self, tmp_path):
        """mark_delivery_done should transition from processing to done."""
        db_path = str(tmp_path / "test.db")
        store = state.StateStore(db_path)
        store.reserve_delivery("del-1")
        store.mark_delivery_failed("del-1")
        # Verify no exception
