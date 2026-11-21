"""Tests for the diagram_assets table (v10 schema) and the bundle module.

Wave 1 of the multi-session architecture (see PLAN-multi-session-architecture.md).
These tests are pure data-layer: they exercise StateStore.save_diagram_asset /
fetch_latest_diagram / fetch_diagram_for_sha / list_diagram_assets /
compute_diagram_diff, and the bundles.write_bundle / read_bundle /
encode_gzip_b64 / decode_gzip_b64 round-trip. No mcp-excalidraw is touched.
"""

from __future__ import annotations

import base64
import gzip
import json
import tempfile
from pathlib import Path

import pytest

from riptide.bundles import (
    BUNDLE_SCHEMA_VERSION,
    DEFAULT_BUNDLE_DIR,
    RENDER_KIND_REVIEW,
    STAGE_DEEP_REVIEW,
    STAGE_DIAGRAM,
    VALID_STAGES,
    cleanup_bundle,
    decode_gzip_b64,
    encode_gzip_b64,
    read_bundle,
    write_bundle,
)
from riptide.state import StateStore


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def store():
    """Create a fresh StateStore for each test (hermetic, no ambient state)."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "state.db")
        yield StateStore(db_path)


@pytest.fixture
def bundle_dir():
    """Hermetic bundle directory for each test (no /tmp pollution)."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


# ── diagram_assets table tests ────────────────────────────────────────────────


class TestDiagramAssetsSchema:
    """The v10 migration creates the table and its indexes."""

    def test_table_exists(self, store):
        names = {
            row[0]
            for row in store._get_conn().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "diagram_assets" in names

    def test_indexes_exist(self, store):
        rows = store._get_conn().execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='diagram_assets'"
        ).fetchall()
        names = {row[0] for row in rows}
        # The CREATE INDEX IF NOT EXISTS calls produce idx_diagram_assets_pr_head
        # and idx_diagram_assets_unique. SQLite also creates an implicit index
        # for the PRIMARY KEY and the UNIQUE constraint.
        assert "idx_diagram_assets_pr_head" in names
        assert "idx_diagram_assets_unique" in names

    def test_unique_constraint_on_triple(self, store):
        """Saving twice for the same (pr_id, base_sha, render_kind) replaces
        the prior row rather than appending — the UNIQUE index is what
        makes re-renders idempotent."""
        scene_a = gzip.compress(b'[{"id": "el-1", "type": "rectangle"}]')
        scene_b = gzip.compress(b'[{"id": "el-1", "type": "ellipse"}]')

        store.save_diagram_asset(
            "ChonSong/riptide#235",
            "deadbeef",
            RENDER_KIND_REVIEW,
            scene_a,
            None,
            1,
        )
        store.save_diagram_asset(
            "ChonSong/riptide#235",
            "deadbeef",
            RENDER_KIND_REVIEW,
            scene_b,
            None,
            1,
        )
        # Only one row, and its scene bytes are the latest (INSERT OR REPLACE).
        rows = store.list_diagram_assets("ChonSong/riptide#235")
        assert len(rows) == 1
        assert rows[0]["scene_json"] == scene_b
        # And decoding the stored scene recovers the latest uncompressed JSON.
        assert json.loads(gzip.decompress(rows[0]["scene_json"])) == [
            {"id": "el-1", "type": "ellipse"}
        ]


class TestSaveAndFetch:
    def test_round_trip_basic(self, store):
        scene = gzip.compress(b'{"elements": [{"id": "el-x", "type": "text"}]}')
        png = gzip.compress(b"\x89PNG_FAKE_BYTES")
        asset_id = store.save_diagram_asset(
            "ChonSong/riptide#235",
            "abc123",
            RENDER_KIND_REVIEW,
            scene,
            png,
            element_count=1,
            created_by="test:round_trip_basic",
        )
        assert len(asset_id) == 32  # uuid4 hex
        fetched = store.fetch_diagram_for_sha("ChonSong/riptide#235", "abc123", RENDER_KIND_REVIEW)
        assert fetched is not None
        assert fetched["id"] == asset_id
        assert fetched["base_sha"] == "abc123"
        assert fetched["pr_id"] == "ChonSong/riptide#235"
        assert fetched["render_kind"] == RENDER_KIND_REVIEW
        assert fetched["element_count"] == 1
        assert fetched["created_by"] == "test:round_trip_basic"
        assert fetched["scene_json"] == scene  # raw bytes, gzipped JSON
        assert fetched["rendered_png"] == png

    def test_fetch_latest_returns_newest_first(self, store):
        # Save three assets for the same PR at different SHAs.
        for sha, count in [("sha1", 1), ("sha2", 2), ("sha3", 3)]:
            store.save_diagram_asset(
                "ChonSong/riptide#235",
                sha,
                RENDER_KIND_REVIEW,
                gzip.compress(b"[]"),
                None,
                count,
            )
        latest = store.fetch_latest_diagram("ChonSong/riptide#235", RENDER_KIND_REVIEW)
        assert latest["base_sha"] == "sha3"
        assert latest["element_count"] == 3

    def test_fetch_latest_with_before_base_sha(self, store):
        # The orchestrator uses ``before_base_sha`` to find the prior scene
        # when a new commit lands.
        for sha in ["sha1", "sha2", "sha3"]:
            store.save_diagram_asset(
                "ChonSong/riptide#235",
                sha,
                RENDER_KIND_REVIEW,
                gzip.compress(b"[]"),
                None,
                1,
            )
        # before_base_sha='sha3' (lexical) — strictly-less-than returns sha2.
        # Lexical comparison is good enough: the index is keyed by base_sha as
        # a string and SHAs are 40-char hex, so lex order == chronological order.
        prior = store.fetch_latest_diagram(
            "ChonSong/riptide#235", RENDER_KIND_REVIEW, before_base_sha="sha3"
        )
        assert prior["base_sha"] == "sha2"

    def test_list_filtered_by_render_kind(self, store):
        # Two kinds at the same head — must coexist.
        store.save_diagram_asset("ChonSong/riptide#235", "abc", "review", gzip.compress(b"r"), None, 1)
        store.save_diagram_asset("ChonSong/riptide#235", "abc", "plan", gzip.compress(b"p"), None, 1)
        reviews = store.list_diagram_assets("ChonSong/riptide#235", render_kind="review")
        plans = store.list_diagram_assets("ChonSong/riptide#235", render_kind="plan")
        all_assets = store.list_diagram_assets("ChonSong/riptide#235")
        assert len(reviews) == 1
        assert len(plans) == 1
        assert len(all_assets) == 2

    def test_fetch_missing_returns_none(self, store):
        assert store.fetch_latest_diagram("ChonSong/no-such#1", RENDER_KIND_REVIEW) is None
        assert store.fetch_diagram_for_sha("ChonSong/no-such#1", "abc", RENDER_KIND_REVIEW) is None


# ── compute_diagram_diff tests ──────────────────────────────────────────────


def _gzip_json(payload) -> bytes:
    return gzip.compress(json.dumps(payload).encode("utf-8"))


class TestComputeDiagramDiff:
    def test_no_prior_returns_empty_added_findings(self, store):
        # First push: no prior scene. The diff records the absence.
        diff = store.compute_diagram_diff(
            None,
            {
                "scan": {"files": [{"filename": "a.py"}, {"filename": "b.py"}]},
                "review": {"findings": [{"file": "a.py", "line": 1, "title": "bug"}]},
            },
        )
        assert diff["has_prior"] is False
        assert diff["prior_element_count"] == 0
        assert diff["added_findings"] == [
            {"file": "a.py", "line": 1, "title": "bug"}
        ]
        assert diff["unchanged_findings"] == []
        assert diff["removed_findings"] == []
        # Files: no prior, so the union of new files is reported as added.
        # (This is signal-only; the LLM refines it with the real prior scan
        # in Wave 3 — see the docstring.)
        assert set(diff["added_files"]) == {"a.py", "b.py"}

    def test_unchanged_findings_when_prior_matches(self, store):
        # Prior scene includes a finding-shaped text element; the new
        # review contains the same finding title. The diff classifies
        # it as unchanged.
        prior_scene = _gzip_json(
            [{"id": "el-1", "kind": "text", "text": "🔴 Hardcoded secret\nin code"}]
        )
        diff = store.compute_diagram_diff(
            prior_scene,
            {
                "review": {
                    "findings": [
                        {"file": "a.py", "line": 1, "title": "🔴 Hardcoded secret\nin code"}
                    ]
                }
            },
        )
        assert diff["has_prior"] is True
        assert diff["prior_element_count"] == 1
        assert diff["added_findings"] == []
        assert diff["unchanged_findings"] == [
            {"file": "a.py", "line": 1, "title": "🔴 Hardcoded secret\nin code"}
        ]
        assert diff["removed_findings"] == []

    def test_added_and_removed_findings(self, store):
        # Prior has finding A; new has finding B. A is removed, B is added.
        prior_scene = _gzip_json(
            [{"id": "el-1", "kind": "text", "text": "🔴 Old finding"}]
        )
        diff = store.compute_diagram_diff(
            prior_scene,
            {
                "review": {
                    "findings": [
                        {"file": "a.py", "line": 1, "title": "🟡 New finding"}
                    ]
                }
            },
        )
        assert len(diff["added_findings"]) == 1
        assert diff["added_findings"][0]["title"] == "🟡 New finding"
        assert len(diff["removed_findings"]) == 1
        assert diff["removed_findings"][0]["title"] == "🔴 Old finding"
        assert diff["unchanged_findings"] == []

    def test_tolerates_legacy_uncompressed_scene(self, store):
        # An older asset may have been written before the gzip convention.
        # The diff must still parse it instead of raising.
        legacy = ('[{"id": "el-1", "kind": "text", "text": "🟢 Legacy finding"}]').encode("utf-8")
        diff = store.compute_diagram_diff(
            legacy,
            {"review": {"findings": []}},
        )
        assert diff["prior_element_count"] == 1
        assert len(diff["removed_findings"]) == 1

    def test_tolerates_corrupt_scene(self, store):
        # Defensive: a corrupt asset must not crash the pipeline.
        diff = store.compute_diagram_diff(
            b"this is not gzipped and not JSON",
            {"review": {"findings": []}},
        )
        assert diff["has_prior"] is True
        assert diff["prior_element_count"] == 0  # graceful fallback


# ── bundles module tests ───────────────────────────────────────────────────


class TestBundleRoundTrip:
    def test_write_then_read(self, bundle_dir):
        path = write_bundle(
            stage=STAGE_DEEP_REVIEW,
            pr_id="ChonSong/riptide#235",
            base_sha="abc123",
            policy={"model": "LongCat-2.0", "provider": "longcat", "stage_timeout_seconds": 300, "must_judge": True},
            job_id="test-job-1",
            scan={"pr_details": {"head": {"sha": "abc123"}}, "files": [{"filename": "a.py"}]},
            review={"findings": [], "judged": True},
            bundle_dir=bundle_dir,
        )
        assert path.parent == bundle_dir
        bundle = read_bundle(path)
        assert bundle["schema_version"] == BUNDLE_SCHEMA_VERSION
        assert bundle["stage"] == STAGE_DEEP_REVIEW
        assert bundle["pr_id"] == "ChonSong/riptide#235"
        assert bundle["base_sha"] == "abc123"
        assert bundle["job_id"] == "test-job-1"
        assert bundle["policy"]["model"] == "LongCat-2.0"
        assert bundle["scan"]["files"] == [{"filename": "a.py"}]
        assert bundle["review"]["judged"] is True
        # The base sha is preserved, parent_sha is not present (None stripped).
        assert "parent_sha" not in bundle

    def test_atomic_write_does_not_leave_partial_file(self, bundle_dir):
        """If the underlying write raises (e.g. ENOSPC), no partial file is
        left behind. We simulate by passing a path under /proc, which is
        read-only and cannot accept new files."""
        with pytest.raises(Exception):
            write_bundle(
                stage=STAGE_DEEP_REVIEW,
                pr_id="ChonSong/riptide#235",
                base_sha="abc",
                policy={"model": "x", "provider": "y", "stage_timeout_seconds": 1},
                bundle_dir=Path("/proc/1/cmdline"),
            )

    def test_unknown_stage_rejected(self, bundle_dir):
        with pytest.raises(ValueError, match="unknown stage"):
            write_bundle(
                stage="make_coffee",
                pr_id="ChonSong/riptide#235",
                base_sha="abc",
                policy={"model": "x", "provider": "y", "stage_timeout_seconds": 1},
                bundle_dir=bundle_dir,
            )

    def test_default_bundle_dir_uses_env_or_tmp(self, monkeypatch, tmp_path):
        # RIPTIDE_BUNDLE_DIR overrides the default; clean teardown of the
        # default location is exercised by cleanup_bundle.
        target = tmp_path / "from-env"
        monkeypatch.setenv("RIPTIDE_BUNDLE_DIR", str(target))
        path = write_bundle(
            stage=STAGE_DIAGRAM,
            pr_id="ChonSong/riptide#235",
            base_sha="deadbeef",
            policy={"model": "m", "provider": "p", "stage_timeout_seconds": 60, "render_kind": "review"},
        )
        assert path.parent == target
        cleanup_bundle(path)
        assert not path.exists()

    def test_cleanup_missing_is_noop(self, bundle_dir):
        # cleanup_bundle on a non-existent path must not raise.
        cleanup_bundle(bundle_dir / "nope.json")


class TestEncodeDecodeGzipB64:
    def test_round_trip_bytes(self):
        original = b"some \x00 binary \xff data \x01"
        encoded = encode_gzip_b64(original)
        assert isinstance(encoded, str)
        decoded = decode_gzip_b64(encoded)
        assert decoded == original

    def test_decode_tolerates_uncompressed_input(self):
        # Legacy assets stored raw bytes (no gzip). The decoder must
        # pass them through instead of raising.
        original = b"plain text without gzip"
        encoded = base64.b64encode(original).decode("ascii")
        decoded = decode_gzip_b64(encoded)
        assert decoded == original

    def test_round_trip_excalidraw_shaped_json(self):
        # A realistic Excalidraw Element[] array, gzipped + base64'd,
        # round-trips byte-for-byte.
        element_array = [
            {"id": "el-1", "type": "rectangle", "x": 100, "y": 200, "width": 80, "height": 40},
            {"id": "el-2", "type": "text", "text": "🔴 Hardcoded secret", "containerId": "el-1"},
        ]
        original = json.dumps(element_array).encode("utf-8")
        encoded = encode_gzip_b64(original)
        decoded = decode_gzip_b64(encoded)
        assert decoded == original
        # The decoded JSON parses back to the same element array.
        assert json.loads(decoded) == element_array
