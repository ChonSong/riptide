#!/usr/bin/env python3
"""scribe.py — Updates work-state.json and posts GitHub comments."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from .work_state import read_state, write_state, now


class Scribe:
    """Handles all external state mutations: work-state.json + GitHub comments.
    
    The only worker that mutates persistent state. All other workers
    produce artifacts; the Scribe records them.
    """
    
    def __init__(self, work_state_path: Optional[str] = None):
        self.state_path = work_state_path
    
    # ── Work state ─────────────────────────────────────────────────────────
    
    def record_review_start(self, track_id: str, pr_number: int) -> dict:
        """Record that a review has started."""
        state = read_state()
        track = state.get("tracks", {}).get(track_id, {})
        track.setdefault("last_review", {})["pr"] = pr_number
        track["last_review"]["started_at"] = now()
        write_state(state)
        return {"recorded": True}
    
    def record_review_complete(
        self,
        track_id: str,
        pr_number: int,
        findings: list[dict],
        diagram_url: Optional[str] = None,
    ) -> dict:
        """Record review completion in work-state.json."""
        state = read_state()
        track = state.get("tracks", {}).get(track_id, {})
        
        track.setdefault("last_review", {}).update({
            "pr": pr_number,
            "completed_at": now(),
            "findings_count": len(findings),
            "diagram_url": diagram_url,
        })
        
        # Store findings for dedup
        track.setdefault("reviewed_prs", {})[str(pr_number)] = {
            "findings": findings,
            "reviewed_at": now(),
        }
        
        write_state(state)
        return {"recorded": True, "findings_count": len(findings)}
    
    def update_workstream(
        self,
        track_id: str,
        ws_id: str,
        status: str,
        outputs: Optional[dict] = None,
    ) -> dict:
        """Update workstream status in state."""
        from .work_state import update_workstream as _update
        return _update(track_id, ws_id, status=status, outputs=outputs)
    
    # ── GitHub comments ────────────────────────────────────────────────────
    
    def post_pr_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
    ) -> dict:
        """Post a PR comment via gh CLI."""
        result = subprocess.run(
            ["gh", "pr", "comment", str(pr_number), "--repo", f"{owner}/{repo}", "--body", body],
            capture_output=True, text=True, timeout=30,
        )
        return {
            "posted": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    
    def post_inline_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        file: str,
        line: int,
        body: str,
    ) -> dict:
        """Post an inline comment on a specific file/line."""
        result = subprocess.run(
            [
                "gh", "api", f"/repos/{owner}/{repo}/pulls/{pr_number}/comments",
                "-f", f"body={body}",
                "-f", f"path={file}",
                "-f", f"line={line}",
                "-f", "side=RIGHT",
            ],
            capture_output=True, text=True, timeout=30,
        )
        return {
            "posted": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    
    def post_review_with_assembler(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        findings: list[dict],
        diagram_url: Optional[str] = None,
        model: str = "custom:LongCat-2.0",
    ) -> dict:
        """Post review using assemble_review.py."""
        # Write findings to temp file
        findings_path = "/tmp/findings.json"
        with open(findings_path, 'w') as f:
            json.dump(findings, f, indent=2)
        
        cmd = [
            "python", "-m", "riptide.assemble_review",
            "--findings", findings_path,
            "--owner", owner,
            "--repo", repo,
            "--pr", str(pr_number),
            "--model", model,
        ]
        
        if diagram_url:
            cmd.extend(["--diagram-url", diagram_url])
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        return {
            "posted": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
