#!/usr/bin/env python3
"""scribe.py — Updates work-state.json and posts GitHub comments."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
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
        model: Optional[str] = None,
        findings_path: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> dict:
        """Post review using assemble_review.py.

        The findings file is per-run: a single shared /tmp/findings.json let one
        concurrent review's write be read and posted by another session's
        assembler. The model attribution is threaded through so reviews are not
        all signed with one hardcoded model name.
        """
        # Write findings to a per-run file (never the shared /tmp/findings.json)
        if not findings_path:
            findings_path = (
                f"/tmp/riptide-review-findings-{owner}-{repo}-{pr_number}-"
                f"{uuid.uuid4().hex[:8]}.json"
            )
        Path(findings_path).parent.mkdir(parents=True, exist_ok=True)
        with open(findings_path, 'w') as f:
            json.dump(findings, f, indent=2)

        cmd = [
            "python", "-m", "riptide.assemble_review",
            "--findings", findings_path,
            "--owner", owner,
            "--repo", repo,
            "--pr", str(pr_number),
            "--model", model or os.environ.get("RIPTIDE_DEEPTHINK_MODEL", "custom:LongCat-2.0"),
        ]

        provider_value = provider or os.environ.get("RIPTIDE_DEEPTHINK_PROVIDER")
        if provider_value:
            cmd.extend(["--provider", provider_value])
        
        if diagram_url:
            cmd.extend(["--diagram-url", diagram_url])
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        return {
            "posted": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
