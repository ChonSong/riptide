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

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger("riptide.state")


def _metadata_json(metadata) -> Optional[str]:
    """Serialise ``review_memory.metadata`` exactly once.

    ``review_memory.store_review_outcome`` already encodes its dict, so the old
    ``json.dumps(metadata)`` here encoded it a second time and the column held a
    JSON *string literal* instead of the object it was given — reading it back
    yielded a string, not the dict the caller recorded. Accept either form so the
    column is always readable JSON.
    """
    if not metadata:
        return None
    if isinstance(metadata, str):
        return metadata
    return json.dumps(metadata)

# ── Constants ────────────────────────────────────────────────────────────────

# Deliveries stuck in 'processing' longer than this are considered stale
# (crashed webhook) and become eligible for re-reservation.
DELIVERY_STALE_TTL = 300  # 5 minutes
# Fast path: webhook handler — low latency matters, GitHub has 10s timeout
retry_db_fast = retry(
    retry=retry_if_exception_type(sqlite3.OperationalError),
    wait=wait_exponential(multiplier=0.2, min=0.2, max=1.0),
    stop=stop_after_attempt(3),
    reraise=True,
)

# Slow path: cron jobs / background cleanup — can afford longer waits
retry_db_background = retry(
    retry=retry_if_exception_type(sqlite3.OperationalError),
    wait=wait_exponential(multiplier=1.0, min=2.0, max=10.0),
    stop=stop_after_attempt(5),
    reraise=True,
)

DEFAULT_DB_PATH = os.environ.get(
    "RIPTIDE_STATE_DB",
    str(Path.home() / ".local/share/riptide/state.db"),
)

# Shared TTL for fix activity (has_running_fix, cleanup_stale_queue_items, jobs cutoff)
FIX_TTL_SECONDS = 7200  # 2 hours


class StateStore:
    """SQLite-backed state for tracking jobs, deliveries, and processed comments."""

    SCHEMA_VERSION = 10

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
            delivery_id TEXT PRIMARY KEY, received_at REAL, status TEXT NOT NULL DEFAULT 'processing')""")
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

        # v6: tier1_comment_id — the canonical Tier-1 comment thread per PR so
        # re-syncs PATCH in place instead of re-POSTing. The ALTER is required
        # for DBs whose pr_heuristics was created at v5, and for any DB whose
        # version row drifted ahead of its table: CREATE TABLE IF NOT EXISTS
        # never adds a column to an existing table.
        heuristics_cols = {row[1] for row in conn.execute("PRAGMA table_info(pr_heuristics)").fetchall()}
        if "tier1_comment_id" not in heuristics_cols:
            conn.execute("ALTER TABLE pr_heuristics ADD COLUMN tier1_comment_id INTEGER")
            log.info("pr_heuristics: added tier1_comment_id column (v6)")

        # v7: checkbox trigger dedup
        conn.execute("""CREATE TABLE IF NOT EXISTS checkbox_triggers (
            pr_key TEXT NOT NULL, label TEXT NOT NULL, triggered_at REAL NOT NULL,
            PRIMARY KEY (pr_key, label))""")

        # v7: review_memory — persist review outcomes per PR for context injection,
        # and review_profiles — per-repo aggregate stats for common-finding
        # patterns. These CREATE statements were dropped from init() by the
        # fa99b25 merge while the queries below (store_review_outcome,
        # get_memory_context) survived, so a database created since then has had
        # no review_memory table at all. IF NOT EXISTS keeps this idempotent for
        # existing databases, which were created while the statements were still
        # present.
        conn.execute("""CREATE TABLE IF NOT EXISTS review_memory (
            id TEXT PRIMARY KEY,
            pr_key TEXT NOT NULL,
            pr_number INTEGER,
            owner TEXT,
            repo TEXT,
            head_sha TEXT,
            findings_count INTEGER,
            critical_count INTEGER,
            warning_count INTEGER,
            verdict TEXT,
            user_feedback INTEGER,
            created_at TEXT,
            metadata TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS review_profiles (
            repo TEXT PRIMARY KEY,
            total_reviews INTEGER DEFAULT 0,
            common_findings TEXT DEFAULT '[]',
            last_review_at TEXT,
            updated_at TEXT
        )""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_memory_pr_key ON review_memory (pr_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_memory_repo ON review_memory (repo)"
        )

        # v8: durable work queue with PID-based recovery
        conn.execute("""CREATE TABLE IF NOT EXISTS work_queue (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL,
            completed_at REAL,
            error TEXT,
            traceback TEXT,
            pid INTEGER,
            attempts INTEGER NOT NULL DEFAULT 0
        )""")

        # v9: fix queue — serialized on-demand `@riptide-bot fix` requests.
        # Idempotent, so it is safe to run on every startup against an existing
        # production DB (creates the table, adds owner/repo when missing).
        self.init_fix_queue()

        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_pr_status ON jobs (pr_number, status)")

        if version < 2:
            self._migrate_poller_comments()

        if version < 8:
            try:
                conn.execute("ALTER TABLE work_queue ADD COLUMN pid INTEGER")
                conn.execute("ALTER TABLE work_queue ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise
            try:
                conn.execute("ALTER TABLE deliveries ADD COLUMN status TEXT NOT NULL DEFAULT 'processing'")
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise

        # v10: diagram_assets — multi-session architecture (see PLAN-multi-session-architecture.md).
        # The diagram stage persists the raw .excalidraw JSON (gzipped) plus the
        # rendered PNG (gzipped) so a subsequent push can apply a delta patch
        # instead of re-rendering from scratch. The (pr_id, base_sha, render_kind)
        # UNIQUE means re-renders overwrite; the delta_of FK gives the prior
        # scene by chain. No behaviour change for existing code paths — the
        # Conductor and Scribe do not read this table until Wave 2/3 land.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS diagram_assets (
                id              TEXT PRIMARY KEY,
                pr_id           TEXT NOT NULL,
                base_sha        TEXT NOT NULL,
                parent_sha      TEXT,
                element_count   INTEGER NOT NULL,
                render_kind     TEXT NOT NULL,
                scene_json      BLOB NOT NULL,
                rendered_png    BLOB,
                renderer_meta   TEXT,
                created_at      TEXT NOT NULL,
                created_by      TEXT,
                delta_of        TEXT,
                FOREIGN KEY (delta_of) REFERENCES diagram_assets(id)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_diagram_assets_pr_head "
            "ON diagram_assets (pr_id, base_sha)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_diagram_assets_unique "
            "ON diagram_assets (pr_id, base_sha, render_kind)"
        )

        if version < self.SCHEMA_VERSION:
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (self.SCHEMA_VERSION,))
        conn.commit()

        # Recover pending work on startup (durability contract)
        try:
            self.recover_pending_work()
        except Exception as e:
            log.warning(f"Startup recovery failed (non-fatal): {e}")

    def _migrate_poller_comments(self):
        """One-time migration from poller's metadata.db into the new state.db.

        The poller (riptide/poller.py) still tracks processed comment IDs in
        ~/.local/share/riptide/metadata.db; without this copy an upgrading
        install loses its dedup history and re-processes old fix commands.

        Called from _init_db() *before* schema_version is bumped (v<2), so a
        failure here must propagate: that leaves the version low and makes the
        migration retryable on the next start instead of silently dropping
        history. INSERT OR IGNORE keeps re-runs idempotent.
        """
        if not POLLER_DB_PATH.exists():
            return
        with sqlite3.connect(str(POLLER_DB_PATH)) as src:
            table_check = src.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='poller_processed_comments'"
            ).fetchone()
            if not table_check:
                log.info(f"No poller_processed_comments table found in {POLLER_DB_PATH}")
                return

            old_columns = {row[1] for row in src.execute("PRAGMA table_info(poller_processed_comments)").fetchall()}

            if "pending_response" in old_columns:
                rows = src.execute(
                    "SELECT comment_id, processed_at, result, pending_response FROM poller_processed_comments"
                ).fetchall()
            else:
                rows = src.execute(
                    "SELECT comment_id, processed_at, result, '' FROM poller_processed_comments"
                ).fetchall()

        conn = self._get_conn()
        try:
            for comment_id, processed_at, result, pending_response in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO processed_comments (comment_id, processed_at, result, pending_response) VALUES (?, ?, ?, ?)",
                    (comment_id, processed_at, result, pending_response),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            log.error(f"Poller migration from {POLLER_DB_PATH} failed; retrying on next start")
            raise
        log.info(f"Migrated {len(rows)} comment records from {POLLER_DB_PATH}")

    @retry_db_fast
    def reserve_delivery(self, delivery_id: str) -> bool:
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute("INSERT INTO deliveries (delivery_id, received_at) VALUES (?, ?)",
                         (delivery_id, time.time()))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Already exists — check if stale (crashed webhook)
            row = conn.execute(
                "SELECT status, received_at FROM deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            if row and row[0] == 'processing' and (time.time() - row[1]) > DELIVERY_STALE_TTL:
                # Stale — re-reserve by updating received_at
                conn.execute(
                    "UPDATE deliveries SET received_at = ?, status = 'processing' WHERE delivery_id = ?",
                    (time.time(), delivery_id),
                )
                conn.commit()
                return True
            return False
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                log.warning(f"Database locked during delivery reservation: {delivery_id}")
                return False
            raise
        finally:
            self._release_lock()

    @retry_db_fast
    def mark_delivery_done(self, delivery_id: str):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            cur = conn.execute(
                "UPDATE deliveries SET status = 'done' WHERE delivery_id = ?",
                (delivery_id,),
            )
            if cur.rowcount == 0:
                log.warning("mark_delivery_done: delivery %s not found", delivery_id)
            conn.commit()
        finally:
            self._release_lock()

    @retry_db_fast
    def mark_delivery_failed(self, delivery_id: str):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            cur = conn.execute(
                "UPDATE deliveries SET status = 'failed' WHERE delivery_id = ?",
                (delivery_id,),
            )
            if cur.rowcount == 0:
                log.warning(f"mark_delivery_failed: delivery {delivery_id} not found")
            conn.commit()
        finally:
            self._release_lock()

    @retry_db_fast
    def create_job(self, job_id: str, pr_number: int, tier: str):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute("INSERT OR IGNORE INTO jobs (id, pr_number, tier, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                         (job_id, pr_number, tier, time.time()))
            conn.commit()
        finally:
            self._release_lock()

    @retry_db_background
    def mark_complete(self, job_id: str):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute("UPDATE jobs SET status='complete', completed_at=? WHERE id=?",
                         (time.time(), job_id))
            conn.commit()
        finally:
            self._release_lock()

    @retry_db_background
    def mark_failed(self, job_id: str):
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute("UPDATE jobs SET status='failed', completed_at=? WHERE id=?",
                         (time.time(), job_id))
            conn.commit()
        finally:
            self._release_lock()

    # ── Work Queue (durable work items) ──────────────────────────────────────

    def enqueue_work(self, work_id: str, kind: str, payload: dict) -> bool:
        """Enqueue a work item. Returns True if inserted, False if duplicate."""
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT OR IGNORE INTO work_queue (id, kind, payload, status, created_at, pid)
                   VALUES (?, ?, ?, 'pending', ?, ?)""",
                (work_id, kind, json.dumps(payload), time.time(), os.getpid()),
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
        """Mark work as completed/failed. Returns True if row was transitioned.

        Uses WHERE id=? AND status IN ('pending', 'recovering') so that items
        being recovered can also be completed.
        """
        conn = self._get_conn()
        status = "failed" if error else "completed"
        conn.execute(
            "UPDATE work_queue SET status=?, completed_at=?, error=?, traceback=? WHERE id=? AND status IN ('pending', 'recovering')",
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

    def cleanup_old_deliveries(self, max_age_seconds: int = 86400):
        """Remove completed/folder deliveries older than max_age_seconds."""
        conn = self._get_conn()
        cutoff = time.time() - max_age_seconds
        self._acquire_lock()
        try:
            conn.execute(
                "DELETE FROM deliveries WHERE status IN ('done', 'failed') AND received_at < ?",
                (cutoff,),
            )
            conn.commit()
        finally:
            self._release_lock()

    def recover_pending_work(self) -> list[dict]:
        """Recover pending work after process restart.

        Atomically claims recently-pending items by transitioning to 'recovering'.
        Items older than the recovery window are marked as stale.
        Returns items successfully claimed for recovery.

        Tunable via env vars:
        - RIPTIDE_RECOVERY_WINDOW_SECONDS (default: 300 = 5 min)
        - RIPTIDE_RECOVERY_STALE_ERROR (default: 'startup_recovery')
        """
        conn = self._get_conn()
        now = time.time()
        recovery_window = int(os.environ.get("RIPTIDE_RECOVERY_WINDOW_SECONDS", "300"))
        recovery_cutoff = now - recovery_window
        stale_error = os.environ.get("RIPTIDE_RECOVERY_STALE_ERROR", "startup_recovery")

        # Mark all pending items older than recovery window as stale
        conn.execute(
            "UPDATE work_queue SET status='failed', completed_at=?, error=? WHERE status='pending' AND created_at < ?",
            (now, stale_error, recovery_cutoff),
        )
        conn.commit()

        # Get recently-pending items (exclude items from this PID — already running)
        rows = conn.execute(
            """SELECT id, kind, payload, created_at FROM work_queue
               WHERE status='pending' AND created_at > ? AND (pid != ? OR pid IS NULL)
               ORDER BY created_at ASC""",
            (recovery_cutoff, os.getpid()),
        ).fetchall()

        # Atomically claim each item
        claimed = []
        for row in rows:
            work_id = row[0]
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE work_queue SET status='recovering', pid=? WHERE id=? AND status='pending'",
                (os.getpid(), work_id),
            )
            conn.commit()
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
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
        cutoff = time.time() - 7200
        escaped = f"{self._escape_like(name_prefix)}-%"
        row = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE id LIKE ? ESCAPE '\\' AND status='pending' AND created_at > ?",
            (escaped, cutoff)).fetchone()
        return row[0] > 0

    @retry_db_background
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

    def list_pending_jobs(self, name_prefix: str) -> list[dict]:
        """Return pending jobs whose id starts with ``<name_prefix>-`` (newest first).

        Used to release reservations whose spawned cron job already finished:
        a reservation must not outlive the job it was made for.
        """
        conn = self._get_conn()
        escaped = f"{self._escape_like(name_prefix)}-%"
        rows = conn.execute(
            "SELECT id, pr_number, tier, created_at FROM jobs "
            "WHERE id LIKE ? ESCAPE '\\' AND status='pending' ORDER BY created_at DESC",
            (escaped,),
        ).fetchall()
        return [
            {"id": r[0], "pr_number": r[1], "tier": r[2], "created_at": r[3]}
            for r in rows
        ]

    def get_job_status(self, pr_number: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id, tier, status, created_at, completed_at FROM jobs WHERE pr_number=? ORDER BY created_at DESC LIMIT 1",
            (pr_number,)).fetchone()
        if row:
            return {"id": row[0], "tier": row[1], "status": row[2], "created_at": row[3], "completed_at": row[4]}
        return None

    # ── Fix Queue ─────────────────────────────────────────────────────────────
    # `@riptide-bot fix` is serialized globally: at most one fix session runs at
    # a time and extra requests wait here, FIFO by created_at (id as tiebreaker,
    # since multiple requests can land in the same clock tick).

    def init_fix_queue(self):
        """Idempotent: create fix_queue table and index if missing."""
        conn = self._get_conn()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS fix_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pr_number INTEGER NOT NULL,
                pr_key TEXT NOT NULL,
                description TEXT,
                commenter TEXT NOT NULL,
                installation_id INTEGER,
                owner TEXT,
                repo TEXT,
                created_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                started_at REAL,
                completed_at REAL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fix_queue_status ON fix_queue (status, created_at)"
        )
        # Migration: add missing columns if upgrading from older schema
        cols = [row[1] for row in conn.execute("PRAGMA table_info(fix_queue)").fetchall()]
        if "owner" not in cols:
            conn.execute("ALTER TABLE fix_queue ADD COLUMN owner TEXT")
        if "repo" not in cols:
            conn.execute("ALTER TABLE fix_queue ADD COLUMN repo TEXT")
        conn.commit()

    def enqueue_fix(self, pr_number: int, pr_key: str, commenter: str, description: str = "",
                    installation_id: int | None = None, owner: str = "", repo: str = "") -> int:
        """Add a fix request to the queue. Returns the queue row id."""
        self.init_fix_queue()
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO fix_queue (pr_number, pr_key, description, commenter, installation_id, owner, repo, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pr_number, pr_key, description, commenter, installation_id, owner, repo, time.time()),
        )
        conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def get_queue_length(self, pr_number: int, owner: str = "", repo: str = "",
                         max_age_seconds: int | None = None) -> int:
        """Count queued (not yet started) fix requests for this PR.

        owner/repo narrow the count to one repository; omitting them counts
        every queued request for the PR number (legacy callers).

        max_age_seconds, when set, counts only rows created within that window.
        Callers that treat a queued row as "a fix is already pending" must set
        it: nothing drains fix_queue, so a row's presence otherwise blocks the PR
        forever (PR #215 review). Omitting it returns the raw backlog.
        """
        self.init_fix_queue()
        conn = self._get_conn()
        query = "SELECT COUNT(*) FROM fix_queue WHERE pr_number = ? AND status = 'queued'"
        params: list = [pr_number]
        if owner:
            query += " AND owner = ?"
            params.append(owner)
        if repo:
            query += " AND repo = ?"
            params.append(repo)
        if max_age_seconds is not None:
            query += " AND created_at > ?"
            params.append(time.time() - max_age_seconds)
        row = conn.execute(query, params).fetchone()
        return row[0]

    def get_queue_position(self, queue_id: int) -> Optional[int]:
        """Return 1-based position of this queued request, or None if not queued.

        Tie-breaking: items with the same created_at are ordered by id (FIFO).
        """
        self.init_fix_queue()
        conn = self._get_conn()
        row = conn.execute(
            "SELECT created_at FROM fix_queue WHERE id = ? AND status = 'queued'",
            (queue_id,),
        ).fetchone()
        if not row:
            return None
        pos = conn.execute(
            "SELECT COUNT(*) FROM fix_queue WHERE status = 'queued' AND (created_at < ? OR (created_at = ? AND id <= ?))",
            (row[0], row[0], queue_id),
        ).fetchone()
        return pos[0] if pos else None

    def start_next_queued_fix(self) -> Optional[dict]:
        """Pop the oldest queued fix and mark it 'running'. Returns its data or None."""
        self.init_fix_queue()
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id, pr_number, pr_key, description, commenter, installation_id, owner, repo FROM fix_queue "
                "WHERE status = 'queued' ORDER BY created_at ASC, id ASC LIMIT 1",
            ).fetchone()
            if not row:
                conn.commit()
                return None
            conn.execute(
                "UPDATE fix_queue SET status = 'running', started_at = ? WHERE id = ?",
                (time.time(), row[0]),
            )
            conn.commit()
            return {
                "id": row[0],
                "pr_number": row[1],
                "pr_key": row[2],
                "description": row[3],
                "commenter": row[4],
                "installation_id": row[5],
                "owner": row[6],
                "repo": row[7],
            }
        except Exception:
            conn.rollback()
            raise

    def complete_fix_queue_item(self, queue_id: int, success: bool = True):
        """Mark a running queue item as completed or failed."""
        self.init_fix_queue()
        conn = self._get_conn()
        status = "completed" if success else "failed"
        conn.execute(
            "UPDATE fix_queue SET status = ?, completed_at = ? WHERE id = ?",
            (status, time.time(), queue_id),
        )
        conn.commit()

    def cleanup_stale_queue_items(self, max_age_seconds: int = FIX_TTL_SECONDS):
        """Mark stale 'running' items as failed (crashed without cleanup)."""
        self.init_fix_queue()
        conn = self._get_conn()
        cutoff = time.time() - max_age_seconds
        conn.execute(
            "UPDATE fix_queue SET status = 'failed', completed_at = ? "
            "WHERE status = 'running' AND started_at < ?",
            (time.time(), cutoff),
        )
        conn.commit()

    # ── Global fix activity ───────────────────────────────────────────────────

    def has_running_fix(self) -> bool:
        """Check if ANY fix job is currently running (global serialization gate)."""
        conn = self._get_conn()
        cutoff = time.time() - FIX_TTL_SECONDS
        # Escape wildcards in case job_id contains % or _
        pattern = self._escape_like("riptide-fix-") + "%"
        row = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE id LIKE ? ESCAPE '\\' AND status = 'pending' AND created_at > ?",
            (pattern, cutoff),
        ).fetchone()
        if row[0] > 0:
            return True
        # Also check the queue for 'running' items (with cutoff)
        self.init_fix_queue()
        row = conn.execute(
            "SELECT COUNT(*) FROM fix_queue WHERE status = 'running' AND started_at > ?",
            (cutoff,),
        ).fetchone()
        return row[0] > 0

    def get_running_fix_pr(self) -> Optional[int]:
        """Return the PR number of the currently-running fix, or None."""
        conn = self._get_conn()
        cutoff = time.time() - FIX_TTL_SECONDS
        pattern = self._escape_like("riptide-fix-") + "%"
        row = conn.execute(
            "SELECT pr_number FROM jobs WHERE id LIKE ? ESCAPE '\\' AND status = 'pending' AND created_at > ? "
            "ORDER BY created_at DESC LIMIT 1",
            (pattern, cutoff),
        ).fetchone()
        if row:
            return row[0]
        self.init_fix_queue()
        row = conn.execute(
            "SELECT pr_number FROM fix_queue WHERE status = 'running' AND started_at > ? ORDER BY started_at DESC LIMIT 1",
            (cutoff,),
        ).fetchone()
        return row[0] if row else None

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

    # ── Review Memory ──────────────────────────────────────────────────────────

    def store_review_outcome(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        head_sha: str,
        findings_count: int,
        critical_count: int,
        warning_count: int,
        verdict: str,
        metadata: Optional[str] = None,
    ):
        """Store a review outcome in review_memory and update review_profiles."""
        import uuid

        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        pr_key = f"{owner}/{repo}#{pr_number}"
        review_id = str(uuid.uuid4())

        conn.execute(
            """INSERT INTO review_memory
               (id, pr_key, pr_number, owner, repo, head_sha,
                findings_count, critical_count, warning_count,
                verdict, user_feedback, created_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                review_id,
                pr_key,
                pr_number,
                owner,
                repo,
                head_sha,
                findings_count,
                critical_count,
                warning_count,
                verdict,
                0,  # user_feedback — default 0 (no feedback yet)
                now,
                _metadata_json(metadata),
            ),
        )

        # Upsert review_profiles
        conn.execute(
            """INSERT INTO review_profiles (repo, total_reviews, last_review_at, updated_at)
               VALUES (?, 1, ?, ?)
               ON CONFLICT(repo) DO UPDATE SET
                 total_reviews = total_reviews + 1,
                 last_review_at = excluded.last_review_at,
                 updated_at = excluded.updated_at""",
            (repo, now, now),
        )

        conn.commit()

    def get_review_profile(self, repo: str) -> Optional[dict]:
        """Return the review profile for a repo, or None if no history."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT repo, total_reviews, common_findings, last_review_at, updated_at "
            "FROM review_profiles WHERE repo = ?",
            (repo,),
        ).fetchone()
        if row is None:
            return None
        return {
            "repo": row[0],
            "total_reviews": row[1],
            "common_findings": json.loads(row[2]) if row[2] else [],
            "last_review_at": row[3],
            "updated_at": row[4],
        }

    def get_memory_context(self, owner: str, repo: str) -> str:
        """
        Build a prompt injection string with common findings from past reviews.

        Returns:
            A string with historical findings, or empty string if no history.
        """
        conn = self._get_conn()
        profile = self.get_review_profile(repo)
        if not profile:
            return ""

        # Get the most recent reviews for this repo
        rows = conn.execute(
            "SELECT verdict, findings_count, critical_count, warning_count, created_at "
            "FROM review_memory WHERE repo = ? ORDER BY created_at DESC LIMIT 10",
            (repo,),
        ).fetchall()

        if not rows:
            return ""

        # Compute aggregate stats
        total_reviews = profile["total_reviews"]
        critical_rate = sum(r[2] for r in rows) / max(total_reviews, 1)
        warning_rate = sum(r[3] for r in rows) / max(total_reviews, 1)

        # Extract common findings (warnings + criticals from recent reviews)
        common = []
        for row in rows:
            if row[4]:  # created_at
                label = f"{row[0]} ({row[2]} critical, {row[3]} warning)"
                common.append(label)

        # Build context string
        lines = [
            "## Review History (from past reviews)",
            "",
            f"- Total reviews: {total_reviews}",
            f"- Recent critical rate: {critical_rate:.1%}",
            f"- Recent warning rate: {warning_rate:.1%}",
            f"- Last review: {profile.get('last_review_at', 'never')}",
            "",
            "### Recent Review Outcomes:",
        ]
        for c in common[:5]:
            lines.append(f"- {c}")
        lines.append("")
        lines.append(
            "Consider these historical patterns when reviewing this PR. "
            "Focus on catching issues that have been common in the past."
        )
        return "\n".join(lines)

    # ── Diagram assets (multi-session architecture, v10) ──────────────────
    #
    # The Diagram stage persists the raw .excalidraw JSON (gzipped) plus the
    # rendered PNG (gzipped) so a subsequent push can apply a delta patch
    # against the prior asset instead of re-rendering from scratch. See
    # PLAN-multi-session-architecture.md §3 for the full schema rationale.
    # The methods below are pure data-layer operations; the rendering and
    # diff-application logic lives in Wave 3 (mcp-excalidraw integration).

    @retry_db_fast
    def save_diagram_asset(
        self,
        pr_id: str,
        base_sha: str,
        render_kind: str,
        scene_json: bytes,
        rendered_png: bytes | None,
        element_count: int,
        parent_sha: str | None = None,
        delta_of: str | None = None,
        created_by: str | None = None,
        renderer_meta: str | None = None,
    ) -> str:
        """Insert a diagram asset, or overwrite the existing one for the same
        (pr_id, base_sha, render_kind) triple. The diagram_assets UNIQUE
        index makes this idempotent: re-renders for the same head replace
        the prior row instead of appending duplicates.

        Returns the new asset id (uuid4 hex).
        """
        import uuid

        asset_id = uuid.uuid4().hex
        conn = self._get_conn()
        self._acquire_lock()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO diagram_assets
                    (id, pr_id, base_sha, parent_sha, element_count, render_kind,
                     scene_json, rendered_png, renderer_meta, created_at, created_by,
                     delta_of)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    asset_id,
                    pr_id,
                    base_sha,
                    parent_sha,
                    element_count,
                    render_kind,
                    scene_json,
                    rendered_png,
                    renderer_meta,
                    datetime.now(timezone.utc).isoformat(),
                    created_by,
                    delta_of,
                ),
            )
            conn.commit()
        finally:
            self._release_lock()
        return asset_id

    @retry_db_fast
    def fetch_latest_diagram(
        self,
        pr_id: str,
        render_kind: str,
        before_base_sha: str | None = None,
    ) -> Optional[dict]:
        """Fetch the most recent diagram asset for a PR.

        When ``before_base_sha`` is set, returns the most recent asset
        strictly before that commit — this is the "prior scene" the
        delta-patch protocol needs. The unique index on
        (pr_id, base_sha, render_kind) means there is at most one row per
        head, so "most recent before" is unambiguous.
        """
        conn = self._get_conn()
        if before_base_sha is not None:
            row = conn.execute(
                """SELECT id, pr_id, base_sha, parent_sha, element_count,
                          render_kind, scene_json, rendered_png, renderer_meta,
                          created_at, created_by, delta_of
                   FROM diagram_assets
                   WHERE pr_id = ? AND render_kind = ? AND base_sha < ?
                   ORDER BY base_sha DESC, created_at DESC
                   LIMIT 1""",
                (pr_id, render_kind, before_base_sha),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT id, pr_id, base_sha, parent_sha, element_count,
                          render_kind, scene_json, rendered_png, renderer_meta,
                          created_at, created_by, delta_of
                   FROM diagram_assets
                   WHERE pr_id = ? AND render_kind = ?
                   ORDER BY base_sha DESC, created_at DESC
                   LIMIT 1""",
                (pr_id, render_kind),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_diagram_asset(row)

    @retry_db_fast
    def fetch_diagram_for_sha(
        self,
        pr_id: str,
        base_sha: str,
        render_kind: str,
    ) -> Optional[dict]:
        """Fetch the diagram asset for an exact (pr_id, base_sha, render_kind)."""
        conn = self._get_conn()
        row = conn.execute(
            """SELECT id, pr_id, base_sha, parent_sha, element_count,
                      render_kind, scene_json, rendered_png, renderer_meta,
                      created_at, created_by, delta_of
               FROM diagram_assets
               WHERE pr_id = ? AND base_sha = ? AND render_kind = ?
               LIMIT 1""",
            (pr_id, base_sha, render_kind),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_diagram_asset(row)

    @retry_db_fast
    def list_diagram_assets(
        self,
        pr_id: str,
        render_kind: str | None = None,
    ) -> list[dict]:
        """List all diagram assets for a PR, newest head first.

        When ``render_kind`` is set, only assets of that kind (e.g. 'review')
        are returned. Used by the Scribe to show "diagram history" alongside
        the current review comment.
        """
        conn = self._get_conn()
        if render_kind is not None:
            rows = conn.execute(
                """SELECT id, pr_id, base_sha, parent_sha, element_count,
                          render_kind, scene_json, rendered_png, renderer_meta,
                          created_at, created_by, delta_of
                   FROM diagram_assets
                   WHERE pr_id = ? AND render_kind = ?
                   ORDER BY base_sha DESC, created_at DESC""",
                (pr_id, render_kind),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, pr_id, base_sha, parent_sha, element_count,
                          render_kind, scene_json, rendered_png, renderer_meta,
                          created_at, created_by, delta_of
                   FROM diagram_assets
                   WHERE pr_id = ?
                   ORDER BY base_sha DESC, created_at DESC""",
                (pr_id,),
            ).fetchall()
        return [self._row_to_diagram_asset(r) for r in rows]

    def _row_to_diagram_asset(self, row) -> dict:
        """Decode a ``diagram_assets`` row into a plain dict.

        The blob columns are returned as raw bytes — the caller is responsible
        for decompressing (gzip) and parsing (JSON) the scene, and decoding
        the PNG header. Keeping the data layer byte-clean lets the rendering
        path control its own compression/decompression policy.
        """
        return {
            "id": row[0],
            "pr_id": row[1],
            "base_sha": row[2],
            "parent_sha": row[3],
            "element_count": row[4],
            "render_kind": row[5],
            "scene_json": row[6],  # gzipped .excalidraw (Element[] array)
            "rendered_png": row[7],  # gzipped PNG bytes (None until render_png ran)
            "renderer_meta": row[8],
            "created_at": row[9],
            "created_by": row[10],
            "delta_of": row[11],
        }

    @staticmethod
    def compute_diagram_diff(
        prior_scene_json: bytes | None,
        new_context: dict,
    ) -> dict:
        """Compute the structural delta between a prior diagram asset and a
        new review context. Pure-Python data shaping — does not touch
        mcp-excalidraw. The LLM in Wave 3 will use this diff to drive
        ``update_element`` / ``add_element`` / ``delete_element`` calls.

        ``prior_scene_json`` is the raw gzipped ``.excalidraw`` Element[]
        array as stored in the prior asset's ``scene_json`` blob, or
        ``None`` if no prior scene exists (the first push of the PR).

        ``new_context`` is the new context bundle, expected to have
        keys ``scan`` (with ``pr_details``, ``files``) and ``review`` (with
        ``findings``). The graph key is accepted but not used in this
        first cut — the diagram stage's diff is over file changes and
        review findings, not graphify output. Wave 3 will extend this
        when the LLM starts to consume the graph.

        Returns a dict with these keys:
            has_prior:    bool, True iff a prior scene was provided.
            prior_element_count: int (0 if no prior), useful as a baseline.
            added_files, removed_files, modified_files: list[str] (paths).
            added_findings, removed_findings, modified_findings: list[dict]
                (the affected finding objects, by file:line; a finding
                is "modified" if it disappeared from one file:line and
                reappeared at another, otherwise added/removed by exact
                file:line match).
            unchanged_findings: list[dict] (findings present in both).

        The diff is intentionally a *list of structural changes*, not a
        full patch. The LLM decides how to map each change to Excalidraw
        elements (e.g. which new element to add, which old element to
        update or remove). Wave 3 wires the LLM to mcp-excalidraw.
        """
        import gzip
        import json as _json

        def _decode_scene(blob: bytes | None) -> list[dict]:
            if blob is None:
                return []
            try:
                raw = gzip.decompress(blob).decode("utf-8")
            except (OSError, UnicodeDecodeError):
                # Stored scene was not gzip-compressed (legacy or corrupted
                # asset). Fall back to raw decode so the diff still runs.
                raw = blob.decode("utf-8", errors="replace")
            try:
                elements = _json.loads(raw)
            except _json.JSONDecodeError:
                return []
            # Excalidraw Element[] is the canonical shape; tolerate the
            # older wrapper shape ({elements: [...], ...}) too.
            if isinstance(elements, dict) and "elements" in elements:
                elements = elements["elements"]
            return elements if isinstance(elements, list) else []

        prior_elements = _decode_scene(prior_scene_json)
        diff: dict = {
            "has_prior": bool(prior_scene_json),
            "prior_element_count": len(prior_elements),
            "added_files": [],
            "removed_files": [],
            "modified_files": [],
            "added_findings": [],
            "removed_findings": [],
            "modified_findings": [],
            "unchanged_findings": [],
        }

        scan = new_context.get("scan") or {}
        new_files = {
            (f.get("filename") or ""): f
            for f in (scan.get("files") or [])
            if f.get("filename")
        }

        # Files: we don't have the prior files list in the asset (the
        # asset stores rendered Excalidraw, not the scan). Use the
        # prior_scene_json's element labels as a proxy: any element
        # referencing a file path that no longer appears in new_files
        # is a candidate for removal. The agent will validate.
        prior_files: set[str] = set()
        for el in prior_elements:
            for key in ("file", "path", "sourceFile", "containerId"):
                value = el.get(key) if isinstance(el, dict) else None
                if isinstance(value, str) and value:
                    prior_files.add(value)
        new_file_set = set(new_files.keys())
        # Intersection / differences — treat as candidate signal only.
        diff["added_files"] = sorted(new_file_set - prior_files)
        diff["removed_files"] = sorted(prior_files - new_file_set)

        # Findings: exact-match by (file, line, title). Modified = same title,
        # different file:line.
        review = new_context.get("review") or {}
        new_findings = list(review.get("findings") or [])
        new_by_key: dict[tuple, dict] = {}
        new_by_title: dict[str, dict] = {}
        for f in new_findings:
            key = (
                f.get("file") or "",
                f.get("line") or 0,
                f.get("title") or "",
            )
            new_by_key[key] = f
            new_by_title[f.get("title") or ""] = f

        # We don't have prior findings as a list — the prior scene is the
        # only source. Pull finding-shaped elements from the scene: the
        # diagram's "Findings with Severity" section uses ``kind: "text"``
        # elements whose ``text`` string starts with the severity emoji
        # (🔴/🟡/🟢). This is a heuristic, not a parse — the LLM
        # refines the diff with the actual findings in Wave 3.
        prior_findings: list[dict] = []
        for el in prior_elements:
            if not isinstance(el, dict):
                continue
            text = el.get("text") or ""
            if any(text.startswith(prefix) for prefix in ("🔴", "🟡", "🟢")):
                prior_findings.append({"title": text, "raw": el})

        prior_titles = {f.get("title") or "" for f in prior_findings}
        new_titles = set(new_by_title.keys())

        for f in new_findings:
            title = f.get("title") or ""
            if title in prior_titles:
                diff["unchanged_findings"].append(f)
            else:
                diff["added_findings"].append(f)
        for f in prior_findings:
            title = f.get("title") or ""
            if title and title not in new_titles:
                # Truncate long prior titles (which include severity emoji +
                # line breaks + body text) to a comparable key.
                diff["removed_findings"].append(
                    {"title": title.split("\n", 1)[0], "raw": f.get("raw")}
                )

        return diff


# Module-level convenience
POLLER_DB_PATH = Path.home() / ".local/share/riptide/metadata.db"
