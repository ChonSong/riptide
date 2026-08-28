#!/usr/bin/env python3
"""snapshot_judge.py — Pre-push validation of artisan's diff against judge's intent.

Catches errors BEFORE engine runs tests or pushes to remote:
- Syntax errors in edited files (via AST parse)
- Findings not addressed in diff
- Placeholder comments left behind (AST-aware, not string matching)
- Bare except clauses (AST-aware, not string matching)
- File state validation (applies diff in-memory if needed)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional


# Patterns that indicate incomplete work — only matched in actual comments
# (not string literals) when AST is available.
PLACEHOLDER_PATTERNS = ["TODO", "FIXME", "XXX", "HACK", "PLACEHOLDER"]

# Maximum string length for error context passed to downstream workers
MAX_ERROR_CONTEXT_CHARS = 12_000  # ~3k tokens


class SnapshotJudge:
    """Validates that artisan's diff matches judge's original intent.
    
    Uses AST-based analysis for Python files to avoid false positives
    from string literals and docstrings. Falls back to comment-aware
    string matching for non-Python files.
    """
    
    def __init__(self, judge_findings: dict, diff: dict, strict: bool = True):
        self.findings = judge_findings
        self.diff = diff
        self.strict = strict
    
    def validate(self) -> dict:
        """Run all validation checks."""
        issues = []
        
        # Check 1: Syntax validation for Python files (AST-based)
        issues.extend(self._check_syntax())
        
        # Check 2: All findings have corresponding changes
        issues.extend(self._check_findings_addressed())
        
        # Check 3: No placeholder patterns (AST-aware for Python)
        issues.extend(self._check_no_placeholders())
        
        # Check 4: No obviously broken patterns (AST-based for Python)
        issues.extend(self._check_broken_patterns())
        
        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "correction_context": {
                "findings": self.findings,
                "diff": self.diff,
                "issues": issues,
                "suggestions": self._generate_suggestions(issues),
            }
        }
    
    def _check_syntax(self) -> list[str]:
        """Check for syntax errors in edited Python files.
        
        Reads from disk — assumes artisan has already written changes.
        If file doesn't exist on disk, attempts to apply diff in-memory.
        If reconstruction fails, reports an issue (does NOT skip).
        """
        issues = []
        for file_path in self.diff.get("modified_files", []):
            if not file_path.endswith(".py"):
                continue
            try:
                # Try reading from disk first (artisan may have written it)
                if Path(file_path).exists():
                    with open(file_path) as f:
                        source = f.read()
                else:
                    # Fall back: apply diff in-memory to original
                    source = self._reconstruct_source(file_path)
                    if source is None:
                        # Cannot reconstruct — report as issue, don't skip
                        issues.append(f"Cannot validate {file_path}: file not found and cannot reconstruct from diff")
                        continue
                
                ast.parse(source, filename=file_path)
            except SyntaxError as e:
                issues.append(f"Syntax error in {file_path}:{e.lineno}: {e.msg}")
        return issues
    
    def _reconstruct_source(self, file_path: str) -> Optional[str]:
        """Reconstruct source by applying diff hunks in-memory.
        
        Used when artisan hasn't written to disk yet.
        Returns None if reconstruction fails.
        """
        # Find the original file content from the diff
        original = self.diff.get("original_files", {}).get(file_path)
        if original is None:
            return None
        
        # Apply hunks sequentially
        source = original
        for hunk in self.diff.get("hunks", []):
            if hunk.get("file") != file_path:
                continue
            old_text = hunk.get("old_text", "")
            new_text = hunk.get("new_text", "")
            if old_text in source:
                source = source.replace(old_text, new_text, 1)
        
        return source
    
    def _check_findings_addressed(self) -> list[str]:
        """Check that all judge findings have corresponding diff hunks."""
        issues = []
        diff_files = {h.get("file") for h in self.diff.get("hunks", [])}
        
        for finding in self.findings.get("findings", []):
            file = finding.get("file", "")
            if file and file not in diff_files:
                issues.append(f"Finding not addressed: {finding.get('title')} in {file}")
        return issues
    
    def _check_no_placeholders(self) -> list[str]:
        """Check for placeholder comments using tokenize for Python files.
        
        Uses tokenize.COMMENT to find actual comments only - NOT string
        literals or docstrings. This avoids false positives like:
            x = "TODO: fix this"  # string literal, NOT a comment
            triple-quoted docstrings with TODO in them, NOT a comment
        """
        issues = []
        
        for file_path in self.diff.get("modified_files", []):
            if not file_path.endswith(".py"):
                # Non-Python: use simple string matching on added lines
                issues.extend(self._check_placeholders_simple(file_path))
                continue
            
            # Python: use tokenize to get actual comments only
            try:
                if Path(file_path).exists():
                    with open(file_path) as f:
                        source = f.read()
                else:
                    source = self._reconstruct_source(file_path)
                    if source is None:
                        continue
                
                import tokenize
                import io
                try:
                    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
                    for tok_type, tok_string, start, end, line in tokens:
                        if tok_type == tokenize.COMMENT:
                            for pattern in PLACEHOLDER_PATTERNS:
                                if pattern in tok_string:
                                    issues.append(
                                        f"Placeholder in {file_path}:{start[0]}: {tok_string.strip()}"
                                    )
                except tokenize.TokenError:
                    pass  # Syntax errors caught by _check_syntax
                        
            except SyntaxError:
                pass  # Already caught by _check_syntax
        
        return issues
    
    def _check_placeholders_simple(self, file_path: str) -> list[str]:
        """Simple string matching for non-Python files."""
        issues = []
        for hunk in self.diff.get("hunks", []):
            if hunk.get("file") != file_path:
                continue
            for line in hunk.get("added_lines", []):
                stripped = line.strip()
                for pattern in PLACEHOLDER_PATTERNS:
                    if pattern in stripped:
                        issues.append(f"Placeholder in {file_path}: {stripped}")
        return issues
    
    def _check_broken_patterns(self) -> list[str]:
        """Check for broken patterns using AST for Python files.
        
        Uses AST to detect bare except clauses, avoiding false positives
        from string literals and docstrings.
        """
        issues = []
        
        for file_path in self.diff.get("modified_files", []):
            if not file_path.endswith(".py"):
                continue
            
            try:
                if Path(file_path).exists():
                    with open(file_path) as f:
                        source = f.read()
                else:
                    source = self._reconstruct_source(file_path)
                    if source is None:
                        continue
                
                tree = ast.parse(source, filename=file_path)
                
                for node in ast.walk(tree):
                    # Detect bare except clauses
                    if isinstance(node, ast.ExceptHandler):
                        if node.type is None:  # bare except:
                            issues.append(
                                f"Bare except clause in {file_path}:{node.lineno}"
                            )
                    
                    # Detect empty pass blocks (function/class with only pass)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                            issues.append(
                                f"Empty {node.__class__.__name__} '{node.name}' in {file_path}:{node.lineno}"
                            )
                    
                    # Detect wildcard imports
                    if isinstance(node, ast.ImportFrom):
                        for alias in node.names:
                            if alias.name == "*":
                                issues.append(
                                    f"Wildcard import in {file_path}:{node.lineno}: from {node.module} import *"
                                )
                                
            except SyntaxError:
                pass  # Already caught by _check_syntax
        
        return issues
    
    def _generate_suggestions(self, issues: list[str]) -> list[str]:
        """Generate human-readable correction suggestions."""
        suggestions = []
        for issue in issues:
            if "Syntax error" in issue:
                suggestions.append(f"Fix syntax: {issue}")
            elif "not addressed" in issue:
                suggestions.append(f"Apply fix: {issue}")
            elif "Placeholder" in issue:
                suggestions.append(f"Complete implementation: {issue}")
            elif "Bare except" in issue:
                suggestions.append(f"Use specific exception type: {issue}")
            elif "Empty" in issue:
                suggestions.append(f"Implement the function/class body: {issue}")
            elif "Wildcard import" in issue:
                suggestions.append(f"Use explicit imports: {issue}")
            else:
                suggestions.append(f"Review: {issue}")
        return suggestions


def truncate_error_log(raw_output: str, max_chars: int = MAX_ERROR_CONTEXT_CHARS) -> str:
    """Truncate error output to prevent context window blowout.
    
    Strategy:
    - Strip dependency installation logs
    - Keep first 50 lines and last 100 lines (where tracebacks live)
    - Hard-cap at max_chars
    """
    if not raw_output:
        return ""
    
    lines = raw_output.splitlines()
    
    # Strip common noise patterns
    noise_patterns = [
        r"^Downloading\s+",
        r"^Collecting\s+",
        r"^Requirement already satisfied:",
        r"^\s*Building wheel for",
        r"^\s*Installing packages for",
        r"^\s*Resolved\s+\d+packages",
        r"^\s*Downloading\s+https?://",
    ]
    
    filtered = []
    for line in lines:
        is_noise = any(re.match(p, line) for p in noise_patterns)
        if not is_noise:
            filtered.append(line)
    
    # If still too long, keep first 50 + last 100 lines
    if len(filtered) > 200:
        head = filtered[:50]
        tail = filtered[-100:]
        filtered = head + ["\n... [truncated middle] ...\n"] + tail
    
    result = "\n".join(filtered)
    
    # Hard cap
    if len(result) > max_chars:
        result = result[:max_chars] + "\n... [truncated]"
    
    return result
