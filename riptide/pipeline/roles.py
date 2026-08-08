#!/usr/bin/env python3
"""roles.py — Worker role definitions and schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ── Role definitions ────────────────────────────────────────────────────────

ROLES = {
    "probe": {
        "description": "Gathers deterministic context via Riptide tools",
        "tools": ["terminal"],  # runs diff_analyzer, context_bundle, graphify, StateStore
        "output_format": "json",
        "output_path_key": "context_path",
    },
    "judge": {
        "description": "Evaluates diffs against acceptance criteria, dedups findings",
        "tools": ["read_file", "write_file"],
        "output_format": "json",
        "output_path_key": "findings_path",
    },
    "artisan": {
        "description": "Creates/modifies files with exact content",
        "tools": ["read_file", "write_file", "patch", "terminal"],
        "output_format": "json",
        "output_path_key": "artifact_path",
    },
    "engine": {
        "description": "Executes exact shell commands, captures exit code",
        "tools": ["terminal"],
        "output_format": "json",
        "output_path_key": "result_path",
    },
    "warden": {
        "description": "Verifies outputs meet acceptance criteria",
        "tools": ["read_file", "terminal"],
        "output_format": "json",
        "output_path_key": "verification_path",
    },
    "scribe": {
        "description": "Updates work-state.json and posts GitHub comments",
        "tools": ["read_file", "write_file", "terminal"],
        "output_format": "json",
        "output_path_key": "record_path",
    },
}

# ── Worker brief schema ────────────────────────────────────────────────────

@dataclass
class WorkerBrief:
    """Structured brief for a worker dispatch."""
    role: str
    name: str
    track: str
    workstream: str
    pipeline: str  # e.g., "probe → judge → artisan → engine → scribe → warden"
    position: str  # e.g., "after Probe context gathered, before Artisan diagram"
    key_facts: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)
    acceptance: dict = field(default_factory=dict)
    recovery: dict = field(default_factory=dict)
    output_protocol: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "your_role": self.role,
            "your_name": self.name,
            "track": self.track,
            "workstream": self.workstream,
            "pipeline": self.pipeline,
            "your_position": self.position,
            "key_facts": self.key_facts,
            "inputs": self.inputs,
            "acceptance": self.acceptance,
            "recovery": self.recovery,
            "output_protocol": self.output_protocol,
        }


# ── Finding schema (for Arbiter output) ────────────────────────────────────

FINDING_SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "required": ["severity", "title"],
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": ["integer", "string"]},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "warning", "suggestion", "info", "approved"]
                    },
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
            },
        },
    },
}

# ── Context schema (for Probe output) ───────────────────────────────────────

CONTEXT_SCHEMA = {
    "type": "object",
    "required": ["pr_data", "diff_report", "bundle", "already_reviewed"],
    "properties": {
        "pr_data": {"type": "object"},
        "diff_report": {"type": "object"},
        "bundle": {"type": "object"},
        "graphify": {"type": "object"},
        "already_reviewed": {"type": "boolean"},
        "previous_findings": {"type": "array"},
        "key_facts": {"type": "object"},
    },
}
