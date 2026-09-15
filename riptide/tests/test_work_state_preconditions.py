"""The precondition `create_workstream` used to require silently.

`create_workstream` indexed straight into `state["tracks"][track_id]`, so calling
it before `create_track` raised `KeyError` — deterministically, with one thread
and one call, before any interleaving was possible. The Conductor always creates
the track first, which is why this never surfaced in production; but it made the
function unusable on its own, and it is what failing `TestCreateWorkstream` and
`TestWorkStateThreadSafety::test_concurrent_writes` were actually reporting once
the suite stopped depending on leftover ambient state.

The strictness of `update_track`/`update_workstream` is deliberate and asserted
here too: updating something that does not exist really is an error.
"""

import pytest

import riptide.pipeline.work_state as ws


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    """Point the store at a temp file so tests never touch ambient state."""
    path = tmp_path / "work-state.json"
    monkeypatch.setattr(ws, "WORK_STATE_PATH", str(path))
    return path


def test_create_workstream_creates_a_missing_track(state_file):
    created = ws.create_workstream("track-1", "ws-1", role="probe")

    assert created["id"] == "ws-1"
    track = ws.get_track("track-1")
    assert track is not None, "the track must be created on demand"
    assert "ws-1" in track["workstreams"]
    assert ws.get_workstream("track-1", "ws-1")["role"] == "probe"


def test_create_workstream_preserves_existing_track_and_workstreams(state_file):
    ws.create_track("track-2", "Review", "deepthink", {})
    ws.create_workstream("track-2", "ws-a", role="probe")
    first_track = ws.get_track("track-2")

    ws.create_workstream("track-2", "ws-b", role="judge")

    track = ws.get_track("track-2")
    assert set(track["workstreams"]) == {"ws-a", "ws-b"}
    assert track["name"] == first_track["name"]
    assert track["phase"] == first_track["phase"]


def test_create_workstream_tolerates_a_track_missing_its_workstreams_map(
    state_file, tmp_path
):
    """A track written by an older/partial writer must not break creation."""
    ws.create_track("track-3", "Review", "deepthink", {})
    state = ws.read_state()
    del state["tracks"]["track-3"]["workstreams"]
    ws.write_state(state)

    ws.create_workstream("track-3", "ws-c", role="scribe")

    assert ws.get_workstream("track-3", "ws-c")["role"] == "scribe"


def test_created_track_matches_create_track_shape(state_file):
    ws.create_workstream("on-demand", "ws-1", role="probe")
    ws.create_track("explicit", "Review", "deepthink", {})

    on_demand = ws.get_track("on-demand")
    explicit = ws.get_track("explicit")

    assert set(on_demand) == set(explicit), "_new_track is the single source of truth"
    assert on_demand["workstreams"]["ws-1"]["status"] == "pending"
    # The track id is the dict key, not a field inside the record.
    assert "on-demand" in ws.read_state()["tracks"]


def test_update_track_still_rejects_a_missing_track(state_file):
    with pytest.raises(KeyError):
        ws.update_track("never-created", {"phase": "done"})


def test_update_workstream_still_rejects_a_missing_workstream(state_file):
    ws.create_track("track-4", "Review", "deepthink", {})
    with pytest.raises(KeyError):
        ws.update_workstream("track-4", "nope", status="done")
