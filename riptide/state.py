#!/usr/bin/env python3
# riptide/state.py — single state store for all riptide bots.

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("riptide.state")

DEFAULT_DB_PATH = os.environ.get(
    "RIPTIDE_STATE_DB",
    str(Path.home() / ".local/share/riptide/state.db"),
)


class StateStore:
    """SQLite-backed state for tracking jobs, deliveries, and processed comments."""

    SCHEMA_VERSION = 7

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock_path = db_path + ".lock"
        Path(self._lock_path).touch()
        self._lock_fd = open(self._lock_path, "w")
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path, timeout=30)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=10000")
        return self._local.conn

    def _acquire_lock(self):
        fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_EX)

    def _release_lock(self):
        fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        version = row[0] if row else 0

        conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, pr_number INTEGER, tier TEXT, status TEXT,
            created_at REAL, completed_at REAL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS deliveries (
            delivery_id TEXT PRIMARY KEY, received_at REAL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS processed_comments (
            comment_id INTEGER PRIMARY KEY, processed_at TEXT NOT NULL,
            result TEXT, pending_response TEXT)""")

        if version < 3:
            try:
                conn.execute("ALTER TABLE processed_comments ADD COLUMN spawned INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise
        if version < 4:
            try:
                conn.execute("ALTER TABLE processed_comments ADD COLUMN pr_key TEXT")
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise
        conn.execute("""CREATE TABLE IF NOT EXISTS pr_heuristics (
            pr_key TEXT PRIMARY KEY, skip INTEGER NOT NULL DEFAULT 0,
            last_sha TEXT, reviewed_at TEXT, tier1_comment_id INTEGER)""")

        # v7: checkbox trigger dedup
        conn.execute("""CREATE TABLE IF NOT EXISTS checkbox_triggers (
            pr_key TEXT NOT NULL, label TEXT NOT NULL, triggered_at REAL NOT NULL,
            PRIMARY KEY (pr_key, label))""")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_pr_status ON jobs (pr_number, status)")

        if version < 2:
            self._migrate_poller_comments()

        if version < self.SCHEMA_VERSION:
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (self.SCHEMA_VERSION,))
        conn.commit()

    def _migrate_poller_comments(self):
        pass

    def reserve_delivery(self, delivery_id: str) -> bool:
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute("INSERT INTO deliveries (delivery_id, received_at) VALUES (?, ?)",
                         (delivery_id, time.time()))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                log.warning(f"Database locked during delivery reservation: {delivery_id}")
                return False
            raise
        finally:
            self._release_lock()

    def create_job(self, job_id: str, pr_number: int, tier: str):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute("INSERT OR IGNORE INTO jobs (id, pr_number, tier, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                         (job_id, pr_number, tier, time.time()))
            conn.commit()
        finally:
            self._release_lock()

    def mark_complete(self, job_id: str):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute("UPDATE jobs SET status='complete', completed_at=? WHERE id=?",
                         (time.time(), job_id))
            conn.commit()
        finally:
            self._release_lock()

    def mark_failed(self, job_id: str):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute("UPDATE jobs SET status='failed', completed_at=? WHERE id=?",
                         (time.time(), job_id))
            conn.commit()
        finally:
            self._release_lock()

    @staticmethod
    def _escape_like(pattern: str) -> str:
        return pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def has_pending_job(self, name_prefix: str) -> bool:
        conn = self._get_conn()
        cutoff = time.time() - 7200
        escaped = f"{self._escape_like(name_prefix)}-%"
        row = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE id LIKE ? ESCAPE '\\' AND status='pending' AND created_at > ?",
            (escaped, cutoff)).fetchone()
        return row[0] > 0

    def reserve_job(self, job_id: str, pr_number: int, tier: str, name_prefix: str) -> bool:
        conn = self._get_conn()
        self._acquire_lock()
        try:
            cutoff = time.time() - 7200
            escaped = f"{self._escape_like(name_prefix)}-%"
            conn.execute(
                """INSERT INTO jobs (id, pr_number, tier, status, created_at)
                   SELECT ?, ?, ?, 'pending', ?
                   WHERE NOT EXISTS (
                       SELECT 1 FROM jobs WHERE id LIKE ? ESCAPE '\\' AND status='pending' AND created_at > ?
                   )""",
                (job_id, pr_number, tier, time.time(), escaped, cutoff))
            conn.commit()
            return conn.execute("SELECT changes()").fetchone()[0] > 0
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                log.warning(f"Database locked during job reservation: {name_prefix}")
                return False
            raise
        finally:
            self._release_lock()

    def cleanup_stale_pending(self, max_age_seconds: int = 7200):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            cutoff = time.time() - max_age_seconds
            conn.execute("UPDATE jobs SET status='failed', completed_at=? WHERE status='pending' AND created_at < ?",
                         (time.time(), cutoff))
            conn.commit()
        finally:
            self._release_lock()

    def get_job_status(self, pr_number: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id, tier, status, created_at, completed_at FROM jobs WHERE pr_number=? ORDER BY created_at DESC LIMIT 1",
            (pr_number,)).fetchone()
        if row:
            return {"id": row[0], "tier": row[1], "status": row[2], "created_at": row[3], "completed_at": row[4]}
        return None

    def is_comment_processed(self, comment_id: int) -> bool:
        conn = self._get_conn()
        row = conn.execute("SELECT 1 FROM processed_comments WHERE comment_id = ?", (comment_id,)).fetchone()
        return row is not None

    def mark_comment_processed(self, comment_id: int, result: str = "", pending_response: str = "",
                                spawned: bool = False, pr_key: str = ""):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO processed_comments (comment_id, processed_at, result, pending_response, spawned, pr_key) VALUES (?, ?, ?, ?, ?, ?)",
                (comment_id, datetime.now(timezone.utc).isoformat(), result, pending_response, 1 if spawned else 0, pr_key))
            conn.commit()
        finally:
            self._release_lock()

    def get_pending_response(self, comment_id: int) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute("SELECT pending_response FROM processed_comments WHERE comment_id = ?", (comment_id,)).fetchone()
        return row[0] if row and row[0] else None

    def has_pending_fix_for_pr(self, pr_key: str) -> bool:
        conn = self._get_conn()
        row = conn.execute("SELECT 1 FROM processed_comments WHERE spawned = 1 AND pr_key = ? LIMIT 1", (pr_key,)).fetchone()
        return row is not None

    def get_pr_heuristics(self, pr_key: str) -> dict:
        conn = self._get_conn()
        row = conn.execute("SELECT skip, last_sha, reviewed_at FROM pr_heuristics WHERE pr_key = ?", (pr_key,)).fetchone()
        if row is None:
            return {"skip": False, "last_sha": None, "reviewed_at": None}
        return {"skip": bool(row[0]), "last_sha": row[1], "reviewed_at": row[2]}

    def set_pr_skip(self, pr_key: str, skip: bool):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute("INSERT INTO pr_heuristics (pr_key, skip) VALUES (?, ?) ON CONFLICT(pr_key) DO UPDATE SET skip = excluded.skip",
                         (pr_key, 1 if skip else 0))
            conn.commit()
        finally:
            self._release_lock()

    def set_pr_last_sha(self, pr_key: str, last_sha: Optional[str]):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute("INSERT INTO pr_heuristics (pr_key, last_sha) VALUES (?, ?) ON CONFLICT(pr_key) DO UPDATE SET last_sha = excluded.last_sha",
                         (pr_key, last_sha))
            conn.commit()
        finally:
            self._release_lock()

    def set_pr_reviewed_at(self, pr_key: str, reviewed_at: Optional[str]):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute("INSERT INTO pr_heuristics (pr_key, reviewed_at) VALUES (?, ?) ON CONFLICT(pr_key) DO UPDATE SET reviewed_at = excluded.reviewed_at",
                         (pr_key, reviewed_at))
            conn.commit()
        finally:
            self._release_lock()

    def get_pr_tier1_comment_id(self, pr_key: str) -> Optional[int]:
        conn = self._get_conn()
        row = conn.execute("SELECT tier1_comment_id FROM pr_heuristics WHERE pr_key = ?", (pr_key,)).fetchone()
        return row[0] if row else None

    def set_pr_tier1_comment_id(self, pr_key: str, comment_id: Optional[int]):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute("INSERT INTO pr_heuristics (pr_key, tier1_comment_id) VALUES (?, ?) ON CONFLICT(pr_key) DO UPDATE SET tier1_comment_id = excluded.tier1_comment_id",
                         (pr_key, comment_id))
            conn.commit()
        finally:
            self._release_lock()

    def get_last_checkbox_trigger(self, pr_key: str, label: str) -> Optional[float]:
        conn = self._get_conn()
        row = conn.execute("SELECT triggered_at FROM checkbox_triggers WHERE pr_key = ? AND label = ?",
                           (pr_key, label)).fetchone()
        return row[0] if row else None

    def set_last_checkbox_trigger(self, pr_key: str, label: str, ts: float):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute("INSERT OR REPLACE INTO checkbox_triggers (pr_key, label, triggered_at) VALUES (?, ?, ?)",
                         (pr_key, label, ts))
            conn.commit()
        finally:
            self._release_lock()

    def cleanup_stale_checkbox_triggers(self, max_age_seconds: int = 7200):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            cutoff = time.time() - max_age_seconds
            conn.execute("DELETE FROM checkbox_triggers WHERE triggered_at < ?", (cutoff,))
            conn.commit()
        finally:
            self._release_lock()


# Module-level convenience
POLLER_DB_PATH = Path.home() / ".local/share/riptide/metadata.db"
