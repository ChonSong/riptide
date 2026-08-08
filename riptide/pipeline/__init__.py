#!/usr/bin/env python3
"""pipeline — Riptide orchestration pipeline.

Workers:
    Probe    — Gathers deterministic context (context_bundle, diff_analyzer, graphify)
    Judge    — Evaluates diffs, dedups findings
    Artisan  — Creates/modifies files (excalidraw, specs, configs)
    Engine   — Executes exact shell commands
    Warden   — Verifies outputs meet acceptance criteria
    Scribe   — Updates state, posts PR comments

Orchestrator:
    Conductor — Reads work-state.json, dispatches workers, monitors, updates state
"""

from .work_state import read_state, write_state, get_track, create_track, create_workstream
from .conductor import Conductor, create_pr_review_pipeline
from .roles import WorkerBrief, ROLES
from .recovery import StallSignal, FailureType, detect_stall, recover

__all__ = [
    "Conductor",
    "create_pr_review_pipeline",
    "WorkerBrief",
    "ROLES",
    "StallSignal",
    "FailureType",
    "detect_stall",
    "recover",
    "read_state",
    "write_state",
    "get_track",
    "create_track",
    "create_workstream",
]
