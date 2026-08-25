#!/usr/bin/env python3
"""conductor.py — Orchestrator for the Riptide Pipeline.

Reads work-state.json, dispatches workers, monitors for stalls,
verifies outputs, and updates state.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .work_state import (
    read_state, write_state, now,
    get_track, get_workstream, create_track, create_workstream,
    update_track, update_workstream, next_pending_workstream, update_key_facts,
)
from .roles import WorkerBrief, ROLES
from .recovery import detect_stall, recover, assess_partial_output, StallSignal, FailureType

# Will be imported by dispatch
from .probe import Probe
from .judge import Judge
from .artisan import Artisan
from .engine import Engine
from .warden import Warden
from .scribe import Scribe
from .ci_verifier import CIVerifier


class Conductor:
    """Orchestrates workers to complete a track's workstreams."""
    
    def __init__(self, track_id: str):
        self.track_id = track_id
        self.track = get_track(track_id)
        if not self.track:
            raise ValueError(f"Track {track_id} not found")
    
    def run(self) -> dict:
        """Run until all workstreams are done or blocked."""
        results = []
        
        while True:
            next_item = next_pending_workstream(self.track_id)
            if not next_item:
                break
            
            ws_id, ws = next_item
            result = self._run_workstream(ws_id, ws)
            results.append(result)
            
            if result.get("status") == "blocked":
                break
        
        return {"track": self.track_id, "results": results}
    
    def _get_track(self) -> dict:
        """Get current track state."""
        track = get_track(self.track_id)
        if not track:
            raise ValueError(f"Track {self.track_id} disappeared during run")
        return track
    
    def _run_workstream(self, ws_id: str, ws: dict) -> dict:
        """Run a single workstream."""
        # Mark in_progress
        update_workstream(self.track_id, ws_id, status="in_progress")

        # Refresh track state to avoid stale key_facts across workstreams
        self.track = self._get_track()

        # Determine which worker to dispatch
        pipeline = ws.get("pipeline", [])
        role = ws.get("role", "engine")
        
        # Build brief
        brief = WorkerBrief(
            role=role,
            name=f"{role}-{ws_id}",
            track=self.track_id,
            workstream=ws_id,
            pipeline=" → ".join(pipeline),
            position=f"workstream {ws_id}",
            key_facts=self.track.get("key_facts", {}),
            inputs=ws.get("inputs", {}),
            acceptance=ws.get("acceptance", {}),
            recovery=ws.get("recovery", {}),
            output_protocol={"path": ws.get("inputs", {}).get("output_path", "/tmp/output.json")},
        )
        
        # Dispatch worker
        output = self._dispatch(role, brief)
        
        # Verify output
        warden = Warden()
        verification = warden.verify_all([
            {"method": "check_file_exists", "args": {"path": brief.output_protocol["path"]}},
        ])
        
        if verification["pass"]:
            update_workstream(self.track_id, ws_id, status="done", outputs=output)
            return {"workstream": ws_id, "status": "done", "output": output}
        else:
            update_workstream(self.track_id, ws_id, status="failed", outputs=output)
            return {"workstream": ws_id, "status": "failed", "output": output}
    
    def _dispatch(self, role: str, brief: WorkerBrief) -> dict:
        """Dispatch a worker based on role."""
        if role == "probe":
            return self._run_probe(brief)
        elif role == "judge":
            return self._run_judge(brief)
        elif role == "artisan":
            return self._run_artisan(brief)
        elif role == "engine":
            return self._run_engine(brief)
        elif role == "warden":
            return self._run_warden(brief)
        elif role == "scribe":
            return self._run_scribe(brief)
        elif role == "ci_verifier":
            return self._run_ci_verifier(brief)
        else:
            raise ValueError(f"Unknown role: {role}")
    
    def _run_probe(self, brief: WorkerBrief) -> dict:
        """Run Probe worker."""
        pr = brief.inputs.get("pr_number", 1)
        owner = brief.inputs.get("owner", "ChonSong")
        repo = brief.inputs.get("repo", "riptide")
        
        probe = Probe(pr, owner, repo)
        context = probe.gather()
        
        output_path = brief.output_protocol.get("path", f"/tmp/pr-{pr}-context.json")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(context, f, indent=2, default=str)
        
        return {"context_path": output_path, "gathered": True}
    
    def _run_judge(self, brief: WorkerBrief) -> dict:
        """Run Judge worker."""
        context_path = brief.inputs.get("context_path", "")
        with open(context_path) as f:
            context = json.load(f)
        
        judge = Judge(context)
        result = judge.evaluate()
        
        output_path = brief.output_protocol.get("path", "/tmp/findings.json")
        with open(output_path, 'w') as f:
            json.dump(result, f, indent=2)
        
        return {"findings_path": output_path, "findings_count": len(result.get("findings", []))}
    
    def _run_artisan(self, brief: WorkerBrief) -> dict:
        """Run Artisan worker."""
        artisan = Artisan()
        
        # Create files from inputs
        files = brief.inputs.get("files", [])
        created = []
        for file_spec in files:
            result = artisan.create_file(file_spec["path"], file_spec["content"])
            created.append(result)
        
        return {"created": created}
    
    def _run_engine(self, brief: WorkerBrief) -> dict:
        """Run Engine worker."""
        engine = Engine()
        
        command = brief.inputs.get("command", "")
        result = engine.run(command, expected_exit=brief.inputs.get("expected_exit", 0))
        
        return result
    
    def _run_warden(self, brief: WorkerBrief) -> dict:
        """Run Warden worker."""
        warden = Warden()
        
        checks = brief.inputs.get("checks", [])
        result = warden.verify_all(checks)
        
        output_path = brief.output_protocol.get("path", "/tmp/verification.json")
        with open(output_path, 'w') as f:
            json.dump(result, f, indent=2)
        
        return {"verification_path": output_path, "pass": result["pass"]}
    
    def _run_scribe(self, brief: WorkerBrief) -> dict:
        """Run Scribe worker."""
        scribe = Scribe()
        
        action = brief.inputs.get("action", "update_workstream")
        
        if action == "update_workstream":
            return scribe.update_workstream(
                self.track_id,
                brief.workstream,
                brief.inputs.get("status", "done"),
                brief.inputs.get("outputs"),
            )
        elif action == "post_review":
            return scribe.post_review_with_assembler(
                brief.inputs.get("owner", "ChonSong"),
                brief.inputs.get("repo", "riptide"),
                brief.inputs.get("pr_number", 0),
                brief.inputs.get("findings", []),
                brief.inputs.get("diagram_url"),
            )
        elif action == "record_review":
            return scribe.record_review_complete(
                self.track_id,
                brief.inputs.get("pr_number", 0),
                brief.inputs.get("findings", []),
                brief.inputs.get("diagram_url"),
            )
        
        return {"error": f"Unknown scribe action: {action}"}

    def _run_ci_verifier(self, brief: WorkerBrief) -> dict:
        """Run CI Verifier worker — poll GitHub CI checks and classify."""
        owner = brief.inputs.get("owner", "ChonSong")
        repo = brief.inputs.get("repo", "riptide")
        pr_number = brief.inputs.get("pr_number", 0)
        timeout = brief.inputs.get("timeout", 600)

        verifier = CIVerifier(owner, repo, pr_number)
        result = verifier.poll(timeout=timeout)

        output_path = brief.output_protocol.get("path", "/tmp/ci_result.json")
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2, default=str)

        return {
            "ci_result_path": output_path,
            "status": result.get("status", "unknown"),
            "passed": result.get("status") == "success",
            "failed_count": len(result.get("failed", [])),
            "fixable_count": len(result.get("fixable", [])),
            "non_fixable_count": len(result.get("non_fixable", [])),
        }


# ── Pipeline builder ─────────────────────────────────────────────────────────

def create_pr_review_pipeline(
    track_id: str,
    pr_number: int,
    owner: str = "ChonSong",
    repo: str = "riptide",
) -> dict:
    """Create a PR review pipeline for a given PR.
    
    Returns the track with workstreams:
    1. probe → gather context
    2. judge → evaluate diff, produce findings
    3. artisan → generate excalidraw diagram
    4. engine → upload diagram
    5. scribe → post review + update state
    """
    track = get_track(track_id)
    if not track:
        track = create_track(
            track_id,
            name=f"PR Review #{pr_number}",
            phase="Review",
            repos={repo: {"owner": owner, "pr": pr_number}},
        )
    
    create_workstream(
        track_id,
        "ws-1-probe",
        inputs={"pr_number": pr_number, "owner": owner, "repo": repo},
        acceptance={"output_exists": True},
        role="probe",
        pipeline=["fetch_diff", "graphify", "context_bundle"],
    )

    create_workstream(
        track_id,
        "ws-2-judge",
        inputs={"context_path": f"/tmp/pr-{pr_number}-context.json"},
        acceptance={"findings_valid": True},
        role="judge",
        pipeline=["diff_analyzer", "dedup", "score"],
    )

    create_workstream(
        track_id,
        "ws-3-artisan",
        inputs={"findings_path": "/tmp/findings.json"},
        acceptance={"diagram_created": True},
        role="artisan",
        pipeline=["excalidraw", "upload"],
    )

    create_workstream(
        track_id,
        "ws-4-engine",
        inputs={"command": "upload_excalidraw /tmp/review.excalidraw"},
        acceptance={"uploaded": True},
        role="engine",
        pipeline=["upload"],
    )

    create_workstream(
        track_id,
        "ws-5-scribe",
        inputs={
            "pr_number": pr_number,
            "owner": owner,
            "repo": repo,
            "action": "post_review",
        },
        acceptance={"posted": True},
        role="scribe",
        pipeline=["assemble_review", "post_comment"],
    )

    return track


def create_fix_pipeline(
    owner: str,
    repo: str,
    pr_number: int,
    pr_details: dict,
    files: list[dict],
    description: str = "",
    push_eligible: bool = True,
) -> dict:
    """Create a fix pipeline with CI verification.

    Called from ``riptide.fixer._spawn_fix()`` when a fix session is spawned.

    Pipeline:
    1. probe → gather context at PR HEAD
    2. judge → verify findings against current code
    3. artisan → apply targeted edits
    4. engine → run local tests + push
    5. ci_verifier → poll GitHub CI, classify failures
    6. scribe → post summary comment with CI results

    Returns the created track dict with all workstreams staged.
    """
    track_id = f"riptide-fix-{owner}-{repo}-{pr_number}"

    track = get_track(track_id)
    if not track:
        track = create_track(
            track_id,
            name=f"Riptide Fix #{pr_number}",
            phase="Fix",
            repos={repo: {"owner": owner, "pr": pr_number}},
        )

    head_sha = pr_details.get("head", {}).get("sha", "")

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
            "context_path": f"/tmp/pr-{pr_number}-context.json",
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
            "findings_path": "/tmp/findings.json",
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

    return track
