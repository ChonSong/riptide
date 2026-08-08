#!/usr/bin/env python3
"""probe.py — Deterministic context gathering via Riptide tools."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional


class Probe:
    """Gathers all deterministic signals for a PR into structured context.
    
    Wraps Riptide's existing tools:
    - diff_analyzer.py (security, complexity, error-handling)
    - context_bundle.py (concepts, blast radius, taxonomy)
    - graphify (code relationships)
    - StateStore (previous findings, SHA dedup)
    """
    
    def __init__(self, pr_number: int, owner: str = "ChonSong", repo: str = "riptide"):
        self.pr = pr_number
        self.owner = owner
        self.repo = repo
    
    def gather(self) -> dict:
        """Gather all deterministic context for a PR."""
        # 1. Get PR data + files from GitHub API
        pr_data = self._get_pr_data()
        files = self._get_pr_files()
        
        # 2. Run diff_analyzer
        diff_report = self._run_diff_analyzer(files)
        
        # 3. Run context_bundle
        bundle = self._run_context_bundle(files, pr_data)
        
        # 4. Run graphify
        graphify = self._run_graphify(files)
        
        # 5. Check StateStore for previous findings
        previous_findings = self._get_previous_findings()
        already_reviewed = len(previous_findings) > 0
        
        # 6. Extract key facts
        key_facts = self._extract_key_facts(diff_report, bundle)
        
        return {
            "pr_data": pr_data,
            "diff_report": diff_report,
            "bundle": bundle,
            "graphify": graphify,
            "already_reviewed": already_reviewed,
            "previous_findings": previous_findings,
            "key_facts": key_facts,
        }
    
    def _get_pr_data(self) -> dict:
        """Get PR metadata from GitHub API."""
        result = subprocess.run(
            ["gh", "pr", "view", str(self.pr), "--repo", f"{self.owner}/{self.repo}",
             "--json", "number,title,body,author,headRefName,baseRefName,createdAt"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {}
        return json.loads(result.stdout)
    
    def _get_pr_files(self) -> list[dict]:
        """Get PR changed files with stats from GitHub API."""
        result = subprocess.run(
            ["gh", "api", f"/repos/{self.owner}/{self.repo}/pulls/{self.pr}/files",
             "--paginate"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        
        files = json.loads(result.stdout)
        return [{
            "filename": f.get("filename", ""),
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
            "status": f.get("status", "modified"),
            "patch": f.get("patch", ""),
        } for f in files]
    
    def _run_diff_analyzer(self, files: list[dict]) -> dict:
        """Run diff_analyzer.py on changed files."""
        # Import and use the existing analyzer
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from riptide.diff_analyzer import DiffAnalyzer
        
        analyzer = DiffAnalyzer()
        report = analyzer.analyze(files)
        
        return {
            "findings": [
                {
                    "category": f.category,
                    "severity": f.severity,
                    "message": f.message,
                    "file": f.file,
                    "line_hint": f.line_hint,
                }
                for f in report.findings
            ],
            "stats": report.stats,
            "verdict": report.verdict,
            "summary": report.summary,
        }
    
    def _run_context_bundle(self, files: list[dict], pr_data: dict) -> dict:
        """Run context_bundle.py on changed files."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from riptide.context_bundle import build_context_bundle
        
        bundle = build_context_bundle(files, graph_context=None, pr_details=pr_data)
        return bundle
    
    def _run_graphify(self, files: list[dict]) -> dict:
        """Run graphify query for blast radius."""
        filenames = " ".join(f'"{f["filename"]}"' for f in files[:5])
        result = subprocess.run(
            ["graphify", "query", f"what touches {filenames}"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return {"raw": result.stdout, "error": result.stderr}
        return {"raw": result.stdout}
    
    def _get_previous_findings(self) -> list[dict]:
        """Get previous findings from StateStore."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from riptide.state import StateStore
        
        store = StateStore()
        pr_key = f"{self.owner}/{self.repo}#{self.pr}"
        heuristics = store.get_pr_heuristics(pr_key)
        # Return previous review info as findings
        if heuristics.get("reviewed_at"):
            return [{"reviewed_at": heuristics["reviewed_at"], "sha": heuristics.get("last_sha")}]
        return []
    
    def _extract_key_facts(self, diff_report: dict, bundle: dict) -> dict:
        """Extract key facts for work-state.json."""
        agg = bundle.get("aggregate", {})
        return {
            "verdict": diff_report.get("verdict", "pass"),
            "concepts": agg.get("concepts", []),
            "touches_core": agg.get("touches_core", False),
            "security_findings": len([f for f in diff_report.get("findings", []) if f.get("severity") == "critical"]),
            "complexity_findings": len([f for f in diff_report.get("findings", []) if f.get("severity") == "warning"]),
            "total_loc": agg.get("total_loc", 0),
        }
