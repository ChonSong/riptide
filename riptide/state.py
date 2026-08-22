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

    SCHEMA_VERSION = 7

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

        # v7: review_memory — persist review outcomes per PR for context injection
        # and review_profiles — per-repo aggregate stats for common-finding patterns
        conn.execute(
            """CREATE TABLE IF NOT EXISTS review_memory (
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
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS review_profiles (
                repo TEXT PRIMARY KEY,
                total_reviews INTEGER DEFAULT 0,
                common_findings TEXT DEFAULT '[]',
                last_review_at TEXT,
                updated_at TEXT
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_memory_pr_key ON review_memory (pr_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_memory_repo ON review_memory (repo)"
        )

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


# Module-level convenience (poller's old DB path for migration)
POLLER_DB_PATH = Path.home() / ".local/share/riptide/metadata.db"
