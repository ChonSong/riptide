#!/usr/bin/env python3
# riptide/state.py — single state store for all riptide bots.
# Merges orchestrator.StateStore (job tracking + dedup) + poller metadata.db
# (processed comment IDs + pending retries) into one SQLite schema.

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("riptide.state")

DEFAULT_DB_PATH = os.environ.get(
    "RIPTIDE_STATE_DB",
    str(Path.home() / ".local/share/riptide/state.db"),
)


class StateStore:
    """
    SQLite-backed state for tracking jobs, deliveries, and processed comments.

    WAL mode for concurrent reads/writes. Single shared connection per thread
    to avoid "database is locked" errors. Migrations run on every instantiation
    so schema changes don't require manual ALTER TABLE.
    """

    SCHEMA_VERSION = 2

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        # Ensure parent dir exists (poller was the only one doing this)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    # ── Connection ─────────────────────────────────────────────────────────────

    @property
    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path, timeout=30)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
            )
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            version = row[0] if row else 0

            conn.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    pr_number INTEGER,
                    tier TEXT,
                    status TEXT,
                    created_at REAL,
                    completed_at REAL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    received_at REAL
                )"""
            )
            # New in v2: replaces poller's metadata.db
            conn.execute(
                """CREATE TABLE IF NOT EXISTS processed_comments (
                    comment_id INTEGER PRIMARY KEY,
                    processed_at TEXT NOT NULL,
                    result TEXT,
                    pending_response TEXT
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_pr_status ON jobs (pr_number, status)"
            )

            if version < self.SCHEMA_VERSION:
                conn.execute("DELETE FROM schema_version")
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (self.SCHEMA_VERSION,),
                )
                conn.commit()

    # ── Deliveries (Companion dedup) ───────────────────────────────────────────

    def reserve_delivery(self, delivery_id: str) -> bool:
        """Try to reserve a delivery ID. Returns False if already processed."""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            try:
                conn.execute(
                    "INSERT INTO deliveries (delivery_id, received_at) VALUES (?, ?)",
                    (delivery_id, time.time()),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    # ── Jobs (Deepthink / Fixer tracking) ─────────────────────────────────────

    def create_job(self, job_id: str, pr_number: int, tier: str):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (id, pr_number, tier, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                (job_id, pr_number, tier, time.time()),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_complete(self, job_id: str):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            conn.execute(
                "UPDATE jobs SET status='complete', completed_at=? WHERE id=?",
                (time.time(), job_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_failed(self, job_id: str):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            conn.execute(
                "UPDATE jobs SET status='failed', completed_at=? WHERE id=?",
                (time.time(), job_id),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _escape_like(pattern: str) -> str:
        return pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def has_pending_job(self, name_prefix: str) -> bool:
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            cutoff = time.time() - 7200  # 2-hour TTL
            escaped = f"{self._escape_like(name_prefix)}-%"
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE id LIKE ? ESCAPE '\\' AND status='pending' AND created_at > ?",
                (escaped, cutoff),
            ).fetchone()
            return row[0] > 0

    def reserve_job(self, job_id: str, pr_number: int, tier: str, name_prefix: str) -> bool:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            conn.execute("BEGIN IMMEDIATE")
            cutoff = time.time() - 7200
            escaped = f"{self._escape_like(name_prefix)}-%"
            conn.execute(
                """INSERT INTO jobs (id, pr_number, tier, status, created_at)
                   SELECT ?, ?, ?, 'pending', ?
                   WHERE NOT EXISTS (
                       SELECT 1 FROM jobs WHERE id LIKE ? ESCAPE '\\' AND status='pending' AND created_at > ?
                   )""",
                (job_id, pr_number, tier, time.time(), escaped, cutoff),
            )
            conn.commit()
            return conn.total_changes > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def cleanup_stale_pending(self, max_age_seconds: int = 7200):
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            cutoff = time.time() - max_age_seconds
            conn.execute(
                "UPDATE jobs SET status='failed', completed_at=? WHERE status='pending' AND created_at < ?",
                (time.time(), cutoff),
            )
            conn.commit()

    def get_job_status(self, pr_number: int) -> Optional[dict]:
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            row = conn.execute(
                "SELECT id, tier, status, created_at, completed_at FROM jobs "
                "WHERE pr_number=? ORDER BY created_at DESC LIMIT 1",
                (pr_number,),
            ).fetchone()
            if row:
                return {
                    "id": row[0],
                    "tier": row[1],
                    "status": row[2],
                    "created_at": row[3],
                    "completed_at": row[4],
                }
            return None

    # ── Processed comments (Poller dedup + retry) ──────────────────────────────

    def is_comment_processed(self, comment_id: int) -> bool:
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            row = conn.execute(
                "SELECT 1 FROM processed_comments WHERE comment_id = ?",
                (comment_id,),
            ).fetchone()
            return row is not None

    def mark_comment_processed(self, comment_id: int, result: str = "", pending_response: str = ""):
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(
                "INSERT OR REPLACE INTO processed_comments (comment_id, processed_at, result, pending_response) "
                "VALUES (?, ?, ?, ?)",
                (comment_id, datetime.now(timezone.utc).isoformat(), result[:200], pending_response),
            )
            conn.commit()

    def get_pending_response(self, comment_id: int) -> Optional[str]:
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            row = conn.execute(
                "SELECT pending_response FROM processed_comments WHERE comment_id = ?",
                (comment_id,),
            ).fetchone()
            return row[0] if row and row[0] else None

    def has_pending_fix_for_pr(self, pr_key: str) -> bool:
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            like_pattern = '%"spawned"%'
            rows = conn.execute(
                "SELECT result FROM processed_comments WHERE result LIKE ?",
                (like_pattern,),
            ).fetchall()
            for (result_str,) in rows:
                try:
                    import json
                    data = json.loads(result_str)
                    if data.get("pr_key") == pr_key:
                        return True
                except (json.JSONDecodeError, TypeError):
                    if pr_key in result_str:
                        return True
            return False


# Module-level convenience (poller's old DB path for migration)
POLLER_DB_PATH = Path.home() / ".local/share/riptide/metadata.db"


def migrate_poller_comments(target: StateStore):
    """One-time migration from poller's metadata.db into the new state.db."""
    if not POLLER_DB_PATH.exists():
        return
    try:
        with sqlite3.connect(str(POLLER_DB_PATH)) as src:
            rows = src.execute(
                "SELECT comment_id, processed_at, result, pending_response FROM poller_processed_comments"
            ).fetchall()
        for comment_id, processed_at, result, pending_response in rows:
            with sqlite3.connect(target.db_path) as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO processed_comments (comment_id, processed_at, result, pending_response) VALUES (?, ?, ?, ?)",
                    (comment_id, processed_at, result, pending_response),
                )
                conn.commit()
        log.info(f"Migrated {len(rows)} comment records from {POLLER_DB_PATH}")
    except Exception as e:
        log.warning(f"Poller migration skipped: {e}")
