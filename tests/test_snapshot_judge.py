#!/usr/bin/env python3
"""Tests for snapshot_judge.py — pre-push validation of artisan's diff."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from riptide.pipeline.snapshot_judge import SnapshotJudge, truncate_error_log


class TestTruncateErrorLog:
    """Test log truncation utility."""
    
    def test_truncates_long_output(self):
        """Output longer than max_chars is truncated."""
        long_output = "x" * 20_000
        result = truncate_error_log(long_output, max_chars=1000)
        assert len(result) <= 1000 + len("\n... [truncated]")
        assert "... [truncated]" in result
    
    def test_preserves_short_output(self):
        """Output shorter than max_chars is preserved."""
        short_output = "short error message"
        result = truncate_error_log(short_output, max_chars=1000)
        assert result == short_output
    
    def test_strips_dependency_noise(self):
        """Dependency installation logs are stripped."""
        output = textwrap.dedent("""\
            Downloading pytest-7.0.0-py3-none-any.whl (324 kB)
            Collecting requests
            Requirement already satisfied: urllib3 in /usr/lib
            Building wheel for numpy
            Installing packages for wheel
            Resolved 42 packages
            Download https://example.com/package.tar.gz
            Actual error message here
        """)
        result = truncate_error_log(output, max_chars=1000)
        assert "Actual error message here" in result
        assert "Downloading" not in result
        assert "Collecting" not in result
        assert "Requirement already satisfied" not in result
    
    def test_keeps_head_and_tail(self):
        """When output is very long, head and tail are preserved."""
        lines = [f"line {i}" for i in range(300)]
        lines[49] = "HEAD_MARKER"
        lines[-50] = "TAIL_MARKER"
        output = "\n".join(lines)
        
        result = truncate_error_log(output, max_chars=10_000)
        assert "HEAD_MARKER" in result
        assert "TAIL_MARKER" in result
        assert "[truncated middle]" in result
    
    def test_empty_input(self):
        """Empty input returns empty string."""
        assert truncate_error_log("") == ""
    
    def test_truncates_at_newline_boundary(self):
        """Hard cap slices at newline boundary, not mid-word."""
        lines = [f"line {i}" for i in range(100)]
        output = "\n".join(lines)
        
        result = truncate_error_log(output, max_chars=50)
        assert result.endswith("... [truncated]")
        # Should not end mid-word
        assert not result[-20:-15].isalnum()


class TestSnapshotJudgeSyntax:
    """Test syntax checking in snapshot judge."""
    
    def test_catches_syntax_error(self, tmp_path):
        """Syntax errors in Python files are caught."""
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("def foo(\n  pass\n")
        
        diff = {"modified_files": [str(bad_file)], "hunks": [{"file": str(bad_file), "added_lines": ["def foo("]}]}
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        assert not result["valid"]
        assert any("Syntax error" in issue for issue in result["issues"])
    
    def test_passes_valid_syntax(self, tmp_path):
        """Valid Python files pass syntax check."""
        good_file = tmp_path / "good.py"
        good_file.write_text("def foo():\n    return 42\n")
        
        diff = {"modified_files": [str(good_file)], "hunks": [{"file": str(good_file), "added_lines": ["def foo():"]}]}
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        assert result["valid"]
    
    def test_reconstructs_from_diff_when_file_missing(self, tmp_path):
        """When file doesn't exist on disk, diff is applied in-memory.
        
        For new files (not in original_files), defaults to empty string
        so new file content can be validated.
        """
        missing_file = tmp_path / "missing.py"
        
        diff = {
            "modified_files": [str(missing_file)],
            "hunks": [{
                "file": str(missing_file),
                "old_text": "def foo():\n    pass",
                "new_text": "def foo():\n    return 42",
                "added_lines": ["    return 42"],
            }],
            "original_files": {str(missing_file): "def foo():\n    pass"},
        }
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        # Should pass because reconstructed code is valid
        assert result["valid"]
    
    def test_new_file_with_invalid_syntax_reports_issue(self, tmp_path):
        """New files with invalid syntax should be caught."""
        new_file = tmp_path / "new.py"
        
        diff = {
            "modified_files": [str(new_file)],
            "hunks": [{
                "file": str(new_file),
                "old_text": "",
                "new_text": "def foo(\n  pass\n",
                "added_lines": ["def foo(", "  pass"],
            }],
            "original_files": {},  # New file
        }
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        # Should fail because new file has syntax error
        assert not result["valid"]
        assert any("Syntax error" in issue for issue in result["issues"])
    
    def test_new_file_defaults_to_empty_string(self, tmp_path):
        """New files (not in original_files) default to empty string."""
        new_file = tmp_path / "new.py"
        
        diff = {
            "modified_files": [str(new_file)],
            "hunks": [{
                "file": str(new_file),
                "old_text": "",
                "new_text": "def foo():\n    return 42\n",
                "added_lines": ["def foo():", "    return 42"],
            }],
            "original_files": {},  # New file, not in original_files
        }
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        # Should pass because new file is valid
        assert result["valid"]
    
    def test_strict_hunk_matching_requires_context(self):
        """Hunks require at least 3 lines of context for strict matching."""
        # This test verifies that short hunks are rejected
        diff = {
            "modified_files": ["test.py"],
            "hunks": [{
                "file": "test.py",
                "old_text": "x = 1",  # Too short for strict matching
                "new_text": "x = 2",
                "added_lines": ["x = 2"],
            }],
            "original_files": {"test.py": "x = 1\ny = 2\nz = 3\n"},
        }
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        # Should pass because hunk was too short to match (safe behavior)
        assert result["valid"]


class TestSnapshotJudgeFindings:
    """Test finding-addressed validation."""
    
    def test_catches_unaddressed_findings(self, tmp_path):
        """Findings without corresponding diff hunks are flagged."""
        good_file = tmp_path / "good.py"
        good_file.write_text("def foo():\n    return 42\n")
        
        diff = {"modified_files": [str(good_file)], "hunks": [{"file": str(good_file), "added_lines": ["def foo():"]}]}
        judge_findings = {
            "findings": [
                {"file": "other.py", "title": "Issue in other file"},
            ]
        }
        judge = SnapshotJudge(judge_findings, diff)
        result = judge.validate()
        
        assert not result["valid"]
        assert any("not addressed" in issue for issue in result["issues"])
    
    def test_passes_when_all_findings_addressed(self, tmp_path):
        """All findings with corresponding diff hunks pass."""
        good_file = tmp_path / "good.py"
        good_file.write_text("def foo():\n    return 42\n")
        
        diff = {"modified_files": [str(good_file)], "hunks": [{"file": str(good_file), "added_lines": ["def foo():"]}]}
        judge_findings = {
            "findings": [
                {"file": str(good_file), "title": "Issue in this file"},
            ]
        }
        judge = SnapshotJudge(judge_findings, diff)
        result = judge.validate()
        
        assert result["valid"]


class TestSnapshotJudgePlaceholders:
    """Test placeholder detection (AST-aware)."""
    
    def test_catches_placeholder_in_comment(self, tmp_path):
        """Placeholder comments are caught."""
        good_file = tmp_path / "good.py"
        good_file.write_text("def foo():\n    # TODO: implement this\n    return 42\n")
        
        diff = {"modified_files": [str(good_file)], "hunks": [{"file": str(good_file), "added_lines": ["    # TODO: implement this"]}]}
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        assert not result["valid"]
        assert any("Placeholder" in issue for issue in result["issues"])
    
    def test_ignores_placeholder_in_string_literal(self, tmp_path):
        """Placeholder strings in string literals are NOT flagged (AST-aware)."""
        good_file = tmp_path / "good.py"
        good_file.write_text('def foo():\n    x = "TODO: fix this"\n    return x\n')
        
        diff = {"modified_files": [str(good_file)], "hunks": [{"file": str(good_file), "added_lines": ['    x = "TODO: fix this"']}]}
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        assert result["valid"]
    
    def test_ignores_placeholder_in_docstring(self, tmp_path):
        """Placeholder strings in docstrings are NOT flagged (tokenize-aware)."""
        good_file = tmp_path / "good.py"
        good_file.write_text('def foo():\n    """TODO: implement this."""\n    return 42\n')
        
        diff = {"modified_files": [str(good_file)], "hunks": [{"file": str(good_file), "added_lines": ['    """TODO: implement this."""']}]}
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        assert result["valid"]
    
    def test_reports_issue_when_new_file_has_invalid_content(self, tmp_path):
        """When new file has invalid content, validation reports issue."""
        new_file = tmp_path / "bad.py"
        
        diff = {
            "modified_files": [str(new_file)],
            "hunks": [{
                "file": str(new_file),
                "old_text": "",
                "new_text": "def foo(\n  pass\n",  # Invalid syntax
                "added_lines": ["def foo(", "  pass"],
            }],
            "original_files": {},  # New file
        }
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        # Should report syntax error
        assert not result["valid"]
        assert any("Syntax error" in issue for issue in result["issues"])


class TestSnapshotJudgeBrokenPatterns:
    """Test broken pattern detection (AST-aware)."""
    
    def test_catches_bare_except(self, tmp_path):
        """Bare except clauses are caught via AST."""
        good_file = tmp_path / "good.py"
        good_file.write_text("try:\n    pass\nexcept:\n    pass\n")
        
        diff = {"modified_files": [str(good_file)], "hunks": [{"file": str(good_file), "added_lines": ["except:"]}]}
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        assert not result["valid"]
        assert any("Bare except" in issue for issue in result["issues"])
    
    def test_ignores_bare_except_in_string(self, tmp_path):
        """Bare except in string literals is NOT flagged."""
        good_file = tmp_path / "good.py"
        good_file.write_text('x = "except: something"\n')
        
        diff = {"modified_files": [str(good_file)], "hunks": [{"file": str(good_file), "added_lines": ['x = "except: something"']}]}
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        assert result["valid"]
    
    def test_catches_empty_function(self, tmp_path):
        """Empty function bodies (pass-only) are caught."""
        good_file = tmp_path / "good.py"
        good_file.write_text("def foo():\n    pass\n")
        
        diff = {"modified_files": [str(good_file)], "hunks": [{"file": str(good_file), "added_lines": ["def foo():"]}]}
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        assert not result["valid"]
        assert any("Empty" in issue for issue in result["issues"])
    
    def test_catches_wildcard_import(self, tmp_path):
        """Wildcard imports are caught."""
        good_file = tmp_path / "good.py"
        good_file.write_text("from os import *\n")
        
        diff = {"modified_files": [str(good_file)], "hunks": [{"file": str(good_file), "added_lines": ["from os import *"]}]}
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        assert not result["valid"]
        assert any("Wildcard import" in issue for issue in result["issues"])
    
    def test_reports_issue_when_new_file_has_invalid_content(self, tmp_path):
        """When new file has invalid content, validation reports issue."""
        new_file = tmp_path / "bad.py"
        
        diff = {
            "modified_files": [str(new_file)],
            "hunks": [{
                "file": str(new_file),
                "old_text": "",
                "new_text": "def foo(\n  pass\n",  # Invalid syntax
                "added_lines": ["def foo(", "  pass"],
            }],
            "original_files": {},  # New file
        }
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        # Should report syntax error
        assert not result["valid"]
        assert any("Syntax error" in issue for issue in result["issues"])


class TestSnapshotJudgeNonPython:
    """Test snapshot judge with non-Python files."""
    
    def test_non_python_placeholder_simple_match(self, tmp_path):
        """Non-Python files use simple string matching for placeholders."""
        js_file = tmp_path / "test.js"
        js_file.write_text("// TODO: implement\nconsole.log('hi');\n")
        
        diff = {"modified_files": [str(js_file)], "hunks": [{"file": str(js_file), "added_lines": ["// TODO: implement"]}]}
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        assert not result["valid"]
        assert any("Placeholder" in issue for issue in result["issues"])
    
    def test_non_python_syntax_not_checked(self, tmp_path):
        """Non-Python files don't get syntax checked."""
        js_file = tmp_path / "test.js"
        js_file.write_text("this is not valid javascript {{{")
        
        diff = {"modified_files": [str(js_file)], "hunks": [{"file": str(js_file), "added_lines": ["this is not valid javascript {{{"]}]}
        judge = SnapshotJudge({"findings": []}, diff)
        result = judge.validate()
        
        assert result["valid"]
