#!/usr/bin/env python3
"""judge.py — Evaluates diffs, dedups findings, produces structured output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class Judge:
    """Takes Probe's output and produces structured findings.
    
    Dedups against previous findings to avoid repeating comments.
    Focuses on: dead code, redundant imports, edge cases.
    """
    
    def __init__(self, probe_output: dict, focus_areas: Optional[list] = None):
        self.probe = probe_output
        self.focus_areas = focus_areas or ["dead_code", "redundant_imports", "edge_cases"]
    
    def evaluate(self) -> dict:
        """Produce max 3 NEW findings, deduped against previous."""
        if self.probe.get("already_reviewed"):
            return {"findings": [], "already_reviewed": True}
        
        findings = []
        previous = self.probe.get("previous_findings", [])
        previous_lines = {f.get("line") for f in previous}
        
        # Check diff report findings
        for finding in self.probe.get("diff_report", {}).get("findings", []):
            if finding.get("line_hint") in previous_lines:
                continue  # Skip already-reported
            if self._is_in_focus(finding):
                findings.append({
                    "file": finding.get("file", ""),
                    "line": finding.get("line_hint", ""),
                    "severity": finding.get("severity", "info"),
                    "title": finding.get("message", ""),
                    "detail": finding.get("message", ""),
                })
        
        # Check for redundant imports (deterministic)
        redundant = self._check_redundant_imports()
        findings.extend(redundant)
        
        # Check for dead code (deterministic)
        dead_code = self._check_dead_code()
        findings.extend(dead_code)
        
        return {"findings": findings[:3]}  # Max 3
    
    def _is_in_focus(self, finding: dict) -> bool:
        """Check if finding matches focus areas."""
        msg = finding.get("message", "").lower()
        if "redundant" in msg or "import" in msg:
            return "redundant_imports" in self.focus_areas
        if "dead" in msg or "unused" in msg or "unreachable" in msg:
            return "dead_code" in self.focus_areas
        if "bare except" in msg or "silently ignored" in msg:
            return "edge_cases" in self.focus_areas
        return False
    
    def _check_redundant_imports(self) -> list[dict]:
        """Check for redundant imports in the diff."""
        findings = []
        bundle = self.probe.get("bundle", {})
        
        for concept in bundle.get("concepts", []):
            if concept.get("concept") == "core" and concept.get("status") == "added":
                # Check for duplicate imports in added core files
                pass  # Would need to parse actual imports
        
        return findings
    
    def _check_dead_code(self) -> list[dict]:
        """Check for dead code patterns."""
        findings = []
        diff_report = self.probe.get("diff_report", {})
        
        for finding in diff_report.get("findings", []):
            if finding.get("category") == "structure":
                msg = finding.get("message", "").lower()
                if "deletion" in msg or "unused" in msg:
                    findings.append({
                        "file": finding.get("file", ""),
                        "line": finding.get("line_hint", ""),
                        "severity": "suggestion",
                        "title": finding.get("message", ""),
                        "detail": finding.get("message", ""),
                    })
        
        return findings
