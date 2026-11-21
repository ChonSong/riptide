#!/usr/bin/env python3
# riptide/bundles.py — context-bundle file format for the multi-session architecture.
#
# Each spawned Herms session in the riptide pipeline (Stage 3 Deep Review, Stage 4
# Diagram) reads a JSON context bundle from a known path on disk. The orchestrator
# (the riptide service running on the prod box) writes the bundle before the cron
# spawn; the session reads it via the same "Read the prompt from /tmp/riptide-...
# and execute it. After execution, delete the file." mechanism the deepthink
# path already uses (see riptide/deepthink.py:_spawn_deepthink).
#
# Why a file (not an env var or stdin)?
#   - Bundles can be 100s of KB (Excalidraw scene JSON, PNG bytes, review
#     findings). Env vars and the Herms "self-contained prompt" arg both have
#     practical size limits on the order of 64-128 KB; ARG_MAX on the receiving
#     side is the same wall.
#   - Files survive retries: a failed Stage 3 spawn that retries re-reads the
#     same bundle file the orchestrator wrote, so the second attempt sees
#     exactly the same inputs as the first. In-memory passing would force the
#     orchestrator to re-build on every retry.
#   - A file is naturally observable: an operator can ``cat`` the bundle to see
#     what the agent saw. Debugging is much easier than replaying a spawn from
#     env-var string soup.
#
# Schema (all fields optional unless marked required):
#
#   {
#     "schema_version": 1,                              # required, integer
#     "job_id": "riptide-deep-review-ChonSong-riptide-235-3d31089-7a1b2c",
#     "stage": "deep_review" | "diagram",                # required, enum
#     "pr_id": "ChonSong/riptide#235",                  # required
#     "base_sha": "3d31089...",                          # required
#     "parent_sha": "...",                                # optional, prior head if patch
#     "scan": {                                          # optional (Stage 1 output)
#       "pr_details": {...},
#       "files": [...],
#       "pr_created_at": "...",
#       "triggered_at": "..."
#     },
#     "graph": {                                         # optional (Stage 2 output)
#       "distance_map": {...},
#       "communities": [...],
#       "god_nodes": [...]
#     },
#     "review": {                                        # Stage 3 output (in Stage 4's bundle)
#       "findings": [...],
#       "severity_table": [...],
#       "suggested_changes": [...],
#       "judged": true,
#       "model": "...",
#       "provider": "..."
#     },
#     "diagram_assets": {                                # optional, current+prior asset refs
#       "review": {"asset_id": "uuid", "rendered_png_b64": "..."},
#       "plan": null
#     },
#     "prior_diagram_asset": {                            # optional, for delta-patch
#       "asset_id": "uuid",
#       "scene_json_b64": "...",                            # gzipped .excalidraw, base64
#       "rendered_png_b64": "...",
#       "diff": {                                            # pre-computed by Stage 4's orchestrator
#         "added_files": [...],
#         "removed_files": [...],
#         "added_findings": [...],
#         "removed_findings": [...],
#         "unchanged_findings": [...]
#       }
#     } | null,
#     "policy": {                                        # required
#       "model": "LongCat-2.0",                            # required, .env-driven
#       "provider": "longcat",                             # required, .env-driven
#       "max_input_tokens": 12000,                         # optional
#       "stage_timeout_seconds": 300,                      # required
#       "must_judge": true,                                 # Stage 3
#       "render_kind": "review" | "plan",                   # Stage 4
#       "store": "state_db:diagram_assets"                 # Stage 4
#     }
#   }
#
# Binary data (PNG bytes, gzipped JSON) is base64-encoded in transit. The agent
# decodes to disk before driving mcp-excalidraw; the orchestrator re-encodes
# from the saved asset row. This keeps the bundle JSON-only and human-readable.

from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("riptide.bundles")

# Where bundles live on disk. /tmp is the existing riptide convention
# (see riptide/deepthink.py:_spawn_deepthink's prompt file pattern). Override
# with RIPTIDE_BUNDLE_DIR for hermetic tests.
DEFAULT_BUNDLE_DIR = Path(
    os.environ.get(
        "RIPTIDE_BUNDLE_DIR",
        "/tmp",
    )
)

# Schema version. Bump when the bundle shape changes in a way the receiving
# agent needs to know about. Wave 1 ships 1; Wave 2 may introduce new top-level
# fields; Wave 3 will add the diagram_assets section.
BUNDLE_SCHEMA_VERSION = 1

# Stage identifiers. The receiving agent uses these to know which fields are
# required. The orchestrator sets the stage at write time.
STAGE_DEEP_REVIEW = "deep_review"
STAGE_DIAGRAM = "diagram"
VALID_STAGES = frozenset({STAGE_DEEP_REVIEW, STAGE_DIAGRAM})

# Render kinds. ``review`` is the default for Stage 4; ``plan`` is a future
# mode for bot 2b (autonomous fix) that may need a different diagram.
RENDER_KIND_REVIEW = "review"
RENDER_KIND_PLAN = "plan"


def write_bundle(
    stage: str,
    pr_id: str,
    base_sha: str,
    policy: dict,
    *,
    job_id: Optional[str] = None,
    parent_sha: Optional[str] = None,
    scan: Optional[dict] = None,
    graph: Optional[dict] = None,
    review: Optional[dict] = None,
    diagram_assets: Optional[dict] = None,
    prior_diagram_asset: Optional[dict] = None,
    bundle_dir: Optional[Path] = None,
) -> Path:
    """Write a context bundle to disk and return the path.

    The file is named ``riptide-bundle-{job_id}.json`` inside ``bundle_dir``
    (or RIPTIDE_BUNDLE_DIR, or /tmp). The receiving spawned session reads
    it via the existing prompt mechanism ("Read the prompt from
    /tmp/riptide-bundle-... and execute it. After execution, delete the
    file."). The path is also returned so the orchestrator can pass it
    into the prompt body that the agent sees.

    The schema_version is stamped automatically. Any field passed in is
    validated only enough to produce a sensible error: the agent that
    reads the bundle is the source of truth on the field contract.
    """
    if stage not in VALID_STAGES:
        raise ValueError(
            f"unknown stage {stage!r}; must be one of {sorted(VALID_STAGES)}"
        )
    if not job_id:
        job_id = f"riptide-{stage.replace('_', '-')}-{pr_id.replace('/', '-').replace('#', '-')}-{base_sha[:12]}-{uuid.uuid4().hex[:8]}"
    bundle = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "job_id": job_id,
        "stage": stage,
        "pr_id": pr_id,
        "base_sha": base_sha,
        "parent_sha": parent_sha,
        "scan": scan,
        "graph": graph,
        "review": review,
        "diagram_assets": diagram_assets,
        "prior_diagram_asset": prior_diagram_asset,
        "policy": dict(policy),  # copy so caller mutations don't leak
    }
    # Strip None values so the bundle stays tight; the agent treats missing
    # keys as "not supplied" anyway.
    bundle = {k: v for k, v in bundle.items() if v is not None or k in {"schema_version", "job_id", "stage", "pr_id", "base_sha", "policy"}}

    target_dir = (
        Path(bundle_dir) if bundle_dir is not None
        else Path(os.environ.get("RIPTIDE_BUNDLE_DIR", "/tmp"))
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"riptide-bundle-{job_id}.json"
    # Atomic write: write to a temp file in the same directory, then rename.
    # Avoids leaving a half-written bundle if the spawn is interrupted.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target_dir), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(bundle, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp_name, target)
    except Exception:
        # Clean up the temp file on any error so /tmp doesn't accumulate.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    log.info("Wrote context bundle %s (%d bytes)", target, target.stat().st_size)
    return target


def read_bundle(path: Path) -> dict:
    """Read a context bundle from disk. Pure deserialisation — no gzip,
    no base64, no schema validation beyond JSON shape. The agent is
    responsible for the field contract.
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def encode_gzip_b64(blob: bytes) -> str:
    """Encode a bytes payload as gzipped base64 for transit through the
    JSON bundle. The receiving agent calls :func:`decode_gzip_b64` to
    recover the original bytes.
    """
    return base64.b64encode(gzip.compress(blob)).decode("ascii")


def decode_gzip_b64(text: str) -> bytes:
    """Inverse of :func:`encode_gzip_b64`. Tolerates a non-gzipped input
    (legacy assets, raw bytes) by passing through.
    """
    raw = base64.b64decode(text)
    try:
        return gzip.decompress(raw)
    except (OSError, EOFError):
        return raw


def cleanup_bundle(path: Path) -> None:
    """Delete a bundle file. Idempotent: missing files are not an error.
    The spawned session's prompt explicitly says to delete the file after
    execution, so the agent is the primary caller. The orchestrator
    also calls this on stage failure to avoid /tmp accumulation.
    """
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as e:
        log.warning("Could not remove bundle %s: %s", path, e)


__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "DEFAULT_BUNDLE_DIR",
    "RENDER_KIND_PLAN",
    "RENDER_KIND_REVIEW",
    "STAGE_DEEP_REVIEW",
    "STAGE_DIAGRAM",
    "VALID_STAGES",
    "cleanup_bundle",
    "decode_gzip_b64",
    "encode_gzip_b64",
    "read_bundle",
    "write_bundle",
]
