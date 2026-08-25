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
        
        # 7. Cleanliness signals (new)
        cleanliness = self._gather_cleanliness_signals(pr_data, files)
        
        return {
            "pr_data": pr_data,
            "diff_report": diff_report,
            "bundle": bundle,
            "graphify": graphify,
            "already_reviewed": already_reviewed,
            "previous_findings": previous_findings,
            "key_facts": key_facts,
            "cleanliness": cleanliness,
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

    def _gather_cleanliness_signals(self, pr_data: dict, files: list[dict]) -> dict:
        """Gather cleanliness signals: merge conflicts, related PRs, test coverage, description quality.

        All signals are gathered via `gh` CLI for deterministic, structured output.
        """
        # Merge conflict status
        mergeable = self._get_mergeable_status()

        # Related open PRs touching same files
        related_prs = self._get_related_prs(files)

        # Test coverage: source files changed vs test files changed
        test_coverage = self._check_test_coverage(files)

        # PR description quality
        description_quality = self._check_description_quality(pr_data)

        # Commit hygiene
        commit_hygiene = self._check_commit_hygiene()

        # PR staleness
        staleness = self._check_staleness(pr_data)

        # CI pre-check
        ci_precheck = self._get_ci_precheck()

        return {
            "mergeable": mergeable,
            "related_prs": related_prs,
            "test_coverage": test_coverage,
            "description_quality": description_quality,
            "commit_hygiene": commit_hygiene,
            "staleness": staleness,
            "ci_precheck": ci_precheck,
        }

    def _get_mergeable_status(self) -> dict:
        """Check if PR has merge conflicts."""
        result = subprocess.run(
            ["gh", "pr", "view", str(self.pr), "--repo", f"{self.owner}/{self.repo}",
             "--json", "mergeable,mergeStateStatus"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"mergeable": "unknown", "status": "unknown"}
        data = json.loads(result.stdout)
        return {
            "mergeable": data.get("mergeable", "unknown"),
            "status": data.get("mergeStateStatus", "unknown"),
        }

    def _get_related_prs(self, files: list[dict]) -> list[dict]:
        """Find other open PRs touching the same files."""
        if not files:
            return []
        # Get filenames for matching
        filenames = [f.get("filename", "") for f in files if f.get("filename")]
        if not filenames:
            return []
        # List open PRs (excluding this one)
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--repo", f"{self.owner}/{self.repo}",
             "--json", "number,title,files,author"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        try:
            all_prs = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            return []
        related = []
        for pr in all_prs:
            pr_number = pr.get("number", 0)
            if pr_number == self.pr:
                continue
            pr_files = [f.get("filename", "") for f in pr.get("files", [])]
            overlap = set(filenames) & set(pr_files)
            if overlap:
                related.append({
                    "number": pr_number,
                    "title": pr.get("title", ""),
                    "author": pr.get("author", {}).get("login", ""),
                    "overlap_files": sorted(overlap),
                })
        return related

    def _check_test_coverage(self, files: list[dict]) -> dict:
        """Check if changed source files have corresponding test changes."""
        source_files = []
        test_files = []
        for f in files:
            fname = f.get("filename", "")
            if not fname:
                continue
            if fname.startswith("tests/") or fname.startswith("test_"):
                test_files.append(fname)
            elif fname.endswith(".py") and not fname.startswith((".github/", "scripts/", "docs/")):
                source_files.append(fname)
        # Source files without test changes
        untested = []
        for src in source_files:
            stem = src.replace("/", ".").removesuffix(".py")
            # Check if any test file matches the source
            has_test = any(
                stem.split(".")[-1] in tf or tf.replace("tests/", "").replace(".py", "") in stem
                for tf in test_files
            )
            if not has_test:
                untested.append(src)
        return {
            "source_files": source_files,
            "test_files": test_files,
            "untested_source": untested,
            "has_test_coverage": len(untested) == 0 and len(source_files) > 0,
        }

    def _check_description_quality(self, pr_data: dict) -> dict:
        """Check PR description quality: length, issue links, body presence."""
        body = pr_data.get("body", "") or ""
        body_stripped = body.strip()
        # Issue references (#123, GH-123, or full URLs)
        import re
        issue_refs = re.findall(r"(?:#|GH-|issues/)(\d+)", body_stripped)
        return {
            "has_body": len(body_stripped) > 20,
            "body_length": len(body_stripped),
            "issue_refs": issue_refs,
            "has_issue_link": len(issue_refs) > 0,
            "quality": "good" if len(body_stripped) > 50 and issue_refs else (
                "minimal" if len(body_stripped) > 20 else "missing"
            ),
        }

    def _check_commit_hygiene(self) -> dict:
        """Check commit messages against Conventional Commits."""
        import re
        result = subprocess.run(
            ["gh", "pr", "view", str(self.pr), "--repo", f"{self.owner}/{self.repo}",
             "--json", "commits"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"commits": [], "conventional_count": 0, "total": 0, "all_conventional": False}
        try:
            data = json.loads(result.stdout)
            # gh pr view --json commits returns a list directly
            if isinstance(data, list):
                commits = data
            else:
                commits = data.get("commits", [])
        except (json.JSONDecodeError, ValueError):
            return {"commits": [], "conventional_count": 0, "total": 0, "all_conventional": False}
        conventional_pattern = re.compile(
            r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
            r"(\([^)]+\))?(!)?: .+"
        )
        conventional_count = 0
        commit_summaries = []
        for commit in commits:
            msg = commit.get("message", "").split("\n")[0]  # First line only
            commit_summaries.append(msg)
            if conventional_pattern.match(msg):
                conventional_count += 1
        return {
            "commits": commit_summaries,
            "conventional_count": conventional_count,
            "total": len(commits),
            "all_conventional": conventional_count == len(commits) and len(commits) > 0,
        }

    def _check_staleness(self, pr_data: dict) -> dict:
        """Check PR staleness: age, last update, base branch divergence."""
        from datetime import datetime, timezone
        created = pr_data.get("createdAt", "")
        updated = pr_data.get("updatedAt", "")
        # Calculate age in days
        age_days = None
        if created:
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - created_dt).days
            except (ValueError, TypeError):
                pass
        return {
            "created_at": created,
            "updated_at": updated,
            "age_days": age_days,
            "is_stale": age_days is not None and age_days > 30,
        }

    def _get_ci_precheck(self) -> dict:
        """Check current CI status before reviewing."""
        result = subprocess.run(
            ["gh", "pr", "checks", str(self.pr), "--repo", f"{self.owner}/{self.repo}",
             "--json", "name,state"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"checks": [], "failing": [], "status": "unknown"}
        try:
            checks = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            return {"checks": [], "failing": [], "status": "unknown"}
        failing = [c for c in checks if c.get("state") == "failure"]
        return {
            "checks": checks,
            "failing": failing,
            "status": "failing" if failing else ("passing" if checks else "none"),
        }
