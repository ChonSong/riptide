#!/usr/bin/env python3
"""recovery.py — Stall detection and recovery protocol for Riptide Pipeline."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class FailureType(Enum):
    TRANSIENT = "transient"          # Network, server down → retry same brief
    EXPLORATION_DRIFT = "drift"      # Went researching → tighten brief
    AMBIGUOUS = "ambiguous"          # Requirements unclear → escalate to user
    IMPOSSIBLE = "impossible"        # External blocker → mark blocked
    TIMEOUT = "timeout"              # Wall-clock exceeded → kill + inspect
    LOOP = "loop"                    # Same tool call repeated → kill + tighten
    SCHEMA_VIOLATION = "schema"      # Output format wrong → retry with schema


@dataclass
class StallSignal:
    """Detected stall condition."""
    failure_type: FailureType
    worker_name: str
    workstream: str
    message: str
    partial_output: Optional[dict] = None
    retry_count: int = 0


# ── Detection ───────────────────────────────────────────────────────────────


def detect_stall(
    worker_name: str,
    workstream: str,
    start_time: float,
    expected_duration: int,
    tool_calls: list[dict],
    output_delta: bool,
    turn_count: int,
) -> Optional[StallSignal]:
    """Detect if a worker has stalled. Returns StallSignal if stalled."""
    
    elapsed = time.time() - start_time
    
    # Wall-clock timeout (2× expected)
    if elapsed > expected_duration * 2:
        return StallSignal(
            failure_type=FailureType.TIMEOUT,
            worker_name=worker_name,
            workstream=workstream,
            message=f"Exceeded 2× expected duration ({expected_duration}s, elapsed {elapsed:.0f}s)",
        )
    
    # Tool-call loop (same tool >3 times with identical args)
    if len(tool_calls) >= 3:
        last_three = tool_calls[-3:]
        if (
            all(tc.get("tool") == last_three[0].get("tool") for tc in last_three)
            and all(tc.get("args") == last_three[0].get("args") for tc in last_three)
        ):
            return StallSignal(
                failure_type=FailureType.LOOP,
                worker_name=worker_name,
                workstream=workstream,
                message=f"Same tool called 3× with identical args: {last_three[0].get('tool')}",
            )
    
    # No output delta for 2+ turns (reasoning in circles)
    if turn_count >= 2 and not output_delta:
        return StallSignal(
            failure_type=FailureType.EXPLORATION_DRIFT,
            worker_name=worker_name,
            workstream=workstream,
            message="No output produced in 2+ turns — likely reasoning in circles",
        )
    
    return None


# ── Recovery ─────────────────────────────────────────────────────────────────


def recover(signal: StallSignal, brief: dict) -> dict:
    """Generate recovery action based on stall type."""
    
    if signal.failure_type == FailureType.TRANSIENT:
        return {
            "action": "retry",
            "brief": brief,
            "note": "Transient failure — retry same brief",
        }
    
    elif signal.failure_type == FailureType.TIMEOUT:
        return {
            "action": "kill_and_inspect",
            "brief": brief,
            "note": "Timeout — kill worker, inspect partial output, retry or partial-credit",
        }
    
    elif signal.failure_type == FailureType.LOOP:
        # Tighten brief: add explicit "if X fails, skip to Y"
        tightened = dict(brief)
        tightened["constraints"] = tightened.get("constraints", [])
        tightened["constraints"].append(
            "If a tool call fails twice, skip to next step. Do not retry."
        )
        return {
            "action": "retry_tightened",
            "brief": tightened,
            "note": "Tool-call loop detected — tightened brief with skip-on-failure",
        }
    
    elif signal.failure_type == FailureType.EXPLORATION_DRIFT:
        # Tighten brief: remove exploration, add exact commands
        tightened = dict(brief)
        tightened["constraints"] = tightened.get("constraints", [])
        tightened["constraints"].append(
            "Do not explore or research. Execute exact commands only."
        )
        tightened["constraints"].append(
            "If you don't know something, report it — don't investigate."
        )
        return {
            "action": "retry_tightened",
            "brief": tightened,
            "note": "Exploration drift detected — tightened brief with exact-commands-only",
        }
    
    elif signal.failure_type == FailureType.SCHEMA_VIOLATION:
        return {
            "action": "retry_with_schema",
            "brief": brief,
            "note": "Output schema violation — retry with schema enforcement",
        }
    
    elif signal.failure_type == FailureType.AMBIGUOUS:
        return {
            "action": "escalate",
            "brief": brief,
            "note": "Ambiguous requirements — escalate to user with options",
        }
    
    elif signal.failure_type == FailureType.IMPOSSIBLE:
        return {
            "action": "mark_blocked",
            "brief": brief,
            "note": "External blocker — mark workstream blocked, update key_facts",
        }
    
    return {"action": "unknown", "brief": brief, "note": "Unknown stall type"}


# ── Partial credit assessment ────────────────────────────────────────────────


def assess_partial_output(
    partial_output: dict,
    acceptance: dict,
) -> tuple[bool, list[str]]:
    """Check if partial output meets any acceptance criteria.
    
    Returns (accept_partial, list_of_failed_criteria).
    """
    failed = []
    
    for criterion, expected in acceptance.items():
        if criterion not in partial_output:
            failed.append(f"Missing: {criterion}")
        elif partial_output[criterion] != expected:
            failed.append(f"Mismatch: {criterion} (expected {expected}, got {partial_output[criterion]})")
    
    # Accept if at least one criterion met
    accept = len(failed) < len(acceptance) if acceptance else False
    
    return accept, failed
