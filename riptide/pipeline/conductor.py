#!/usr/bin/env python3
"""conductor.py — Orchestrator for the Riptide Pipeline.

Reads work-state.json, dispatches workers (deterministic + LLM-spawned),
monitors for stalls, verifies outputs, and updates state.

The Conductor orchestrates WHEN to spawn Hermes sessions and WHAT context
to give them. Deterministic workers (Probe, Artisan, Engine, Warden) run
in-process. LLM-dependent workers (Judge, Scribe) spawn Hermes sessions
via the provided `spawn_llm` callback.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .work_state import (
    read_state, write_state, now,
    get_track, get_workstream, create_track, create_workstream,
    update_track, update_workstream, next_pending_workstream, update_key_facts,
)
from .roles import WorkerBrief, ROLES
from .recovery import detect_stall, recover, assess_partial_output, StallSignal, FailureType

# Deterministic workers (run in-process)
from .probe import Probe
from .artisan import Artisan
from .engine import Engine
from .warden import Warden

# ── Per-stage skill assignments ──────────────────────────────────────────────
# Loaded via --skill for each Hermes session spawned by the pipeline.

JUDGE_SKILLS = ["deep-think", "brooks-lint", "github-pr-lifecycle"]
"""Judge stage: deep reasoning + design smell detection + inline comment posting."""

SCRIBE_SKILLS = ["github-pr-lifecycle", "excalidraw"]
"""Scribe stage: review summary generation + diagram link handling + posting."""


class Conductor:
    """Orchestrates workers to complete a track's workstreams.
    
    Args:
        track_id: Unique identifier for this review track.
        spawn_llm: Callback to spawn an LLM session. Signature:
                   spawn_llm(prompt: str, name: str, skills: list[str]) -> bool
                   Returns True if session was spawned successfully.
    """
    
    def __init__(self, track_id: str, spawn_llm: Optional[Callable] = None):
        self.track_id = track_id
        self.track = get_track(track_id)
        if not self.track:
            raise ValueError(f"Track {track_id} not found")
        self.spawn_llm = spawn_llm or self._default_spawn_llm
    
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
        else:
            raise ValueError(f"Unknown role: {role}")
    
    # ── Deterministic Workers (in-process) ──────────────────────────────────
    
    def _run_probe(self, brief: WorkerBrief) -> dict:
        """Run Probe worker — deterministic context gathering."""
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
    
    def _run_artisan(self, brief: WorkerBrief) -> dict:
        """Run Artisan worker — deterministic file creation."""
        artisan = Artisan()
        
        files = brief.inputs.get("files", [])
        created = []
        for file_spec in files:
            result = artisan.create_file(file_spec["path"], file_spec["content"])
            created.append(result)
        
        return {"created": created}
    
    def _run_engine(self, brief: WorkerBrief) -> dict:
        """Run Engine worker — deterministic shell execution."""
        engine = Engine()
        
        command = brief.inputs.get("command", "")
        result = engine.run(command, expected_exit=brief.inputs.get("expected_exit", 0))
        
        return result
    
    def _run_warden(self, brief: WorkerBrief) -> dict:
        """Run Warden worker — deterministic verification."""
        warden = Warden()
        
        checks = brief.inputs.get("checks", [])
        result = warden.verify_all(checks)
        
        output_path = brief.output_protocol.get("path", "/tmp/verification.json")
        with open(output_path, 'w') as f:
            json.dump(result, f, indent=2)
        
        return {"verification_path": output_path, "pass": result["pass"]}
    
    # ── LLM-Dependent Workers (spawn Hermes sessions) ───────────────────────
    
    def _run_judge(self, brief: WorkerBrief) -> dict:
        """Run Judge worker — LLM deep-think on probe output.
        
        Spawns a Hermes session to analyze the probe context and produce
        findings. Falls back to deterministic Judge if no LLM available.
        """
        context_path = brief.inputs.get("context_path", "")
        with open(context_path) as f:
            context = json.load(f)
        
        # Build LLM prompt with pre-gathered context
        prompt = self._build_judge_prompt(context, brief)
        
        # Spawn Hermes session for deep-think analysis
        output_path = brief.output_protocol.get("path", "/tmp/findings.json")
        spawned = self.spawn_llm(
            prompt=prompt,
            name=f"judge-{self.track_id}-{brief.workstream}",
            skills=JUDGE_SKILLS,
        )
        
        if spawned:
            # Wait for LLM session to write findings
            # (In practice, the LLM session writes directly to output_path)
            return {"findings_path": output_path, "spawned": True}
        else:
            # Fallback to deterministic Judge
            from .judge import Judge
            judge = Judge(context)
            result = judge.evaluate()
            with open(output_path, 'w') as f:
                json.dump(result, f, indent=2)
            return {"findings_path": output_path, "findings_count": len(result.get("findings", [])), "spawned": False}
    
    def _run_scribe(self, brief: WorkerBrief) -> dict:
        """Run Scribe worker — assembles and posts review.
        
        Spawns a Hermes session to generate the review summary, then
        posts the assembled review.
        """
        action = brief.inputs.get("action", "update_workstream")
        
        if action == "update_workstream":
            return self._update_workstream(brief)
        elif action == "post_review":
            return self._post_review(brief)
        
        return {"error": f"Unknown scribe action: {action}"}
    
    def _update_workstream(self, brief: WorkerBrief) -> dict:
        """Update workstream status (deterministic)."""
        from .work_state import update_workstream as _update
        return _update(
            self.track_id,
            brief.workstream,
            status=brief.inputs.get("status", "done"),
            outputs=brief.inputs.get("outputs"),
        )
    
    def _post_review(self, brief: WorkerBrief) -> dict:
        """Post review — LLM generates summary, Scribe assembles."""
        findings_path = brief.inputs.get("findings_path", "/tmp/findings.json")
        owner = brief.inputs.get("owner", "ChonSong")
        repo = brief.inputs.get("repo", "riptide")
        pr_number = brief.inputs.get("pr_number", 0)
        diagram_url = brief.inputs.get("diagram_url")
        
        # Build prompt for LLM to generate review summary
        prompt = self._build_scribe_prompt(findings_path, owner, repo, pr_number, diagram_url)
        
        # Spawn Hermes session for summary generation
        spawned = self.spawn_llm(
            prompt=prompt,
            name=f"scribe-{self.track_id}-{brief.workstream}",
            skills=SCRIBE_SKILLS,
        )
        
        if spawned:
            return {"posted": True, "spawned": True}
        else:
            # Fallback: use assembler directly
            from .scribe import Scribe
            scribe = Scribe()
            with open(findings_path) as f:
                findings = json.load(f)
            result = scribe.post_review_with_assembler(
                owner, repo, pr_number, findings, diagram_url
            )
            return result
    
    # ── LLM Prompt Builders ─────────────────────────────────────────────────
    
    def _build_judge_prompt(self, context: dict, brief: WorkerBrief) -> str:
        """Build prompt for LLM deep-think analysis."""
        files_str = "\n".join(
            f"  - {f.get('filename', '?')} (+{f.get('additions', 0)}/-{f.get('deletions', 0)})"
            for f in context.get("files_changed", [])[:20]
        )
        
        diff_summary = context.get("diff_raw", "")[:12000]
        
        findings = context.get("diff_report", {}).get("findings", [])
        findings_str = "\n".join(
            f"- [{f.get('severity', 'info')}] {f.get('category', '?')}: {f.get('message', '')}"
            for f in findings[:10]
        )
        
        return f"""PR #{brief.inputs.get('pr_number', '?')} in {brief.inputs.get('owner', '?')}/{brief.inputs.get('repo', '?')}

## Context (pre-gathered)
### Files Changed
{files_str}

### Diff Summary
```
{diff_summary}
```

### Deterministic Findings
{findings_str}

## Your Task: Deep Analysis
Call `skill_view('deep-think')` first, then analyze the diff.

1. Verify each deterministic finding against the actual diff
2. Find NEW issues the deterministic analysis missed (logic bugs, race conditions, edge cases)
3. Post 1-3 inline review comments via `gh api repos/O/R/pulls/N/comments`
4. Write findings to {brief.output_protocol.get("path", "/tmp/findings.json")} as JSON:
   [{{severity, title, detail, file, line}}]

Rules: Max 3 inline comments. Real issues only. No padding.
"""
    
    def _build_scribe_prompt(self, findings_path: str, owner: str, repo: str, pr_number: int, diagram_url: Optional[str]) -> str:
        """Build prompt for LLM review summary generation."""
        diagram_section = f"\n## Architecture Diagram\n[View Diagram]({diagram_url})\n" if diagram_url else ""
        
        return f"""## Review Summary Generation

Read findings from {findings_path}, then:

1. Write a concise review summary (max 300 words)
2. Include: overall verdict, key findings, recommended actions
3. Post as PR comment via `gh pr comment {pr_number} --repo {owner}/{repo} --body "..."`
4. Sign off with: `---\n<sub>🤖 Riptide Review via Hermes</sub>`
{diagram_section}
"""
    
    # ── Default LLM Spawn (fallback) ────────────────────────────────────────
    
    def _default_spawn_llm(self, prompt: str, name: str, skills: list[str]) -> bool:
        """Default LLM spawn — logs that no LLM is available."""
        import logging
        log = logging.getLogger("riptide.conductor")
        log.warning(f"No LLM spawn callback configured for {name}. Using deterministic fallback.")
        return False


# ── Pipeline builder ─────────────────────────────────────────────────────────

def create_pr_review_pipeline(
    track_id: str,
    pr_number: int,
    owner: str = "ChonSong",
    repo: str = "riptide",
) -> dict:
    """Create a PR review pipeline for a given PR.
    
    Returns the track with workstreams:
    1. probe → gather context (deterministic)
    2. judge → deep-think analysis (LLM-spawned)
    3. artisan → generate excalidraw diagram (deterministic)
    4. engine → upload diagram (deterministic)
    5. scribe → post review (LLM-spawned summary)
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
        pipeline=["deep_think", "dedup", "score"],
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
