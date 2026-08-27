#!/usr/bin/env python3
"""
async_conductor.py — State-machine conductor that chains stratified Hermes sessions.

Replaces the synchronous in-process Conductor with an async state machine:
- Each worker is a separate Hermes session (stratified context, skills, memory)
- Sessions communicate via work-state.json and temp files
- Conductor resumes on completion webhook (no polling)
- Failure recovery per-worker (retry just the failed stage)

Architecture:
    Conductor.run() → dispatch("probe") → Hermes session A completes
                  → webhook callback → Conductor.resume()
                  → dispatch("judge") → Hermes session B completes
                  → webhook callback → Conductor.resume()
                  → ... → dispatch("scribe") → track complete
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .work_state import (
    read_state, write_state, now, modify_state,
    get_track, get_workstream, create_track, create_workstream,
    update_track, update_workstream, next_pending_workstream, update_key_facts,
    get_stuck_tracks, cleanup_stuck_tracks, get_pipeline_status,
)
from .session_spawner import spawn_worker_session, ROLE_CONFIGS


def _set_started_at(track_id: str, ws_id: str) -> None:
    """Set the started_at timestamp for a workstream (for stuck-pipeline detection)."""
    def _do(state):
        ws = state["tracks"][track_id]["workstreams"][ws_id]
        ws["started_at"] = now()
    modify_state(_do)

log = logging.getLogger("riptide.async_conductor")


class AsyncConductor:
    """
    State-machine conductor that dispatches stratified Hermes sessions.

    Unlike the synchronous Conductor (which runs workers in-thread),
    this conductor dispatches each worker as a separate Hermes cron
    session and resumes when the session completes.
    """

    def __init__(self, track_id: str):
        self.track_id = track_id
        self.track = get_track(track_id)
        if not self.track:
            raise ValueError(f"Track {track_id} not found")

    def run(self) -> dict:
        """
        Run the state machine — dispatch the next pending workstream.

        Unlike the synchronous Conductor (which runs all workers in-thread),
        this dispatches ONE worker at a time. When that session completes,
        it calls resume() to dispatch the next worker.
        """
        next_item = next_pending_workstream(self.track_id)
        if not next_item:
            return {"track": self.track_id, "results": []}

        ws_id, ws = next_item
        result = self._dispatch_workstream(ws_id, ws)

        return {"track": self.track_id, "results": [result]}

    def resume(self, completed_workstream: str) -> dict:
        """
        Resume the state machine after a worker session completes.

        Called by the webhook handler when a Hermes session finishes.
        Reads the output from the completed workstream, validates it,
        and dispatches the next one.
        """
        # Refresh track state
        self.track = get_track(self.track_id)
        if not self.track:
            raise ValueError(f"Track {self.track_id} disappeared")

        # Extract key facts from the completed workstream's output
        completed_ws = self.track.get("workstreams", {}).get(completed_workstream, {})
        key_facts = self._extract_key_facts(completed_ws)

        # Store key facts for downstream workers
        if key_facts:
            update_key_facts(self.track_id, key_facts)

        # Validate the completed worker's output
        validation = self._validate_worker_output(completed_ws)
        if not validation["valid"]:
            log.error(
                f"Worker {completed_workstream} output invalid: {validation['errors']}"
            )
            update_workstream(
                self.track_id, completed_workstream, status="failed"
            )
            return {
                "track": self.track_id,
                "results": [{
                    "workstream": completed_workstream,
                    "status": "failed",
                    "errors": validation["errors"],
                }],
            }

        # Mark complete
        update_workstream(self.track_id, completed_workstream, status="done")

        # Continue with remaining workstreams
        return self.run()

    def _validate_worker_output(self, ws: dict) -> dict:
        """
        Validate a worker's output file exists and is valid JSON.
        
        Returns {"valid": True} or {"valid": False, "errors": [...]}.
        """
        errors = []
        
        # Compute expected output path (same logic as _build_inputs)
        role = ws.get("role", "engine")
        output_path = f"/tmp/riptide-{self.track_id}-{role}-output.json"
        
        # Check file exists
        if not Path(output_path).exists():
            errors.append(f"Output file not found: {output_path}")
            return {"valid": False, "errors": errors}
        
        # Check file is valid JSON
        try:
            with open(output_path) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            errors.append(f"Invalid JSON in output file: {e}")
            return {"valid": False, "errors": errors}
        
        # Check file is not empty
        if not data:
            errors.append("Output file is empty")
            return {"valid": False, "errors": errors}
        
        return {"valid": True}

    def _dispatch_workstream(self, ws_id: str, ws: dict) -> dict:
        """
        Dispatch a single workstream as a Hermes cron session.

        Instead of instantiating a Python class (Probe(), Judge(), etc.),
        this creates a Hermes cron session with stratified context.
        Includes retry logic for transient failures.
        """
        role = ws.get("role", "engine")
        pipeline = ws.get("pipeline", [])

        log.info(f"Dispatching {role} for track {self.track_id}, workstream {ws_id}")

        # Mark as in_progress
        update_workstream(self.track_id, ws_id, status="in_progress")
        # Set started_at timestamp for stuck-pipeline detection
        _set_started_at(self.track_id, ws_id)

        # Build inputs from workstream spec
        inputs = self._build_inputs(ws, role)

        # Get acceptance criteria
        acceptance = ws.get("acceptance", {})

        # Get key facts from upstream workers
        key_facts = self.track.get("key_facts", {})

        # Check retry count
        retry_count = ws.get("retry_count", 0)
        max_retries = 3

        # Dispatch the Hermes session
        success = spawn_worker_session(
            role=role,
            track_id=self.track_id,
            workstream_id=ws_id,
            inputs=inputs,
            acceptance=acceptance,
            key_facts=key_facts,
        )

        if success:
            return {
                "workstream": ws_id,
                "status": "dispatched",
                "role": role,
                "message": f"{role} session dispatched — will resume on completion",
            }
        elif retry_count < max_retries:
            # Retry: increment counter and re-dispatch
            log.warning(
                f"Dispatch failed for {ws_id} (attempt {retry_count + 1}/{max_retries}), retrying..."
            )
            update_workstream(
                self.track_id, ws_id, status="pending", retry_count=retry_count + 1
            )
            return {
                "workstream": ws_id,
                "status": "retrying",
                "role": role,
                "retry_count": retry_count + 1,
                "message": f"Retrying {role} (attempt {retry_count + 1}/{max_retries})",
            }
        else:
            update_workstream(self.track_id, ws_id, status="failed")
            return {
                "workstream": ws_id,
                "status": "failed",
                "role": role,
                "message": f"Failed to dispatch {role} session after {max_retries} attempts",
            }

    def retry_workstream(self, ws_id: str) -> dict:
        """
        Manually retry a failed or pending workstream.
        
        Resets the workstream status to pending and re-dispatches it.
        """
        ws = get_workstream(self.track_id, ws_id)
        if not ws:
            raise ValueError(f"Workstream {ws_id} not found")
        
        if ws.get("status") not in ("failed", "blocked", "pending"):
            raise ValueError(f"Cannot retry workstream with status '{ws.get('status')}'")
        
        # Reset status to pending and clear retry count
        update_workstream(self.track_id, ws_id, status="pending", retry_count=0)
        
        # Re-dispatch
        ws = get_workstream(self.track_id, ws_id)
        if not ws:
            raise ValueError(f"Workstream {ws_id} disappeared during retry")
        return self._dispatch_workstream(ws_id, ws)

    def _build_inputs(self, ws: dict, role: str) -> dict:
        """Build role-specific inputs from workstream spec."""
        base_inputs = dict(ws.get("inputs", {}))

        # Add role-specific output path
        output_path = f"/tmp/riptide-{self.track_id}-{role}-output.json"
        base_inputs["output_path"] = output_path
        base_inputs["track_id"] = self.track_id
        base_inputs["workstream_id"] = ws.get("id", "unknown")

        # Add resume URL for the completion webhook
        base_inputs["resume_url"] = (
            f"http://localhost:8477/conductor/resume"
            f"?track={self.track_id}&workstream={ws.get('id', 'unknown')}"
        )

        # Add role-specific enrichments
        if role == "judge":
            # Judge needs the probe output path
            probe_output = f"/tmp/riptide-{self.track_id}-probe-output.json"
            base_inputs["context_path"] = probe_output

        elif role == "artisan":
            # Artisan needs the judge findings path
            judge_output = f"/tmp/riptide-{self.track_id}-judge-output.json"
            base_inputs["findings_path"] = judge_output
            # Add findings summary for context
            findings_summary = self._load_findings_summary(judge_output)
            base_inputs["findings_summary"] = findings_summary

        elif role == "engine":
            # Engine needs the command from upstream
            base_inputs["command"] = base_inputs.get("command", "echo 'no command'")

        elif role == "warden":
            # Warden needs to verify the upstream output
            upstream_output = f"/tmp/riptide-{self.track_id}-{ws.get('upstream_role', 'engine')}-output.json"
            base_inputs["verify_path"] = upstream_output

        elif role == "scribe":
            # Scribe needs all upstream outputs
            base_inputs["context_path"] = f"/tmp/riptide-{self.track_id}-probe-output.json"
            base_inputs["findings_path"] = f"/tmp/riptide-{self.track_id}-judge-output.json"
            base_inputs["diagram_url"] = self._get_diagram_url()
            if self.track:
                repos = self.track.get("repos") or {}
            else:
                repos = {}
            first_repo = next(iter(repos.keys()), "riptide")
            base_inputs["owner"] = repos.get(first_repo, {}).get("owner", "ChonSong")
            base_inputs["repo"] = first_repo
            base_inputs["pr_number"] = repos.get(first_repo, {}).get("pr", 0)

        elif role == "ci_verifier":
            if self.track:
                repos = self.track.get("repos") or {}
            else:
                repos = {}
            first_repo = next(iter(repos.keys()), "riptide")
            base_inputs["owner"] = repos.get(first_repo, {}).get("owner", "ChonSong")
            base_inputs["repo"] = first_repo
            base_inputs["pr_number"] = repos.get(first_repo, {}).get("pr", 0)
            base_inputs["timeout"] = base_inputs.get("timeout", 600)

        return base_inputs

    def _extract_key_facts(self, completed_ws: dict) -> dict:
        """Extract key facts from a completed workstream's output file."""
        role = completed_ws.get("role", "unknown")
        track_id = self.track_id
        
        # Read output from temp file (worker writes here)
        output_path = f"/tmp/riptide-{track_id}-{role}-output.json"
        try:
            with open(output_path) as f:
                output = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        
        key_facts = {}

        if role == "probe":
            if isinstance(output, dict):
                key_facts["graphify"] = output.get("graphify", {})
                key_facts["already_reviewed"] = output.get("already_reviewed", False)
                key_facts["previous_findings"] = output.get("previous_findings", [])

        elif role == "judge":
            if isinstance(output, dict):
                findings = output.get("findings", [])
                key_facts["finding_count"] = len(findings)
                key_facts["severities"] = [f.get("severity") for f in findings]

        elif role == "artisan":
            if isinstance(output, dict):
                key_facts["diagram_url"] = output.get("diagram_url", "")

        elif role == "engine":
            if isinstance(output, dict):
                key_facts["command_success"] = output.get("success", False)
                key_facts["exit_code"] = output.get("exit_code", -1)

        elif role == "warden":
            if isinstance(output, dict):
                key_facts["verification_passed"] = output.get("pass", False)

        return key_facts

    def _load_findings_summary(self, findings_path: str) -> str:
        """Load a summary of findings for the artisan's prompt."""
        try:
            with open(findings_path) as f:
                data = json.load(f)
            findings = data.get("findings", [])
            summaries = [f"- [{f.get('severity', '?')}] {f.get('title', '?')}" for f in findings[:5]]
            return "\n".join(summaries) if summaries else "No findings"
        except (FileNotFoundError, json.JSONDecodeError):
            return "No findings available"

    def _get_diagram_url(self) -> str:
        """Get the diagram URL from the artisan's output (if available)."""
        try:
            artisan_output = f"/tmp/riptide-{self.track_id}-artisan-output.json"
            with open(artisan_output) as f:
                data = json.load(f)
            return data.get("diagram_url", "")
        except (FileNotFoundError, json.JSONDecodeError):
            return ""




def create_stratified_review_pipeline(
    owner: str,
    repo: str,
    pr_number: int,
    pr_details: dict,
    files: list[dict],
) -> dict:
    """
    Create a review pipeline with stratified Hermes sessions.
    
    Idempotent: if a track already exists for this PR, returns it as-is
    (does not create duplicate workstreams).
    """
    track_id = f"riptide-review-{owner}-{repo}-{pr_number}"

    track = get_track(track_id)
    if track:
        # Track already exists — return as-is (idempotent)
        return track

    track = create_track(
        track_id,
        name=f"Riptide Review #{pr_number}",
        phase="StratifiedReview",
        repos={repo: {"owner": owner, "pr": pr_number}},
    )

    head_sha = pr_details.get("head", {}).get("sha", "")

    # Stage 1: Probe — gather context
    create_workstream(
        track_id,
        "ws-1-probe",
        inputs={
            "pr_number": pr_number,
            "owner": owner,
            "repo": repo,
            "files": files,
            "head_sha": head_sha,
        },
        acceptance={"output_exists": True},
        role="probe",
        pipeline=["fetch_diff", "graphify", "context_bundle"],
    )

    # Stage 2: Judge — evaluate code quality
    create_workstream(
        track_id,
        "ws-2-judge",
        inputs={
            "context_path": f"/tmp/riptide-{track_id}-probe-output.json",
            "pr_number": pr_number,
            "owner": owner,
            "repo": repo,
        },
        acceptance={"findings_valid": True},
        role="judge",
        pipeline=["diff_analyzer", "dedup", "score"],
    )

    # Stage 3: Artisan — generate diagram
    create_workstream(
        track_id,
        "ws-3-artisan",
        inputs={
            "findings_path": f"/tmp/riptide-{track_id}-judge-output.json",
            "pr_number": pr_number,
            "owner": owner,
            "repo": repo,
        },
        acceptance={"diagram_created": True},
        role="artisan",
        pipeline=["excalidraw", "upload"],
    )

    # Stage 4: Warden — verify outputs
    create_workstream(
        track_id,
        "ws-4-warden",
        inputs={
            "verify_path": f"/tmp/riptide-{track_id}-artisan-output.json",
            "upstream_role": "artisan",
        },
        acceptance={"verification_passed": True},
        role="warden",
        pipeline=["verify_all"],
    )

    # Stage 5: Scribe — assemble and post review
    create_workstream(
        track_id,
        "ws-5-scribe",
        inputs={
            "pr_number": pr_number,
            "owner": owner,
            "repo": repo,
            "action": "post_review",
            "head_sha": head_sha,
        },
        acceptance={"posted": True},
        role="scribe",
        pipeline=["assemble_review", "post_comment"],
    )

    # Return the track (re-read to get the latest state)
    result = get_track(track_id)
    if result is None:
        raise RuntimeError(f"Failed to create track {track_id}")
    return result


def create_stratified_fix_pipeline(
    owner: str,
    repo: str,
    pr_number: int,
    pr_details: dict,
    files: list[dict],
    description: str = "",
    push_eligible: bool = True,
) -> dict:
    """
    Create a fix pipeline with stratified Hermes sessions.
    
    Idempotent: if a track already exists for this PR, returns it as-is
    (does not create duplicate workstreams).
    
    Pipeline stages:
        1. probe: Fetch diff, context bundle, review findings
        2. judge: Verify findings, classify valid
        3. artisan: Edit files, apply targeted fixes
        4. engine: Run tests, push if green
        5. ci_verifier: Poll CI, classify failures
        6. scribe: Format summary, post comment
    """
    track_id = f"riptide-fix-{owner}-{repo}-{pr_number}"

    track = get_track(track_id)
    if track:
        # Track already exists — return as-is (idempotent)
        return track

    track = create_track(
        track_id,
        name=f"Riptide Fix #{pr_number}",
        phase="StratifiedFix",
        repos={repo: {"owner": owner, "pr": pr_number}},
    )

    head_sha = pr_details.get("head", {}).get("sha", "")
    if not head_sha:
        raise ValueError(f"PR #{pr_number} has no head SHA — cannot create fix pipeline")

    create_workstream(
        track_id,
        "ws-1-probe",
        inputs={
            "pr_number": pr_number,
            "owner": owner,
            "repo": repo,
            "files": files,
            "head_sha": head_sha,
        },
        acceptance={"output_exists": True},
        role="probe",
        pipeline=["fetch_diff", "context_bundle", "review_findings"],
    )

    create_workstream(
        track_id,
        "ws-2-judge",
        inputs={
            "context_path": f"/tmp/riptide-{track_id}-probe-output.json",
            "description": description,
        },
        acceptance={"findings_valid": True},
        role="judge",
        pipeline=["verify_findings", "classify_valid"],
    )

    create_workstream(
        track_id,
        "ws-3-artisan",
        inputs={
            "findings_path": f"/tmp/riptide-{track_id}-judge-output.json",
            "files": files,
            "push_eligible": push_eligible,
        },
        acceptance={"edits_applied": True},
        role="artisan",
        pipeline=["edit_files", "targeted_fixes"],
    )

    create_workstream(
        track_id,
        "ws-4-engine",
        inputs={
            "command": "run_tests_and_push",
            "push_eligible": push_eligible,
            "head_ref": pr_details.get("head", {}).get("ref", ""),
        },
        acceptance={"tests_passed": True, "pushed": True},
        role="engine",
        pipeline=["run_tests", "push_if_green"],
    )

    create_workstream(
        track_id,
        "ws-5-ci-verifier",
        inputs={
            "pr_number": pr_number,
            "owner": owner,
            "repo": repo,
            "timeout": 600,
        },
        acceptance={"ci_complete": True},
        role="ci_verifier",
        pipeline=["poll_ci", "classify_failures"],
    )

    create_workstream(
        track_id,
        "ws-6-scribe",
        inputs={
            "pr_number": pr_number,
            "owner": owner,
            "repo": repo,
            "action": "post_fix_summary",
            "head_sha": head_sha,
        },
        acceptance={"posted": True},
        role="scribe",
        pipeline=["format_summary", "post_comment"],
    )

    # Return the track (re-read to get the latest state)
    result = get_track(track_id)
    if result is None:
        raise RuntimeError(f"Failed to create track {track_id}")
    return result
