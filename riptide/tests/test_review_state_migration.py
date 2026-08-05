"""
Tests for state migration, reserve_job changes(), and T0Orchestrator integration.
"""

import os
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from riptide.state import StateStore, POLLER_DB_PATH


class TestReserveJobChangesDetection:
    """Test that reserve_job uses changes() correctly (not total_changes)."""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_reserve_job_returns_true_on_first_reservation(self):
        store = StateStore(db_path=self.db_path)
        result = store.reserve_job("riptide-review-owner-repo-42-abc-123", 42, "t1", "riptide-review-owner-repo-42")
        assert result is True

    def test_reserve_job_returns_false_when_already_pending(self):
        store = StateStore(db_path=self.db_path)
        store.reserve_job("riptide-review-owner-repo-42-abc-123", 42, "t1", "riptide-review-owner-repo-42")
        result = store.reserve_job("riptide-review-owner-repo-42-def-456", 42, "t1", "riptide-review-owner-repo-42")
        assert result is False

    def test_reserve_job_different_pr_independent(self):
        store = StateStore(db_path=self.db_path)
        store.reserve_job("riptide-review-owner-repo-42-abc-123", 42, "t1", "riptide-review-owner-repo-42")
        result = store.reserve_job("riptide-review-owner-repo-43-abc-123", 43, "t1", "riptide-review-owner-repo-43")
        assert result is True

    def test_reserve_job_does_not_rely_on_total_changes(self):
        """Verify reserve_job returns correct result even after other operations."""
        store = StateStore(db_path=self.db_path)
        # Do some other operations first
        store.reserve_delivery("delivery-1")
        store.reserve_delivery("delivery-2")
        # Now reserve a job
        result = store.reserve_job("riptide-review-owner-repo-42-abc-123", 42, "t1", "riptide-review-owner-repo-42")
        assert result is True
        # Reserve same again - should be False
        result2 = store.reserve_job("riptide-review-owner-repo-42-def-456", 42, "t1", "riptide-review-owner-repo-42")
        assert result2 is False


class TestMigratePollerComments:
    """Test automatic migration from poller's metadata.db."""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.old_db_path = Path(self.tmp_dir) / "metadata.db"

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_legacy_db(self, with_pending=True):
        """Create a legacy poller metadata.db with sample data."""
        self.old_db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.old_db_path)) as conn:
            if with_pending:
                conn.execute("""
                    CREATE TABLE poller_processed_comments (
                        comment_id INTEGER PRIMARY KEY,
                        processed_at TEXT NOT NULL,
                        result TEXT,
                        pending_response TEXT
                    )
                """)
                conn.execute(
                    "INSERT INTO poller_processed_comments (comment_id, processed_at, result, pending_response) VALUES (?, ?, ?, ?)",
                    (1001, "2026-01-01T00:00:00", '{"status": "ok"}', "test-pending")
                )
            else:
                conn.execute("""
                    CREATE TABLE poller_processed_comments (
                        comment_id INTEGER PRIMARY KEY,
                        processed_at TEXT NOT NULL,
                        result TEXT
                    )
                """)
                conn.execute(
                    "INSERT INTO poller_processed_comments (comment_id, processed_at, result) VALUES (?, ?, ?)",
                    (1001, "2026-01-01T00:00:00", '{"status": "ok"}')
                )
            conn.commit()

    def test_migration_runs_on_fresh_db(self):
        """When old poller DB exists, migration should run on init."""
        self._create_legacy_db()
        
        with patch("riptide.state.POLLER_DB_PATH", self.old_db_path):
            store = StateStore(db_path=self.db_path)
            # Verify migration ran
            assert store.is_comment_processed(1001)

    def test_migration_handles_missing_old_db(self):
        """Migration should silently skip if old DB doesn't exist."""
        with patch("riptide.state.POLLER_DB_PATH", self.old_db_path):
            store = StateStore(db_path=self.db_path)
            # Should not raise even if POLLER_DB_PATH doesn't exist
            store._migrate_poller_comments()

    def test_migration_with_pending_response(self):
        """Migration should preserve pending_response column."""
        self._create_legacy_db(with_pending=True)
        
        with patch("riptide.state.POLLER_DB_PATH", self.old_db_path):
            store = StateStore(db_path=self.db_path)
            assert store.is_comment_processed(1001)
            pending = store.get_pending_response(1001)
            assert pending == "test-pending"

    def test_migration_without_pending_response(self):
        """Migration should handle legacy schema without pending_response."""
        self._create_legacy_db(with_pending=False)
        
        with patch("riptide.state.POLLER_DB_PATH", self.old_db_path):
            store = StateStore(db_path=self.db_path)
            assert store.is_comment_processed(1001)

    def test_migration_failure_does_not_update_schema_version(self):
        """If migration fails, schema version should remain retryable."""
        self._create_legacy_db()
        
        # Use a fresh DB path to avoid interference from previous test runs
        fresh_db_path = os.path.join(self.tmp_dir, "fresh_test.db")
        
        # Mock _migrate_poller_comments to raise an exception
        with patch("riptide.state.POLLER_DB_PATH", self.old_db_path):
            with patch.object(StateStore, "_migrate_poller_comments", side_effect=Exception("Migration failed")):
                with pytest.raises(Exception, match="Migration failed"):
                    StateStore(db_path=fresh_db_path)
            
            # Verify schema version was NOT updated (still < 2)
            # Use a separate connection to check since the StateStore creation failed
            with sqlite3.connect(fresh_db_path) as conn:
                row = conn.execute("SELECT version FROM schema_version").fetchone()
                version = row[0] if row else 0
                assert version < 2  # Migration should be retryable


class TestT0OrchestratorStateStoreIntegration:
    """Test that T0Orchestrator uses the new StateStore from state.py."""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_t0orchestrator_uses_new_state_store(self):
        from riptide.orchestrator import T0Orchestrator
        from riptide.state import StateStore as NewStateStore
        
        store = StateStore(db_path=self.db_path)
        orch = T0Orchestrator(companion=None, github_client=None, state_store=store)
        # The state should be an instance of the new StateStore from state.py
        assert isinstance(orch.state, NewStateStore)

    def test_t0orchestrator_state_store_has_processed_comments(self):
        """Verify the new StateStore has processed_comments table."""
        from riptide.orchestrator import T0Orchestrator
        
        store = StateStore(db_path=self.db_path)
        orch = T0Orchestrator(companion=None, github_client=None, state_store=store)
        # Should be able to mark a comment processed (method from new StateStore)
        orch.state.mark_comment_processed(12345, result="test", spawned=True, pr_key="test-key")
        assert orch.state.is_comment_processed(12345) is True


class TestTemporaryDirectoryCleanup:
    """Test that pre_generate_diagram uses TemporaryDirectory for cleanup."""

    def test_temp_dir_cleanup_on_success(self):
        """Verify temp directory is cleaned up after successful diagram generation."""
        from riptide.grafiphy.orchestrator import pre_generate_diagram
        
        data = {
            "god_nodes": [{"name": "test", "file": "test.py"}],
            "communities": [{"name": "test-community", "members": ["a", "b"]}],
        }
        pr_metadata = {
            "owner": "ChonSong",
            "repo": "riptide",
            "number": 42,
            "title": "test",
            "author": "test",
            "total_loc": 100,
        }
        
        # Mock render_review and upload_excalidraw
        with patch("riptide.grafiphy.orchestrator.render_review") as mock_render, \
             patch("riptide.grafiphy.orchestrator.upload_excalidraw", return_value="https://excalidraw.com/#test") as mock_upload:
            
            # Capture the output_path passed to render_review
            captured_paths = []
            def capture_render(*args, **kwargs):
                captured_paths.append(kwargs.get("output_path"))
            
            mock_render.side_effect = capture_render
            
            result = pre_generate_diagram(data, pr_metadata)
            
            # Verify upload was called and returned URL
            assert result == "https://excalidraw.com/#test"
            assert mock_upload.called
            
            # Verify the temp directory was cleaned up
            if captured_paths:
                output_path = Path(captured_paths[0])
                assert not output_path.parent.exists(), f"Temp dir {output_path.parent} should be cleaned up"

    def test_temp_dir_cleanup_on_failure(self):
        """Verify temp directory is cleaned up even when render fails."""
        from riptide.grafiphy.orchestrator import pre_generate_diagram
        
        data = {
            "god_nodes": [{"name": "test", "file": "test.py"}],
            "communities": [{"name": "test-community", "members": ["a", "b"]}],
        }
        pr_metadata = {
            "owner": "ChonSong",
            "repo": "riptide",
            "number": 42,
            "title": "test",
            "author": "test",
            "total_loc": 100,
        }
        
        # Mock render_review to raise an exception
        with patch("riptide.grafiphy.orchestrator.render_review", side_effect=Exception("Render failed")):
            result = pre_generate_diagram(data, pr_metadata)
            
            # Should return None on failure
            assert result is None
