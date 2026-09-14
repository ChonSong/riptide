#!/usr/bin/env python3
"""work_state.py — deterministic state management for Riptide Pipeline."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

WORK_STATE_PATH = os.environ.get(
    "RIPTIDE_WORK_STATE",
    str(Path.home() / ".hermes/state/riptide-work-state.json"),
)

# Thread-safe lock for read-modify-write operations
_STATE_LOCK = threading.RLock()

# Process-safe counterpart: an exclusive fcntl.flock() on a sidecar lock file
# next to the state file (same convention as riptide/state.py). Each review runs
# in its own OS process, so a thread lock alone lets two processes load the same
# snapshot and have the later write_state() silently drop the other's track.
#
# Re-entrancy: the flock is taken once per outermost acquire (tracked by
# _lock_depth), and a nested critical section JOINS the outer transaction via
# _current_state instead of re-reading the file — so a nested modify_state()
# neither self-deadlocks nor has its write clobbered by the outer's snapshot.
_lock_fds: dict[str, object] = {}
_lock_depth = 0
_current_state: Optional[dict] = None

# ── Schema ──────────────────────────────────────────────────────────────────

WORK_STATE_SCHEMA = {
    "version": 1,
    "tracks": {
        # track_id: {
        #   "name": str,
        #   "phase": str,
        #   "status": "active" | "blocked" | "done",
        #   "current_ws": str,
        #   "workstreams": {
        #     ws_id: {
        #       "id": str,
        #       "status": "pending" | "in_progress" | "done" | "failed" | "blocked",
        #       "inputs": dict,
        #       "outputs": dict,
        #       "acceptance": dict,
        #       "recovery": dict,
        #       "completed_at": str | None,
        #     }
        #   },
        #   "key_facts": dict,
        #   "repos": dict,
        #   "last_updated": str,
        # }
    },
}

# ── CRUD ────────────────────────────────────────────────────────────────────


def _lock_fd():
    """Return the cached lock file handle, creating the sidecar lock file."""
    lock_path = WORK_STATE_PATH + ".lock"
    fd = _lock_fds.get(lock_path)
    if fd is None:
        Path(WORK_STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(lock_path).touch()
        fd = _lock_fds[lock_path] = open(lock_path, "w")
    return fd


@contextmanager
def _state_guard(exclusive: bool = True):
    """Hold the thread lock plus the cross-process flock for a critical section.

    The flock() is taken only on the outermost entry (tracked by _lock_depth)
    and released on the matching exit, so nested critical sections in the same
    process reuse the held lock rather than blocking on it. When the outermost
    section exits, the in-flight transaction state is discarded.
    """
    global _lock_depth, _current_state
    with _STATE_LOCK:
        fd = _lock_fd()
        if _lock_depth == 0:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        _lock_depth += 1
        try:
            yield
        finally:
            _lock_depth -= 1
            if _lock_depth == 0:
                _current_state = None
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)


def read_state() -> dict:
    """Read work-state.json, creating if missing (thread- and process-safe).

    Inside an active critical section this returns the in-flight state, so a
    caller can read its own uncommitted changes.
    """
    with _state_guard(exclusive=False):
        if _current_state is not None:
            return _current_state
        return _read_state_unsafe()


def write_state(state: dict) -> None:
    """Write work-state.json atomically (thread- and process-safe).

    Takes the exclusive lock so a bare write cannot land in the middle of
    another process's read→mutate→write transaction.
    """
    with _state_guard():
        _write_state_unsafe(state)


def modify_state(fn) -> None:
    """Execute fn(state) under lock — atomic read-modify-write.

    Use this for operations that need to read, modify, and write state
    without another thread OR process interleaving between the read and
    write. The exclusive flock is held across the whole transaction, so two
    reviews running in separate processes cannot drop each other's tracks.

    Re-entrant: calling modify_state() from inside fn joins the outer
    transaction (same snapshot, written once) instead of deadlocking or
    clobbering it.

    Example:
        modify_state(lambda s: s["tracks"]["t1"]["status"] = "done")
    """
    global _current_state
    with _state_guard():
        if _current_state is None:
            _current_state = _read_state_unsafe()
        fn(_current_state)
        _write_state_unsafe(_current_state)


def _read_state_unsafe() -> dict:
    if not Path(WORK_STATE_PATH).exists():
        return {"version": 1, "tracks": {}}
    with open(WORK_STATE_PATH, "r") as f:
        return json.load(f)


def _write_state_unsafe(state: dict) -> None:
    Path(WORK_STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
    # Unique tmp filename to avoid concurrent writers clobbering each other
    fd, tmp = tempfile.mkstemp(dir=Path(WORK_STATE_PATH).parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, WORK_STATE_PATH)
    except BaseException:
        # Clean up tmp file on failure
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Track operations ────────────────────────────────────────────────────────


def get_track(track_id: str) -> Optional[dict]:
    state = read_state()
    return state.get("tracks", {}).get(track_id)


def create_track(
    track_id: str,
    name: str,
    phase: str,
    repos: dict,
    key_facts: Optional[dict] = None,
) -> Optional[dict]:
    def _do(state):
        track = {
            "name": name,
            "phase": phase,
            "status": "active",
            "current_ws": None,
            "workstreams": {},
            "key_facts": key_facts or {},
            "repos": repos,
            "last_updated": now(),
        }
        state.setdefault("tracks", {})[track_id] = track
    modify_state(_do)
    return get_track(track_id)


def update_track(track_id: str, updates: dict) -> Optional[dict]:
    def _do(state):
        track = state["tracks"][track_id]
        track.update(updates)
        track["last_updated"] = now()
    modify_state(_do)
    return get_track(track_id)


# ── Workstream operations ───────────────────────────────────────────────────


def get_workstream(track_id: str, ws_id: str) -> Optional[dict]:
    track = get_track(track_id)
    if not track:
        return None
    return track.get("workstreams", {}).get(ws_id)


def create_workstream(
    track_id: str,
    ws_id: str,
    inputs: Optional[dict] = None,
    acceptance: Optional[dict] = None,
    recovery: Optional[dict] = None,
    role: Optional[str] = None,
    pipeline: Optional[list[str]] = None,
) -> dict:
    def _do(state):
        ws = {
            "id": ws_id,
            "status": "pending",
            "role": role or "engine",
            "pipeline": pipeline or [],
            "inputs": inputs or {},
            "outputs": {},
            "acceptance": acceptance or {},
            "recovery": recovery or {},
            "completed_at": None,
        }
        state["tracks"][track_id]["workstreams"][ws_id] = ws
        state["tracks"][track_id]["last_updated"] = now()
    modify_state(_do)
    return get_workstream(track_id, ws_id)


def update_workstream(
    track_id: str,
    ws_id: str,
    status: Optional[str] = None,
    outputs: Optional[dict] = None,
) -> dict:
    def _do(state):
        ws = state["tracks"][track_id]["workstreams"][ws_id]
        if status:
            ws["status"] = status
        if outputs:
            ws["outputs"].update(outputs)
        if status == "done":
            ws["completed_at"] = now()
        state["tracks"][track_id]["last_updated"] = now()
    modify_state(_do)
    return get_workstream(track_id, ws_id)


def next_pending_workstream(track_id: str) -> Optional[tuple[str, dict]]:
    """Find next pending workstream, respecting sequential ordering."""
    track = get_track(track_id)
    if not track:
        return None
    for ws_id, ws in track.get("workstreams", {}).items():
        if ws["status"] == "pending":
            return ws_id, ws
    return None


# ── Key facts ────────────────────────────────────────────────────────────────


def update_key_facts(track_id: str, facts: dict) -> Optional[dict]:
    def _do(state):
        track = state["tracks"][track_id]
        track.setdefault("key_facts", {}).update(facts)
        track["last_updated"] = now()
    modify_state(_do)
    return get_track(track_id)
