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

    SCHEMA_VERSION = 8

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

        # v7: checkbox trigger dedup
        conn.execute("""CREATE TABLE IF NOT EXISTS checkbox_triggers (
            pr_key TEXT NOT NULL, label TEXT NOT NULL, triggered_at REAL NOT NULL,
            PRIMARY KEY (pr_key, label))""")

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
        pass

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
                json.dumps(metadata) if metadata else None,
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


# Module-level convenience
POLLER_DB_PATH = Path.home() / ".local/share/riptide/metadata.db"
