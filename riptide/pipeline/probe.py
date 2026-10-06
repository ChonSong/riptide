#!/usr/bin/env python3
"""probe.py — Deterministic context gathering via Riptide tools."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional

# Regex for severity rows in Riptide severity tables: starts with |, then emoji
ROW_RE = r"^[ \t]*\|[ \t]*(🔴|🟡)"


class Probe:
    """Gathers all deterministic context for a PR.

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

        # 7. Gather review findings from all sources
        review_findings = self._get_review_findings()

        return {
            "pr_data": pr_data,
            "diff_report": diff_report,
            "bundle": bundle,
            "graphify": graphify,
            "already_reviewed": already_reviewed,
            "previous_findings": previous_findings,
            "key_facts": key_facts,
            "review_findings": review_findings,
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
        return [
            {
                "filename": f.get("filename", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "status": f.get("status", "modified"),
                "patch": f.get("patch", ""),
            }
            for f in files
        ]

    def _run_diff_analyzer(self, files: list[dict]) -> dict:
        """Run diff_analyzer.py on changed files."""
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

    # ============================================================
    # _get_review_findings — Main method: fetch from 3 GH API endpoints
    # ============================================================

    def _get_review_findings(self) -> list[dict]:
        """Fetch review findings from all three GitHub API endpoints.

        Returns:
            list[dict]: Deduplicated, capped, sorted list of findings with
                        {source, severity, file, line, body}.
        """
        findings: list[dict] = []

        # --- Source 1: Pull request reviews (human reviewers + CodeRabbit) ---
        reviews = self._run_gh(
            ["gh", "api", f"/repos/{self.owner}/{self.repo}/pulls/{self.pr}/reviews", "--paginate"],
        )

        for review in reviews:
            body = review.get("body", "") or ""
            user = review.get("user", {}).get("login", "") or ""

            # Skip empty bodies
            if not body.strip():
                continue

            # Skip CodeRabbit SUMMARY comments
            if "coderabbitai" in user.lower() and "This is an auto-generated comment: summarize" in body:
                continue

            # Skip '🤖 Hermes review' ack comments
            if "🤖 Hermes review" in body:
                continue

            # CodeRabbit review submissions (walk-through bodies) carry no
            # per-finding path/line of their own — their actionable findings
            # arrive as inline PR comments. Treat them like human reviews:
            # verbatim, 🟡.
            if "coderabbitai" in user.lower():
                findings.append({
                    "source": "coderabbitai",
                    "severity": "🟡",
                    "file": None,
                    "line": None,
                    "body": self._parse_coderabbit_comment(body)["body"]
                    if self._parse_coderabbit_comment(body) else body[:300],
                })
                continue

            # Human review submissions (non-bot users with non-empty body)
            # Severity 🟡 (unknown), file None, line None, body verbatim
            findings.append({
                "source": "human",
                "severity": "🟡",
                "file": None,
                "line": None,
                "body": body,
            })

        # --- Source 2: Issue comments (Riptide bot reviews) ---
        comments_issues = self._run_gh(
            ["gh", "api", f"/repos/{self.owner}/{self.repo}/issues/{self.pr}/comments", "--paginate"],
        )

        for comment in comments_issues:
            body = comment.get("body", "") or ""
            user = comment.get("user", {}).get("login", "") or ""

            # Skip empty bodies
            if not body.strip():
                continue

            # Skip '🤖 Hermes review' ack comments
            if "🤖 Hermes review" in body:
                continue

            # Riptide reviews (issue comments whose first line starts with '## Review:')
            first_line = body.split("\n")[0].strip()
            if first_line.startswith("## Review:"):
                finding = self._parse_riptide_review(body)
                if finding is not None:
                    for f in finding:
                        f["source"] = "riptide-review"
                        findings.append(f)
                continue

            # Other issue comments — skip (not Riptide reviews, not CodeRabbit)
            continue

        # --- Source 3: Pull request inline comments (CodeRabbit + reply chains) ---
        comments_pr = self._run_gh(
            ["gh", "api", f"/repos/{self.owner}/{self.repo}/pulls/{self.pr}/comments", "--paginate"],
        )

        # Group comments by in_reply_to_id for reply chain collapsing
        root_comments = []
        reply_groups: dict[str, list[dict]] = {}

        for comment in comments_pr:
            body = comment.get("body", "") or ""
            user = comment.get("user", {}).get("login", "") or ""

            # Skip empty bodies
            if not body.strip():
                continue

            # Skip '🤖 Hermes review' ack comments
            if "🤖 Hermes review" in body:
                continue

            # CodeRabbit inline comments (by coderabbitai[bot])
            if "coderabbitai" in user.lower():
                # Check if this is a SUMMARY comment
                if "This is an auto-generated comment: summarize" in body:
                    continue
                finding = self._parse_coderabbit_comment(body)
                if finding is not None:
                    finding["file"] = comment.get("path") or None
                    finding["line"] = comment.get("line") or None
                    # Check if this comment is a reply (has in_reply_to_id)
                    in_reply_to = comment.get("in_reply_to_id")
                    if in_reply_to:
                        # This is a reply — track it, root will carry it
                        rid = str(in_reply_to)
                        if rid not in reply_groups:
                            reply_groups[rid] = []
                        reply_groups[rid].append(finding)
                    else:
                        # Root comment
                        finding["source"] = "coderabbitai"
                        root_comments.append(finding)
                continue

            # Other PR comments — skip
            continue

        # Collapse reply chains: keep root comments only
        # All replies are merged into the root via dedup later
        for f in root_comments:
            findings.append(f)

        # Also add the reply-group roots (they carry the finding content)
        for rid, group in reply_groups.items():
            if group:
                findings.append(group[0])

        # ---- Dedupe ----
        seen: dict[tuple[str | None, int | None], dict] = {}
        final_findings: list[dict] = []

        for finding in findings:
            key_file = finding.get("file")
            key_line = finding.get("line")
            key = (key_file, key_line)

            if key in seen:
                # Merge sources
                existing = seen[key]
                existing["source"] = (
                    existing.get("source", "") + ", " + finding.get("source", "")
                )
                # Keep first body
            else:
                seen[key] = finding
                # Ensure source is set
                if "source" not in finding:
                    finding["source"] = "unknown"
                final_findings.append(finding)

        # Sort: 🔴 first then 🟡
        severity_order = {"🔴": 0, "🟡": 1}
        final_findings.sort(key=lambda f: severity_order.get(f.get("severity", "🟡"), 1))

        # Cap at 15 findings. The truncation note goes on the last KEPT
        # finding — annotating a row that is then trimmed away would report
        # a truncation nobody can see.
        if len(final_findings) > 15:
            truncated_count = len(final_findings) - 15
            final_findings = final_findings[:15]
            last = final_findings[-1]
            last_body = last.get("body", "")
            last["body"] = f"{last_body} …(truncated, {truncated_count} more)" if last_body else f"…(truncated, {truncated_count} more)"

        return final_findings

    # ============================================================
    # Parsing helpers
    # ============================================================

    def _parse_riptide_review(self, body: str) -> list[dict] | None:
        """Parse a Riptide severity table from a review body.

        Returns list of finding dicts or None if no valid rows found.
        """
        # Strip fenced code blocks (``` ... ```)
        cleaned = re.sub(r"```.*?```", "", body, flags=re.DOTALL)

        # Find all severity rows: lines starting with | containing 🔴 or 🟡
        row_pattern = re.compile(ROW_RE, re.MULTILINE)
        table_rows = []
        for line in cleaned.split('\n'):
            if line.startswith('|') and re.search(ROW_RE, line):
                table_rows.append(line)

        if not table_rows:
            return None

        findings = []
        for row_line in table_rows:
            cells = [c.strip() for c in row_line.split('|')]

            # Row shape from analysis: ['', emoji, title, code_span, description, '']
            # cells[1] = emoji (severity), cells[2] = title (finding body),
            # cells[3] = code(`path.py:123`) (file path with optional line),
            # cells[-2] = description/last content cell
            
            if len(cells) < 4:
                # Need at least: | emoji | title | code_span | ...
                continue

            # Severity: cells[1] (the emoji after the first pipe)
            severity_match = re.match(ROW_RE, row_line)
            severity = severity_match.group(1) if severity_match else "🟡"

            # File: last code-span in cells[3] (second-to-last content cell before last)
            # cells[3] = code(`path.py:123`) or code(`path.py`)
            code_span_cell = cells[3]
            code_span_match = re.findall(r"`([^`]+)`", code_span_cell)
            if not code_span_match:
                continue

            file_path = code_span_match[-1]  # last code-span
            # Strip :line suffix
            file_path = re.sub(r":\d+$", "", file_path)

            # Body: cells[2] (the title/text between emoji and code span)
            body_text = cells[2].strip()

            findings.append({
                "severity": severity,
                "file": file_path if file_path else None,
                "line": None,  # line info not extracted separately per test expectations
                "body": body_text,
            })

        return findings if findings else None

    def _parse_coderabbit_comment(self, body: str) -> dict | None:
        """Parse a CodeRabbit inline comment into a finding.

        Returns a finding dict or None if no actionable content found.
        """
        # Strip HTML comments <!-- ... -->
        cleaned = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
        # Strip markdown headers (# ...)
        cleaned = re.sub(r"^#+\\s+", "", cleaned, flags=re.MULTILINE)

        # Extract severity from body
        severity_emoji = self._map_severity(cleaned)

        # body = first 300 chars after stripping
        body_text = cleaned[:300].strip()

        if not body_text:
            return None

        return {
            "severity": severity_emoji,
            "file": None,  # will be set from comment.path in the main loop
            "line": None,  # will be set from comment.line in the main loop
            "body": body_text,
        }

    @staticmethod
    def _map_severity(text: str) -> str:
        """Map CodeRabbit severity markers to emoji."""
        # Check for emoji markers first
        if "🟡" in text:
            return "🟡"
        if "⚠️" in text:
            return "🔴"
        # Check for text labels
        text_upper = text.upper()
        if "MAJOR" in text_upper:
            return "🔴"
        if "MINOR" in text_upper:
            return "🟡"
        return "🟡"  # default

    def _run_gh(self, cmd: list[str]) -> list[dict]:
        """Run a `gh api` command and parse JSON output.

        Returns list of dicts, or empty list on failure.
        """
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        try:
            return json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            return []
