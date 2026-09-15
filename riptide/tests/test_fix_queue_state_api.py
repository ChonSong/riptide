"""End-to-end coverage for the fix-queue state API.

These tests exercise the queue the way production does: two requests serialise,
the running slot gates new work, positions are FIFO, ownership filters the
count, and an expired "running" row must not hold the gate forever. They exist
because `fa99b25` (#156) deleted these methods from `StateStore` while leaving
`fixer.py` calling them on the main `@riptide-bot fix` path — every fix command
raised `AttributeError` before it could spawn anything.

Kept deliberately independent of the ambient environment: every test builds its
own `StateStore` on a temp database, so it passes under the hermetic suite and
in a clean CI checkout alike.
"""

import sqlite3
import time

import pytest

from riptide.state import FIX_TTL_SECONDS, StateStore


@pytest.fixture()
def store(tmp_path):
    return StateStore(db_path=str(tmp_path / "state.db"))


def test_fresh_state_has_nothing_running(store):
    assert store.has_running_fix() is False
    assert store.get_running_fix_pr() is None


def test_enqueue_serialises_and_reports_fifo_positions(store):
    first = store.enqueue_fix(101, "o/r#101", "alice", "fix a", owner="o", repo="r")
    second = store.enqueue_fix(101, "o/r#101", "bob", "fix b", owner="o", repo="r")

    assert store.get_queue_position(first) == 1
    assert store.get_queue_position(second) == 2
    assert store.get_queue_length(101, owner="o", repo="r") == 2
    # Queued work is not running work.
    assert store.has_running_fix() is False

    started = store.start_next_queued_fix()
    assert started["id"] == first, "FIFO: the oldest request starts first"
    assert started["owner"] == "o" and started["repo"] == "r"
    assert store.has_running_fix() is True
    assert store.get_running_fix_pr() == 101
    # A started request is no longer "queued", so it has no position.
    assert store.get_queue_position(first) is None
    assert store.get_queue_length(101, owner="o", repo="r") == 1


def test_completion_frees_the_running_slot(store):
    queued = store.enqueue_fix(202, "o/r#202", "carol", "", owner="o", repo="r")
    item = store.start_next_queued_fix()

    store.complete_fix_queue_item(item["id"], success=True)

    assert store.has_running_fix() is False
    assert store.get_running_fix_pr() is None
    assert store.get_queue_length(202, owner="o", repo="r") == 0
    assert store.start_next_queued_fix() is None, "queue is drained"
    assert queued == item["id"]


def test_enqueue_records_installation_id(store):
    store.enqueue_fix(
        303, "o/r#303", "dave", "with id", installation_id=987654, owner="o", repo="r"
    )
    item = store.start_next_queued_fix()
    assert item["installation_id"] == 987654
    assert item["pr_key"] == "o/r#303"
    assert item["commenter"] == "dave"


def test_queue_length_is_scoped_to_owner_and_repo(store):
    store.enqueue_fix(55, "o/r#55", "x", "", owner="o", repo="r")
    store.enqueue_fix(55, "o/z#55", "y", "", owner="o", repo="z")

    assert store.get_queue_length(55, owner="o", repo="r") == 1
    assert store.get_queue_length(55, owner="o", repo="z") == 1
    assert store.get_queue_length(55, owner="o", repo="nope") == 0
    # Legacy callers omit owner/repo: that counts every queued request for the PR.
    assert store.get_queue_length(55) == 2


def test_expired_running_item_does_not_hold_the_gate(store):
    """A worker that died mid-fix must not block every future fix request."""
    queued = store.enqueue_fix(404, "o/r#404", "erin", "", owner="o", repo="r")
    started = store.start_next_queued_fix()
    assert started["id"] == queued

    connection = sqlite3.connect(store.db_path)
    connection.execute(
        "UPDATE fix_queue SET started_at = ? WHERE id = ?",
        (time.time() - FIX_TTL_SECONDS - 60, queued),
    )
    connection.commit()

    assert store.has_running_fix() is False, "an expired run frees the gate"
    assert store.get_running_fix_pr() is None

    store.cleanup_stale_queue_items()
    status = connection.execute(
        "SELECT status FROM fix_queue WHERE id = ?", (queued,)
    ).fetchone()[0]
    connection.close()
    assert status == "failed", "cleanup marks the abandoned item failed"


def test_fixer_check_and_queue_sequence_does_not_raise(store):
    """The exact call sequence `fixer.py` uses when a fix command arrives.

    This is the regression guard: before the restore, this sequence raised
    `AttributeError: 'StateStore' object has no attribute 'has_running_fix'`,
    so no `@riptide-bot fix` command could ever spawn.
    """
    running = store.has_running_fix()
    queued_for_pr = store.get_queue_length(42, owner="o", repo="r")

    queue_id = position = running_pr = None
    if running or queued_for_pr > 0:
        queue_id = store.enqueue_fix(42, "o/r#42", "frank", "", owner="o", repo="r")
        position = store.get_queue_position(queue_id)
        running_pr = store.get_running_fix_pr()

    assert running is False and queued_for_pr == 0
    assert (queue_id, position, running_pr) == (None, None, None)

    # And the busy path queues instead of dropping the request.
    store.enqueue_fix(42, "o/r#42", "gina", "first", owner="o", repo="r")
    started = store.start_next_queued_fix()
    assert started["id"] is not None
    queued = store.enqueue_fix(42, "o/r#42", "gina", "second", owner="o", repo="r")
    assert store.get_queue_position(queued) == 1
    assert store.get_queue_length(42, owner="o", repo="r") == 1
