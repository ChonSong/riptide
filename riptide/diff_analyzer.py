"""
diff_analyzer.py — Deterministic diff analysis for Riptide Companion.

Replaces LLM echo-TL;DR with pattern-based detection:
- Security risks (hardcoded secrets, injection, unsafe eval)
- Complexity (nesting depth, function length)
- Error handling (bare except, swallowed exceptions, missing try)
- Structural (new deps, large changes, deleted code)

Pure Python, no LLM. Deterministic output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class Finding:
    """A single deterministic finding about a diff."""
    category: str       # "security", "complexity", "error_handling", "structure"
    severity: str       # "info", "warning", "critical"
    message: str        # Human-readable description
    file: str = ""      # Optional file reference
    line_hint: str = ""  # Optional code snippet hint


@dataclass
class DiffReport:
    """Aggregated analysis report for a PR diff."""
    findings: list[Finding] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    verdict: str = "pass"  # "pass", "review", "block"
    summary: str = ""

    @property
    def has_actionable(self) -> bool:
        """True if there are findings worth surfacing in a comment."""
        return len(self.findings) > 0

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")


# ── Security patterns ────────────────────────────────────────────────────────

# Hardcoded secrets: API keys, tokens, passwords in code
SECRET_PATTERNS = [
    (re.compile(r"""(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|secret)\s*[:=]\s*['"][a-zA-Z0-9_\-]{16,}['"]""", re.IGNORECASE), "Hardcoded API key/token"),
    (re.compile(r"""(?:password|passwd|pwd)\s*[:=]\s*['"][^'"]+['"]""", re.IGNORECASE), "Hardcoded password"),
    (re.compile(r"""(?:private[_-]?key|secret[_-]?key)\s*[:=]\s*['"]-----BEGIN""", re.IGNORECASE), "Hardcoded private key"),
    (re.compile(r"""AKIA[0-9A-Z]{16}"""), "AWS access key ID"),
    (re.compile(r"""(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}"""), "GitHub personal access token"),
]

# Injection risks
INJECTION_PATTERNS = [
    (re.compile(r"""execute\s*\(\s*["'][^"']*["']\s*\+\s*""", re.IGNORECASE), "Possible SQL injection via string concatenation in execute()"),
    (re.compile(r"""(?:execute|query|raw)\s*\(\s*\w+\s*\)""", re.IGNORECASE), "SQL query via variable — ensure parameterization"),
    (re.compile(r"""\.format\s*\(.*\).*(?:query|sql|execute|raw)""", re.IGNORECASE), "Possible SQL injection via .format()"),
    (re.compile(r"""f["'][^"']*\{[^}]+\}[^"']*(?:query|sql|execute)""", re.IGNORECASE), "Possible SQL injection via f-string"),
    (re.compile(r"""innerHTML\s*=""", re.IGNORECASE), "XSS risk: innerHTML assignment"),
    (re.compile(r"""document\.write\s*\(""", re.IGNORECASE), "XSS risk: document.write()"),
    (re.compile(r"""eval\s*\(""", re.IGNORECASE), "Code injection risk: eval()"),
    (re.compile(r"""os\.system\s*\(\s*[^'"]""", re.IGNORECASE), "Command injection risk: os.system() with variable"),
    (re.compile(r"""subprocess\..*shell\s*=\s*True""", re.IGNORECASE), "Command injection risk: subprocess with shell=True"),
]

# Path traversal
PATH_TRAVERSAL = re.compile(r"""(?:open|read|write)\s*\(\s*[^,]+\s*\+\s*[^,]+\s*\)""")


# ── Complexity thresholds ────────────────────────────────────────────────────

MAX_NESTING_DEPTH = 4        # Warn at 4+ levels
MAX_FUNCTION_LINES = 50      # Warn at 50+ line functions
MAX_CONDITIONS = 5           # Warn at 5+ conditions in one block


# ── Error handling patterns ─────────────────────────────────────────────────

ERROR_PATTERNS = [
    (re.compile(r"""except\s*:\s*$""", re.MULTILINE), "Bare except clause — catches SystemExit and KeyboardInterrupt"),
    (re.compile(r"""except\s+(?:\w+Exception|Exception)\s+as\s+\w+\s*:\s*(?:#.*)?$""", re.MULTILINE), "Exception handler — verify it's not silently ignored"),
    (re.compile(r"""except\s+.*:\s*#\s*(?:TODO|FIXME|hack|temp)""", re.IGNORECASE | re.MULTILINE), "Exception handler marked as temporary"),
]


# ── Main analyzer ────────────────────────────────────────────────────────────


class DiffAnalyzer:
    """Performs deterministic analysis on PR diffs."""

    def __init__(self, max_lines_for_large_change: int = 300):
        self.max_lines_for_large_change = max_lines_for_large_change

    def analyze(self, files: list[dict]) -> DiffReport:
        """Analyze a list of changed files and return a DiffReport."""
        report = DiffReport()

        total_add = sum(f.get("additions", 0) for f in files)
        total_del = sum(f.get("deletions", 0) for f in files)
        net_change = total_add - total_del
        new_files = [f for f in files if f.get("status") == "added"]
        deleted_files = [f for f in files if f.get("status") == "removed"]
        modified_files = [f for f in files if f.get("status") == "modified"]

        report.stats = {
            "total_add": total_add,
            "total_del": total_del,
            "net_change": net_change,
            "file_count": len(files),
            "new_files": len(new_files),
            "deleted_files": len(deleted_files),
            "modified_files": len(modified_files),
        }

        # Check each file's patch
        for f in files:
            patch = f.get("patch", "")
            if not patch:
                continue
            fname = f.get("filename", "?")
            self._check_security(patch, fname, report)
            self._check_complexity(patch, fname, report)
            self._check_error_handling(patch, fname, report)

        # Check structural patterns across all files
        self._check_structure(files, report)

        # Generate summary and verdict
        self._summarize(report)

        return report

    def _check_security(self, patch: str, fname: str, report: DiffReport):
        """Check for security anti-patterns in added lines only."""
        added_lines = self._get_added_lines(patch)
        text = "\n".join(added_lines)

        for pattern, message in SECRET_PATTERNS:
            if pattern.search(text):
                report.findings.append(Finding(
                    category="security",
                    severity="critical",
                    message=message,
                    file=fname,
                ))

        for pattern, message in INJECTION_PATTERNS:
            if pattern.search(text):
                report.findings.append(Finding(
                    category="security",
                    severity="critical",
                    message=message,
                    file=fname,
                ))

        if PATH_TRAVERSAL.search(text):
            report.findings.append(Finding(
                category="security",
                severity="warning",
                message="Possible path traversal — file path built from variable",
                file=fname,
            ))

    def _check_complexity(self, patch: str, fname: str, report: DiffReport):
        """Estimate complexity of added functions."""
        added_lines = self._get_added_lines(patch)
        if not added_lines:
            return

        # Count functions with excessive nesting
        current_func = None
        func_start = 0
        func_lines = []
        nesting_stack = []

        for i, line in enumerate(added_lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # Detect function/def start
            if stripped.startswith("def ") or stripped.startswith("async def "):
                if current_func and len(func_lines) > MAX_FUNCTION_LINES:
                    report.findings.append(Finding(
                        category="complexity",
                        severity="warning",
                        message=f"Function '{current_func}' is {len(func_lines)} lines (threshold: {MAX_FUNCTION_LINES})",
                        file=fname,
                    ))
                current_func = stripped.split("(")[0].replace("async ", "").replace("def ", "")
                func_lines = [line]
                nesting_stack = [self._nesting_level(line)]
                func_start = i
                continue

            if current_func:
                func_lines.append(line)
                level = self._nesting_level(line)

                # Track nesting depth
                if level > 0:
                    if nesting_stack and level > nesting_stack[-1]:
                        nesting_stack.append(level)
                    elif nesting_stack and level < nesting_stack[-1]:
                        # Dedent
                        while nesting_stack and nesting_stack[-1] > level:
                            nesting_stack.pop()

                    if len(nesting_stack) > MAX_NESTING_DEPTH:
                        report.findings.append(Finding(
                            category="complexity",
                            severity="warning",
                            message=f"Function '{current_func}' has nesting depth {len(nesting_stack)} (threshold: {MAX_NESTING_DEPTH})",
                            file=fname,
                        ))
                        # Don't re-report for same function
                        current_func = None
                        func_lines = []

        # Check final function
        if current_func and len(func_lines) > MAX_FUNCTION_LINES:
            report.findings.append(Finding(
                category="complexity",
                severity="warning",
                message=f"Function '{current_func}' is {len(func_lines)} lines (threshold: {MAX_FUNCTION_LINES})",
                file=fname,
            ))

    def _check_error_handling(self, patch: str, fname: str, report: DiffReport):
        """Check for anti-patterns in error handling."""
        added_lines = self._get_added_lines(patch)
        text = "\n".join(added_lines)

        for pattern, message in ERROR_PATTERNS:
            if pattern.search(text):
                report.findings.append(Finding(
                    category="error_handling",
                    severity="warning",
                    message=message,
                    file=fname,
                ))

    def _check_structure(self, files: list[dict], report: DiffReport):
        """Check structural patterns across all changed files."""
        # Large change set
        total_add = report.stats.get("total_add", 0)
        if total_add > self.max_lines_for_large_change:
            report.findings.append(Finding(
                category="structure",
                severity="info",
                message=f"Large change: +{total_add}/-{report.stats.get('total_del', 0)} lines across {len(files)} files",
            ))

        # New dependencies added
        new_imports = []
        for f in files:
            if f.get("status") not in ("added", "modified"):
                continue
            patch = f.get("patch", "")
            for line in self._get_added_lines(patch):
                stripped = line.strip()
                if stripped.startswith("import ") or stripped.startswith("from "):
                    new_imports.append(stripped)

        if len(new_imports) > 3:
            report.findings.append(Finding(
                category="structure",
                severity="info",
                message=f"{len(new_imports)} new imports — ensure dependencies are intentional",
            ))

        # All files deleted (unusual)
        if report.stats.get("deleted_files", 0) == report.stats.get("file_count", 0) and report.stats.get("file_count", 0) > 2:
            report.findings.append(Finding(
                category="structure",
                severity="warning",
                message=f"All {report.stats['file_count']} changed files are deletions — verify this is not a regression",
            ))

    def _summarize(self, report: DiffReport):
        """Generate summary and verdict from findings."""
        if not report.findings:
            report.summary = "No issues detected by deterministic analysis."
            report.verdict = "pass"
            return

        # Determine verdict
        if any(f.severity == "critical" for f in report.findings):
            report.verdict = "block"
        elif any(f.severity == "warning" for f in report.findings):
            report.verdict = "review"
        else:
            report.verdict = "pass"

        # Build summary
        categories = {}
        for f in report.findings:
            categories.setdefault(f.category, []).append(f)

        parts = []
        for cat, findings in categories.items():
            if len(findings) == 1:
                parts.append(findings[0].message)
            else:
                parts.append(f"{len(findings)} {cat} issues found")

        report.summary = "; ".join(parts)

    @staticmethod
    def _get_added_lines(patch: str) -> list[str]:
        """Extract only added lines from a patch (excluding diff headers)."""
        lines = []
        for line in patch.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                lines.append(line[1:])  # Strip the + prefix
        return lines

    @staticmethod
    def _nesting_level(line: str) -> int:
        """Calculate Python indentation nesting level."""
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            return 0
        indent = len(line) - len(stripped)
        return indent // 4  # Assuming 4-space indentation
