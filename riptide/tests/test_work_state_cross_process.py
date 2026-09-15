#!/usr/bin/env python3
"""Cross-process safety tests for work_state.py.

Each review runs in its own OS process (`deepthink.py` spawns one Hermes
Conductor session per PR), so the work-state lock has to hold across processes.
`_STATE_LOCK` is a `threading.Lock` and protects threads only; two reviews
loading the state at the same time could have the later save silently drop the
other review's track.

These tests spawn REAL separate processes — thread-only tests passed before the
fix and cannot catch this. The worker sleeps inside the critical section to
widen the load→save window the way the real path does (a review is spawned by
`record_review_start` and completed by `record_review_complete` minutes later);
the lock must hold regardless of how wide that window is.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

ROUNDS = 3
WORKERS = 3
WRITES_PER_WORKER = 2
# Filler tracks widen the json load/save window so concurrent processes really
# do overlap their read→write spans.
FILLER_TRACKS = 1500
FILLER_BLOB = "x" * 400
MODIFY_SLEEP = 0.15
BARRIER_TIMEOUT = 30
WORKER_TIMEOUT = 120


# ── worker: this file, re-executed in its own OS process ─────────────────────


def _barrier(box: str, idx: int, nworkers: int) -> None:
    """Rendezvous so every process has started before any of them writes."""
    os.makedirs(box, exist_ok=True)
    open(os.path.join(box, f"ready-{idx}"), "w").close()
    deadline = time.time() + BARRIER_TIMEOUT
    while len(glob.glob(os.path.join(box, "ready-*"))) < nworkers:
        if time.time() > deadline:
            raise SystemExit(f"worker {idx}: barrier timeout")
        time.sleep(0.001)


def _worker(shape: str, idx: int, state_path: str, box: str, nworkers: int) -> None:
    from riptide.pipeline import work_state

    _barrier(box, idx, nworkers)

    if shape == "modify":
        for j in range(WRITES_PER_WORKER):
            def _do(state, i=idx, jdx=j):
                # Model the review work that happens between load and save.
                time.sleep(MODIFY_SLEEP)
                state.setdefault("tracks", {})[f"proc-{i}-{jdx}"] = {
                    "name": f"worker {i} write {jdx}",
                    "status": "active",
                }
            work_state.modify_state(_do)

    elif shape == "scribe":
        from riptide.pipeline.scribe import Scribe

        scribe = Scribe()
        for j in range(WRITES_PER_WORKER):
            pr_number = idx * 100 + j
            scribe.record_review_complete(f"track-{idx}", pr_number, [{"severity": "low"}])
            scribe.record_review_complete("shared", pr_number, [{"severity": "low"}])

    else:
        raise SystemExit(f"unknown shape {shape!r}")


# ── helpers ──────────────────────────────────────────────────────────────────


def _spawn(shape: str, idx: int, state_path: str, box: str, nworkers: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["RIPTIDE_WORK_STATE"] = str(state_path)
    return subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--worker", shape,
         str(idx), str(state_path), str(box), str(nworkers)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_workers(shape: str, state_path, box, nworkers: int) -> None:
    procs = [_spawn(shape, i, state_path, box, nworkers) for i in range(nworkers)]
    for p in procs:
        try:
            _, err = p.communicate(timeout=WORKER_TIMEOUT)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            pytest.fail(f"{shape} worker {p.args[3]} timed out — deadlock?")
        assert p.returncode == 0, f"{shape} worker failed: {err}"


def _seed(state_path, tracks: dict) -> None:
    with open(state_path, "w") as f:
        json.dump({"version": 1, "tracks": tracks}, f, indent=2)


def _filler() -> dict:
    return {
        f"filler-{i}": {"name": f"filler {i}", "status": "active", "blob": FILLER_BLOB}
        for i in range(FILLER_TRACKS)
    }


def _read_valid_json(state_path) -> dict:
    """The state file must always be complete, parseable JSON."""
    with open(state_path) as f:
        raw = f.read()
    return json.loads(raw)  # raises on truncation


def _assert_no_tmp_leftovers(directory) -> None:
    leftovers = glob.glob(os.path.join(str(directory), "*.tmp"))
    assert not leftovers, f"atomic write left temp files behind: {leftovers}"


# ── tests ────────────────────────────────────────────────────────────────────


class TestCrossProcessLostUpdate:
    """Concurrent processes must never drop each other's state."""

    def test_modify_state_keeps_every_key_across_processes(self, tmp_path):
        """ROUNDS rounds x WORKERS processes through modify_state, no key lost."""
        for r in range(ROUNDS):
            state_path = tmp_path / f"modify-{r}.json"
            box = tmp_path / f"modify-box-{r}"
            _seed(state_path, _filler())

            _run_workers("modify", state_path, box, WORKERS)

            final = _read_valid_json(state_path)
            tracks = final["tracks"]
            expected = {f"proc-{i}-{j}" for i in range(WORKERS)
                        for j in range(WRITES_PER_WORKER)}
            missing = sorted(expected - set(tracks))
            assert not missing, (
                f"round {r}: cross-process writes lost {missing} — "
                f"{len(tracks)} tracks in final state"
            )
            assert len(tracks) == FILLER_TRACKS + len(expected)
            _assert_no_tmp_leftovers(tmp_path)

    def test_scribe_path_keeps_every_key_across_processes(self, tmp_path):
        """The production call path (Scribe) is process-safe too."""
        for r in range(ROUNDS):
            state_path = tmp_path / f"scribe-{r}.json"
            box = tmp_path / f"scribe-box-{r}"
            seeded = _filler()
            seeded["shared"] = {"name": "shared", "status": "active"}
            for i in range(WORKERS):
                seeded[f"track-{i}"] = {"name": f"track {i}", "status": "active"}
            _seed(state_path, seeded)

            _run_workers("scribe", state_path, box, WORKERS)

            final = _read_valid_json(state_path)
            tracks = final["tracks"]
            for i in range(WORKERS):
                assert f"track-{i}" in tracks
            reviewed = tracks["shared"]["reviewed_prs"]
            expected_prs = {str(i * 100 + j) for i in range(WORKERS)
                            for j in range(WRITES_PER_WORKER)}
            missing = sorted(expected_prs - set(reviewed))
            assert not missing, (
                f"round {r}: Scribe lost review records {missing} — "
                f"shared track holds {sorted(reviewed)}"
            )
            _assert_no_tmp_leftovers(tmp_path)

    def test_state_file_always_parseable_while_processes_write(self, tmp_path):
        """A reader must never observe a truncated or half-written state file."""
        state_path = tmp_path / "atomic.json"
        box = tmp_path / "atomic-box"
        _seed(state_path, _filler())

        procs = [_spawn("modify", i, state_path, box, WORKERS) for i in range(WORKERS)]
        reads = 0
        while any(p.poll() is None for p in procs):
            with open(state_path) as f:
                raw = f.read()
            try:
                json.loads(raw)
            except json.JSONDecodeError as e:  # pragma: no cover - failure path
                for p in procs:
                    p.kill()
                pytest.fail(f"state file observed non-JSON mid-write: {e}")
            reads += 1
            time.sleep(0.002)
        for p in procs:
            _, err = p.communicate()
            assert p.returncode == 0, f"worker failed: {err}"

        assert reads > 0, "poller never sampled the state file"
        _assert_no_tmp_leftovers(tmp_path)


class TestCrossProcessLockIsReal:
    """The lock must actually block a second process, not just be taken."""

    def test_second_process_blocks_while_lock_held(self, tmp_path):
        import fcntl

        from riptide.pipeline import work_state

        state_path = tmp_path / "locked.json"
        box = tmp_path / "locked-box"
        _seed(state_path, {})
        monkey_state = str(state_path)
        lock_path = monkey_state + ".lock"

        # Take the same sidecar lock the module uses, from a separate fd.
        holder = open(lock_path, "w")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        try:
            proc = _spawn("modify", 0, state_path, box, 1)
            time.sleep(1.0)
            assert proc.poll() is None, "worker finished while the lock was held — not serialized"
        finally:
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()

        proc.wait(timeout=WORKER_TIMEOUT)
        final = _read_valid_json(state_path)
        assert "proc-0-0" in final["tracks"]

    def test_lock_sidecar_is_created_next_to_state_file(self, tmp_path, monkeypatch):
        from riptide.pipeline import work_state

        state_path = tmp_path / "sidecar.json"
        monkeypatch.setattr(work_state, "WORK_STATE_PATH", str(state_path))
        work_state.modify_state(lambda s: s.setdefault("tracks", {}).setdefault("t", {}))
        assert (tmp_path / "sidecar.json.lock").exists()
        assert not list(tmp_path.glob("*.tmp"))


class TestReentrancy:
    """Re-entrancy is deliberate: nested critical sections must not deadlock."""

    @pytest.fixture
    def isolated_state(self, tmp_path, monkeypatch):
        from riptide.pipeline import work_state

        path = tmp_path / "reentrant.json"
        monkeypatch.setattr(work_state, "WORK_STATE_PATH", str(path))
        yield work_state, path
        # Depth must always unwind, even if a section raised.
        assert work_state._lock_depth == 0

    def test_modify_state_nested_inside_modify_state(self, isolated_state):
        """A fn that calls modify_state again completes and keeps both updates."""
        work_state, path = isolated_state
        order = []

        def outer(state):
            state.setdefault("tracks", {})["outer"] = {"status": "active"}
            work_state.modify_state(
                lambda s: s["tracks"].__setitem__("inner", {"status": "active"})
            )
            order.append("inner-done")
            state["tracks"]["outer_after"] = {"status": "done"}

        work_state.modify_state(outer)

        assert order == ["inner-done"]
        final = json.loads(path.read_text())
        assert set(final["tracks"]) == {"outer", "inner", "outer_after"}

    def test_read_state_inside_modify_state_does_not_deadlock(self, isolated_state):
        """A fn that reads the state mid-transaction sees the pending mutation."""
        work_state, path = isolated_state
        seen = {}

        def _do(state):
            state.setdefault("tracks", {})["t"] = {"status": "pending"}
            seen["mid"] = work_state.read_state()["tracks"]["t"]["status"]

        work_state.modify_state(_do)

        assert seen["mid"] == "pending"
        assert json.loads(path.read_text())["tracks"]["t"]["status"] == "pending"

    def test_raising_fn_releases_lock_and_depth(self, isolated_state):
        work_state, path = isolated_state

        def _boom(state):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            work_state.modify_state(_boom)

        assert work_state._lock_depth == 0
        work_state.modify_state(lambda s: s["tracks"].__setitem__("after", {"status": "ok"}))
        assert "after" in json.loads(path.read_text())["tracks"]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        _worker(sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5], int(sys.argv[6]))
    else:
        raise SystemExit("this file is a pytest module; run pytest on it")
