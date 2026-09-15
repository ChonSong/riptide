"""Legacy-database upgrade paths that `fa99b25` (#156) silently deleted.

Two migrations were removed while `state.py` was rewritten for fcntl locking:
`_migrate_poller_comments()` was reduced to `pass`, and the
`tier1_comment_id` ALTER never ran. Both are exercised here against synthetic
databases built at the OLD schema version, because that is the only way an
existing install is affected — nothing in the current code path shows the loss.

Every test builds its own databases in `tmp_path`; nothing here touches a real
state database.
"""

import sqlite3

import pytest

import riptide.state as state_mod
from riptide.state import StateStore


def _make_legacy_poller_db(path, *, with_pending_response=True, rows=()):
    with sqlite3.connect(path) as connection:
        if with_pending_response:
            connection.execute(
                """CREATE TABLE poller_processed_comments (
                    comment_id INTEGER PRIMARY KEY,
                    processed_at TEXT,
                    result TEXT,
                    pending_response TEXT)"""
            )
        else:
            connection.execute(
                """CREATE TABLE poller_processed_comments (
                    comment_id INTEGER PRIMARY KEY,
                    processed_at TEXT,
                    result TEXT)"""
            )
        for row in rows:
            placeholders = ",".join("?" * len(row))
            connection.execute(
                f"INSERT INTO poller_processed_comments VALUES ({placeholders})", row
            )


def _make_legacy_state_db(path, *, version=1, heuristics=None):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_version (version INTEGER)")
        connection.execute("INSERT INTO schema_version VALUES (?)", (version,))
        if heuristics is not None:
            connection.execute(
                "CREATE TABLE pr_heuristics (pr_key TEXT PRIMARY KEY, review_count INTEGER)"
            )
            connection.execute(
                "INSERT INTO pr_heuristics VALUES (?, ?)", (heuristics[0], heuristics[1])
            )


@pytest.fixture()
def legacy_poller_db(tmp_path, monkeypatch):
    """Point the migration at a synthetic legacy poller metadata.db."""

    def _install(**kwargs):
        path = tmp_path / "metadata.db"
        _make_legacy_poller_db(path, **kwargs)
        monkeypatch.setattr(state_mod, "POLLER_DB_PATH", path)
        return path

    return _install


def test_poller_comments_are_migrated_and_dedup_history_survives(
    tmp_path, legacy_poller_db
):
    legacy_poller_db(
        rows=[(1001, "2026-01-01T00:00:00+00:00", "done", "pending-x")]
    )
    state_db = tmp_path / "state.db"
    _make_legacy_state_db(state_db, version=1)

    store = StateStore(db_path=str(state_db))

    with sqlite3.connect(state_db) as connection:
        row = connection.execute(
            "SELECT result, pending_response FROM processed_comments WHERE comment_id = 1001"
        ).fetchone()
    assert row == ("done", "pending-x")

    # Without this migration an upgrading install re-processes old fix commands.
    assert store.is_comment_processed(1001) is True
    assert store.get_pending_response(1001) == "pending-x"


def test_poller_migration_accepts_the_older_shape_without_pending_response(
    tmp_path, legacy_poller_db
):
    legacy_poller_db(
        with_pending_response=False,
        rows=[(2001, "2026-01-02T00:00:00+00:00", "done")],
    )
    state_db = tmp_path / "state.db"
    _make_legacy_state_db(state_db, version=1)

    store = StateStore(db_path=str(state_db))

    assert store.is_comment_processed(2001) is True
    assert store.get_pending_response(2001) is None


def test_poller_migration_is_idempotent(tmp_path, legacy_poller_db):
    legacy_poller_db(rows=[(1001, "2026-01-01T00:00:00+00:00", "done", None)])
    state_db = tmp_path / "state.db"
    _make_legacy_state_db(state_db, version=1)

    StateStore(db_path=str(state_db))
    with sqlite3.connect(state_db) as connection:
        first = connection.execute("SELECT COUNT(*) FROM processed_comments").fetchone()[0]

    StateStore(db_path=str(state_db))
    with sqlite3.connect(state_db) as connection:
        second = connection.execute("SELECT COUNT(*) FROM processed_comments").fetchone()[0]

    assert first == second == 1


def test_migration_is_a_noop_without_a_legacy_poller_db(tmp_path):
    state_db = tmp_path / "state.db"
    _make_legacy_state_db(state_db, version=1)
    state_mod_original = state_mod.POLLER_DB_PATH
    try:
        state_mod.POLLER_DB_PATH = tmp_path / "does-not-exist.db"
        StateStore(db_path=str(state_db))  # must not raise
    finally:
        state_mod.POLLER_DB_PATH = state_mod_original

    with sqlite3.connect(state_db) as connection:
        count = connection.execute("SELECT COUNT(*) FROM processed_comments").fetchone()[0]
    assert count == 0


def test_migration_is_a_noop_when_the_legacy_table_is_absent(tmp_path, monkeypatch):
    path = tmp_path / "metadata.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE something_else (x INTEGER)")
    monkeypatch.setattr(state_mod, "POLLER_DB_PATH", path)

    state_db = tmp_path / "state.db"
    _make_legacy_state_db(state_db, version=1)
    StateStore(db_path=str(state_db))  # must not raise

    with sqlite3.connect(state_db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM processed_comments").fetchone()[0] == 0


def test_tier1_comment_id_is_added_to_a_pre_existing_table(tmp_path):
    """CREATE TABLE IF NOT EXISTS cannot add a column to an existing table."""
    state_db = tmp_path / "state.db"
    _make_legacy_state_db(state_db, version=8, heuristics=("ChonSong/riptide#42", 3))

    store = StateStore(db_path=str(state_db))

    with sqlite3.connect(state_db) as connection:
        columns = {r[1] for r in connection.execute("PRAGMA table_info(pr_heuristics)")}
        row = connection.execute(
            "SELECT review_count FROM pr_heuristics WHERE pr_key = 'ChonSong/riptide#42'"
        ).fetchone()
    assert "tier1_comment_id" in columns, "the ALTER must run on an existing table"
    assert row == (3,), "existing heuristic rows survive the upgrade"

    # The getter/setter pair raised `no such column` before the ALTER was restored.
    store.set_pr_tier1_comment_id("ChonSong/riptide#42", 555)
    assert store.get_pr_tier1_comment_id("ChonSong/riptide#42") == 555
