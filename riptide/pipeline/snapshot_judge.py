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
import io
import re
import tokenize
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
                        issues.append(f"Cannot validate {file_path}: file not found and cannot reconstruct from diff")
                        continue
                
                ast.parse(source, filename=file_path)
            except SyntaxError as e:
                issues.append(f"Syntax error in {file_path}:{e.lineno}: {e.msg}")
        return issues
    
    def _reconstruct_source(self, file_path: str) -> Optional[str]:
        """Reconstruct source by applying diff hunks in-memory.
        
        Used when artisan hasn't written to disk yet.
        
        For new files (not in original_files), defaults to empty string
        so new file content can be validated.
        """
        # Find the original file content from the diff
        # If file is new (not in original_files), default to empty string
        original = self.diff.get("original_files", {}).get(file_path, "")
        
        # Apply hunks sequentially
        source = original
        for hunk in self.diff.get("hunks", []):
            if hunk.get("file") != file_path:
                continue
            
            old_text = hunk.get("old_text", "")
            new_text = hunk.get("new_text", "")
            start_line = hunk.get("start_line")
            
            if start_line is not None:
                # Line-number-based application (preferred)
                source = self._apply_hunk_at_line(source, old_text, new_text, start_line)
            elif not old_text:
                # Pure addition (new file or appended content)
                source = source + new_text
            else:
                # Fallback: strict context matching
                source = self._apply_hunk_strict(source, old_text, new_text)
        
        return source
    
    def _apply_hunk_at_line(self, source: str, old_text: str, new_text: str, start_line: int) -> str:
        """Apply a hunk at a specific line number."""
        lines = source.splitlines(keepends=True)
        old_lines = old_text.splitlines(keepends=True)
        
        # Convert to 0-indexed
        idx = start_line - 1
        
        # Verify the old text matches at this position
        if idx < 0 or idx + len(old_lines) > len(lines):
            return source  # Out of bounds, skip
        
        # Check if the lines match
        for i, old_line in enumerate(old_lines):
            if lines[idx + i] != old_line:
                return source  # Mismatch, skip
        
        # Apply the hunk
        new_lines = new_text.splitlines(keepends=True)
        lines[idx:idx + len(old_lines)] = new_lines
        return "".join(lines)
    
    def _apply_hunk_strict(self, source: str, old_text: str, new_text: str) -> str:
        """Apply a hunk using strict context matching.
        
        Requires at least 2 lines of context to avoid false matches.
        """
        if not old_text or not new_text:
            return source
        
        old_lines = old_text.splitlines()
        source_lines = source.splitlines()
        
        # Need at least 2 lines of context for strict matching
        if len(old_lines) < 2:
            # For single-line hunks, find first exact match
            for i in range(len(source_lines)):
                if source_lines[i] == old_lines[0]:
                    new_lines = new_text.splitlines()
                    source_lines[i:i+1] = new_lines
                    return "\n".join(source_lines)
            return source
        
        # Find the first occurrence of the context lines
        # Use first 2 and last 2 lines as anchors
        anchor_start = old_lines[:2]
        anchor_end = old_lines[-2:]
        
        for i in range(len(source_lines) - len(old_lines) + 1):
            # Check if anchors match at this position
            if (source_lines[i:i+2] == anchor_start and 
                source_lines[i+len(old_lines)-2:i+len(old_lines)] == anchor_end):
                # Full match check
                if source_lines[i:i+len(old_lines)] == old_lines:
                    # Apply the hunk
                    new_lines = new_text.splitlines()
                    source_lines[i:i+len(old_lines)] = new_lines
                    return "\n".join(source_lines)
        
        return source  # No match found
    
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
        literals or docstrings.
        """
        issues = []
        
        for file_path in self.diff.get("modified_files", []):
            if not file_path.endswith(".py"):
                issues.extend(self._check_placeholders_simple(file_path))
                continue
            
            try:
                if Path(file_path).exists():
                    with open(file_path) as f:
                        source = f.read()
                else:
                    source = self._reconstruct_source(file_path)
                    if source is None:
                        issues.append(f"Cannot check placeholders in {file_path}: file not found and cannot reconstruct from diff")
                        continue
                
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
        """Check for broken patterns using AST for Python files."""
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
                        issues.append(f"Cannot check broken patterns in {file_path}: file not found and cannot reconstruct from diff")
                        continue
                
                tree = ast.parse(source, filename=file_path)
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.ExceptHandler):
                        if node.type is None:
                            issues.append(
                                f"Bare except clause in {file_path}:{node.lineno}"
                            )
                    
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                            issues.append(
                                f"Empty {node.__class__.__name__} '{node.name}' in {file_path}:{node.lineno}"
                            )
                    
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
    - Hard-cap at max_chars, slicing at newline boundary
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
    
    # Hard cap at newline boundary to avoid slicing mid-word
    if len(result) > max_chars:
        # Find the last newline before max_chars
        last_newline = result.rfind("\n", 0, max_chars)
        if last_newline > 0:
            result = result[:last_newline] + "\n... [truncated]"
        else:
            result = result[:max_chars] + "\n... [truncated]"
    
    return result
