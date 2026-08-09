#!/usr/bin/env python3
"""work_state.py — deterministic state management for Riptide Pipeline."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

WORK_STATE_PATH = os.environ.get(
    "RIPTIDE_WORK_STATE",
    str(Path.home() / ".hermes/state/riptide-work-state.json"),
)

# Thread-safe lock for read-modify-write operations
_STATE_LOCK = threading.Lock()

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


def read_state() -> dict:
    """Read work-state.json, creating if missing (thread-safe)."""
    with _STATE_LOCK:
        return _read_state_unsafe()


def write_state(state: dict) -> None:
    """Write work-state.json atomically (thread-safe)."""
    with _STATE_LOCK:
        _write_state_unsafe(state)


def _read_state_unsafe() -> dict:
    if not Path(WORK_STATE_PATH).exists():
        return {"version": 1, "tracks": {}}
    with open(WORK_STATE_PATH, "r") as f:
        return json.load(f)


def _write_state_unsafe(state: dict) -> None:
    Path(WORK_STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
    tmp = f"{WORK_STATE_PATH}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.replace(tmp, WORK_STATE_PATH)


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
) -> dict:
    state = read_state()
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
    write_state(state)
    return track


def update_track(track_id: str, updates: dict) -> dict:
    state = read_state()
    track = state["tracks"][track_id]
    track.update(updates)
    track["last_updated"] = now()
    write_state(state)
    return track


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
    state = read_state()
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
    write_state(state)
    return ws


def update_workstream(
    track_id: str,
    ws_id: str,
    status: Optional[str] = None,
    outputs: Optional[dict] = None,
) -> dict:
    state = read_state()
    ws = state["tracks"][track_id]["workstreams"][ws_id]
    if status:
        ws["status"] = status
    if outputs:
        ws["outputs"].update(outputs)
    if status == "done":
        ws["completed_at"] = now()
    state["tracks"][track_id]["last_updated"] = now()
    write_state(state)
    return ws


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


def update_key_facts(track_id: str, facts: dict) -> dict:
    state = read_state()
    track = state["tracks"][track_id]
    track.setdefault("key_facts", {}).update(facts)
    track["last_updated"] = now()
    write_state(state)
    return track
