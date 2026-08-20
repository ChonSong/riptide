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

    Connection pattern:
    - Per-thread persistent connection via self._local (avoids "database is locked"
      under concurrent access from webhook threads).
    - WAL mode + busy_timeout=5000 for concurrent reads/writes.
    - All methods use _get_conn() which lazily creates the per-thread connection
      and applies PRAGMAs exactly once per thread.

    Schema versioning:
    - Migrations run on every instantiation so schema changes don't require
      manual ALTER TABLE.
    - Bump SCHEMA_VERSION when adding columns/tables.

    Timestamp conventions:
    - jobs.deliveries: REAL (epoch float) — used for TTL math (cutoff = time.time() - N).
    - processed_comments.processed_at: TEXT (ISO 8601 UTC) — human-readable for debugging
      and stable across timezones. Consumers must parse with datetime.fromisoformat().
    """

    SCHEMA_VERSION = 6

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        # Ensure parent dir exists (poller was the only one doing this)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    # ── Connection ─────────────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """
        Return the per-thread persistent connection.

        Lazily creates a new connection on first access per thread. Applies WAL
        mode and busy_timeout so concurrent threads don't hit "database is locked".
        The connection lives for the thread's lifetime — SQLite's WAL mode handles
        concurrent readers + single writer efficiently without per-call open/close.
        """
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path, timeout=30)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_db(self):
        conn = self._get_conn()
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
            """CREATE TABLE IF NOT EXISTS work_queue (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at REAL,
                completed_at REAL,
                error TEXT,
                traceback TEXT
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_work_queue_kind_status ON work_queue (kind, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_work_queue_status_created_at ON work_queue (status, created_at)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS deliveries (
                delivery_id TEXT PRIMARY KEY,
                received_at REAL
            )"""
        )
        # v2: replaces poller's metadata.db
        conn.execute(
            """CREATE TABLE IF NOT EXISTS processed_comments (
                comment_id INTEGER PRIMARY KEY,
                processed_at TEXT NOT NULL,
                result TEXT,
                pending_response TEXT
            )"""
        )
        # v3: structured flag for spawned fixes (replaces fragile LIKE '%"spawned"'%' search)
        if version < 3:
            try:
                conn.execute(
                    "ALTER TABLE processed_comments ADD COLUMN spawned INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_processed_comments_spawned ON processed_comments (spawned)"
            )
        # v4: pr_key column for exact lookup of spawned fixes per PR
        if version < 4:
            try:
                conn.execute(
                    "ALTER TABLE processed_comments ADD COLUMN pr_key TEXT"
                )
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_processed_comments_pr_key ON processed_comments (pr_key)"
            )
        # v5: pr_heuristics — single authority for per-PR process heuristics
        # (skip/resume, last commented SHA, reviewed-at timestamp for cooldown).
        # Replaces companion_skip.json + deepthink_acted_prs.json so every bot
        # answers "already reviewed / skipped / stale?" from ONE store.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS pr_heuristics (
                pr_key TEXT PRIMARY KEY,
                skip INTEGER NOT NULL DEFAULT 0,
                last_sha TEXT,
                reviewed_at TEXT,
                tier1_comment_id INTEGER
            )"""
        )

        # v6: tier1_comment_id — the canonical Tier-1 comment thread per PR so
        # re-syncs PATCH in place instead of re-POSTing, and later stages /
        # commands can find the thread. ALTER is required for DBs created at
        # v5 (CREATE IF NOT EXISTS won't add the column to an existing table).
        cols = {row[1] for row in conn.execute("PRAGMA table_info(pr_heuristics)").fetchall()}
        if "tier1_comment_id" not in cols:
            conn.execute(
                "ALTER TABLE pr_heuristics ADD COLUMN tier1_comment_id INTEGER"
            )
            log.info("pr_heuristics: added tier1_comment_id column (v6)")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_pr_status ON jobs (pr_number, status)"
        )

        # One-time migration from poller's metadata.db (if it exists)
        # Run BEFORE schema version update so migration failures remain retryable
        if version < 2:
            self._migrate_poller_comments()

        # Update schema version only after all migrations succeed
        if version < self.SCHEMA_VERSION:
            conn.execute("DELETE FROM schema_version")
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (self.SCHEMA_VERSION,),
            )
        conn.commit()

    def _migrate_poller_comments(self):
        """One-time migration from poller's metadata.db into the new state.db."""
        if not POLLER_DB_PATH.exists():
            return
        try:
            with sqlite3.connect(str(POLLER_DB_PATH)) as src:
                table_check = src.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='poller_processed_comments'"
                ).fetchone()
                if not table_check:
                    log.info(f"No poller_processed_comments table found in {POLLER_DB_PATH}")
                    return
                
                old_columns = {row[1] for row in src.execute("PRAGMA table_info(poller_processed_comments)").fetchall()}
                has_pending = "pending_response" in old_columns
                
                if has_pending:
                    rows = src.execute(
                        "SELECT comment_id, processed_at, result, pending_response FROM poller_processed_comments"
                    ).fetchall()
                else:
                    rows = src.execute(
                        "SELECT comment_id, processed_at, result, '' FROM poller_processed_comments"
                    ).fetchall()
            
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                for comment_id, processed_at, result, pending_response in rows:
                    conn.execute(
                        "INSERT OR IGNORE INTO processed_comments (comment_id, processed_at, result, pending_response) VALUES (?, ?, ?, ?)",
                        (comment_id, processed_at, result, pending_response),
                    )
                conn.commit()
                log.info(f"Migrated {len(rows)} comment records from {POLLER_DB_PATH}")
            except Exception:
                conn.rollback()
                raise
        except Exception as e:
            log.warning(f"Poller migration skipped: {e}")

    # ── Deliveries (Companion dedup) ───────────────────────────────────────────

    def reserve_delivery(self, delivery_id: str) -> bool:
        """Try to reserve a delivery ID. Returns False if already processed."""
        conn = self._get_conn()
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
        conn = self._get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO jobs (id, pr_number, tier, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (job_id, pr_number, tier, time.time()),
        )
        conn.commit()

    def mark_complete(self, job_id: str):
        conn = self._get_conn()
        conn.execute(
            "UPDATE jobs SET status='complete', completed_at=? WHERE id=?",
            (time.time(), job_id),
        )
        conn.commit()

    def mark_failed(self, job_id: str):
        conn = self._get_conn()
        conn.execute(
            "UPDATE jobs SET status='failed', completed_at=? WHERE id=?",
            (time.time(), job_id),
        )
        conn.commit()

    # ── Work Queue (durable work items) ──────────────────────────────────────

    def enqueue_work(self, work_id: str, kind: str, payload: dict) -> bool:
        """Enqueue a work item. Returns True if inserted, False if duplicate."""
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT OR IGNORE INTO work_queue (id, kind, payload, status, created_at)
                   VALUES (?, ?, ?, 'pending', ?)""",
                (work_id, kind, json.dumps(payload), time.time()),
            )
            conn.commit()
            return conn.execute("SELECT changes()").fetchone()[0] > 0
        except Exception:
            conn.rollback()
            raise

    def get_pending_work(self, kind: str) -> list[dict]:
        """Get pending work items of a given kind."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT id, kind, payload, created_at FROM work_queue
               WHERE kind = ? AND status = 'pending'
               ORDER BY created_at ASC""",
            (kind,),
        ).fetchall()
        return [
            {"id": r[0], "kind": r[1], "payload": json.loads(r[2]), "created_at": r[3]}
            for r in rows
        ]

    def complete_work(self, work_id: str, error: str = None, traceback_str: str = None) -> bool:
        """Mark work as completed/failed. Returns True if row was transitioned."""
        conn = self._get_conn()
        status = "failed" if error else "completed"
        conn.execute(
            "UPDATE work_queue SET status=?, completed_at=?, error=?, traceback=? WHERE id=? AND status='pending'",
            (status, time.time(), error, traceback_str, work_id),
        )
        conn.commit()
        return conn.execute("SELECT changes()").fetchone()[0] > 0

    def cleanup_stale_work(self, max_age_seconds: int = 7200):
        """Mark stale pending work as failed."""
        conn = self._get_conn()
        cutoff = time.time() - max_age_seconds
        conn.execute(
            "UPDATE work_queue SET status='failed', completed_at=?, error='stale' WHERE status='pending' AND created_at < ?",
            (time.time(), cutoff),
        )
        conn.commit()

    def recover_pending_work(self) -> list[dict]:
        """Recover pending work after process restart.
        
        Atomically claims recently-pending items by transitioning to 'recovering'.
        Items older than 5 minutes are marked as stale.
        Returns items successfully claimed for recovery.
        """
        conn = self._get_conn()
        now = time.time()
        recovery_cutoff = now - 300  # 5 minutes
        
        # Mark all pending items older than recovery window as stale
        conn.execute(
            "UPDATE work_queue SET status='failed', completed_at=?, error='stale' WHERE status='pending' AND created_at < ?",
            (now, recovery_cutoff),
        )
        conn.commit()
        
        # Get recently-pending items
        rows = conn.execute(
            """SELECT id, kind, payload, created_at FROM work_queue
               WHERE status='pending' AND created_at > ?
               ORDER BY created_at ASC""",
            (recovery_cutoff,),
        ).fetchall()
        
        # Atomically claim each item
        claimed = []
        for row in rows:
            work_id = row[0]
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE work_queue SET status='recovering' WHERE id=? AND status='pending'",
                (work_id,),
            )
            conn.commit()
            if conn.total_changes > 0:
                claimed.append({
                    "id": row[0],
                    "kind": row[1],
                    "payload": json.loads(row[2]),
                    "created_at": row[3],
                })
        
        return claimed

    @staticmethod
    def _escape_like(pattern: str) -> str:
        return pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def has_pending_job(self, name_prefix: str) -> bool:
        conn = self._get_conn()
        cutoff = time.time() - 7200  # 2-hour TTL
        escaped = f"{self._escape_like(name_prefix)}-%"
        row = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE id LIKE ? ESCAPE '\\' AND status='pending' AND created_at > ?",
            (escaped, cutoff),
        ).fetchone()
        return row[0] > 0

    def reserve_job(self, job_id: str, pr_number: int, tier: str, name_prefix: str) -> bool:
        conn = self._get_conn()
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
            # Use changes() to deterministically detect whether this INSERT added a row.
            # conn.total_changes is cumulative across the connection and can give
            # false positives; changes() returns rows modified by the last statement only.
            row_count = conn.execute("SELECT changes()").fetchone()[0]
            return row_count > 0
        except Exception:
            conn.rollback()
            raise

    def cleanup_stale_pending(self, max_age_seconds: int = 7200):
        conn = self._get_conn()
        cutoff = time.time() - max_age_seconds
        conn.execute(
            "UPDATE jobs SET status='failed', completed_at=? WHERE status='pending' AND created_at < ?",
            (time.time(), cutoff),
        )
        conn.commit()

    def get_job_status(self, pr_number: int) -> Optional[dict]:
        conn = self._get_conn()
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
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM processed_comments WHERE comment_id = ?",
            (comment_id,),
        ).fetchone()
        return row is not None

    def mark_comment_processed(
        self,
        comment_id: int,
        result: str = "",
        pending_response: str = "",
        spawned: bool = False,
        pr_key: str = "",
    ):
        """
        Record a processed comment.

        result is stored in full (no truncation) — it's a TEXT column, SQLite handles
        arbitrary length. The spawned flag and pr_key are set separately so
        has_pending_fix_for_pr can query indexed columns instead of parsing JSON.
        """
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO processed_comments (comment_id, processed_at, result, pending_response, spawned, pr_key) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                comment_id,
                datetime.now(timezone.utc).isoformat(),
                result,  # full JSON, no truncation
                pending_response,
                1 if spawned else 0,
                pr_key,
            ),
        )
        conn.commit()

    def get_pending_response(self, comment_id: int) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT pending_response FROM processed_comments WHERE comment_id = ?",
            (comment_id,),
        ).fetchone()
        return row[0] if row and row[0] else None

    def has_pending_fix_for_pr(self, pr_key: str) -> bool:
        """
        Check if any comment on this PR already spawned a fix.

        Uses the structured `pr_key` column (indexed) for exact lookup instead of
        LIKE search over JSON strings. Falls back to JSON parsing for legacy rows
        that predate the pr_key column (migration sets pr_key='' for all existing rows).
        """
        conn = self._get_conn()
        # Fast path: exact match on indexed pr_key column
        row = conn.execute(
            "SELECT 1 FROM processed_comments WHERE spawned = 1 AND pr_key = ? LIMIT 1",
            (pr_key,),
        ).fetchone()
        if row is not None:
            return True
        # Fallback: legacy rows without pr_key — parse JSON
        rows = conn.execute(
            "SELECT result FROM processed_comments WHERE pr_key = '' AND result LIKE '%\"spawned\"%'"
        ).fetchall()
        for (result_str,) in rows:
            try:
                data = json.loads(result_str)
                if data.get("pr_key") == pr_key and data.get("result") == "spawned":
                    return True
            except (json.JSONDecodeError, TypeError):
                if pr_key in result_str and "spawned" in result_str:
                    return True
        return False

    # ── PR heuristics (WS-3 Stage 0: single authority for skip/last_sha/cooldown) ──

    def get_pr_heuristics(self, pr_key: str) -> dict:
        """Return per-PR process heuristics, or empty defaults when unknown."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT skip, last_sha, reviewed_at FROM pr_heuristics WHERE pr_key = ?",
            (pr_key,),
        ).fetchone()
        if row is None:
            return {"skip": False, "last_sha": None, "reviewed_at": None}
        return {
            "skip": bool(row[0]),
            "last_sha": row[1],
            "reviewed_at": row[2],
        }

    def set_pr_skip(self, pr_key: str, skip: bool):
        """Set the user-controlled skip flag for a PR."""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO pr_heuristics (pr_key, skip) VALUES (?, ?) "
            "ON CONFLICT(pr_key) DO UPDATE SET skip = excluded.skip",
            (pr_key, 1 if skip else 0),
        )
        conn.commit()

    def set_pr_last_sha(self, pr_key: str, last_sha: Optional[str]):
        """Record the commit SHA the bot last commented on for a PR."""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO pr_heuristics (pr_key, last_sha) VALUES (?, ?) "
            "ON CONFLICT(pr_key) DO UPDATE SET last_sha = excluded.last_sha",
            (pr_key, last_sha),
        )
        conn.commit()

    def set_pr_reviewed_at(self, pr_key: str, reviewed_at: Optional[str]):
        """Record when the PR was last reviewed (ISO 8601 UTC) for cooldown checks."""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO pr_heuristics (pr_key, reviewed_at) VALUES (?, ?) "
            "ON CONFLICT(pr_key) DO UPDATE SET reviewed_at = excluded.reviewed_at",
            (pr_key, reviewed_at),
        )
        conn.commit()

    def get_pr_tier1_comment_id(self, pr_key: str) -> Optional[int]:
        """Return the canonical Tier-1 comment id for a PR, or None."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT tier1_comment_id FROM pr_heuristics WHERE pr_key = ?",
            (pr_key,),
        ).fetchone()
        return row[0] if row else None

    def set_pr_tier1_comment_id(self, pr_key: str, comment_id: Optional[int]):
        """Persist the canonical Tier-1 comment thread for a PR.

        Stage 2: re-syncs PATCH this comment in place instead of re-POSTing,
        and later stages / commands find the thread here.
        """
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO pr_heuristics (pr_key, tier1_comment_id) VALUES (?, ?) "
            "ON CONFLICT(pr_key) DO UPDATE SET tier1_comment_id = excluded.tier1_comment_id",
            (pr_key, comment_id),
        )
        conn.commit()


# Module-level convenience (poller's old DB path for migration)
POLLER_DB_PATH = Path.home() / ".local/share/riptide/metadata.db"
