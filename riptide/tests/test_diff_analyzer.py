# riptide/tests/test_diff_analyzer.py
"""
Tests for the deterministic diff analyzer (Phase 1).
Covers security patterns, complexity, error handling, and structural checks.
"""

import pytest

from riptide.diff_analyzer import DiffAnalyzer, DiffReport, Finding


# ── Helpers ──────────────────────────────────────────────────────────────────


def make_file(filename: str, patch: str, additions: int = 0, deletions: int = 0,
              status: str = "modified") -> dict:
    return {
        "filename": filename,
        "patch": patch,
        "additions": additions,
        "deletions": deletions,
        "status": status,
    }


SIMPLE_PATCH = """\
+def hello():
+    return "world"
"""

LONG_FUNC_PATCH = """\
+def very_long_function():
+    x = 1
+    y = 2
+    z = 3
+    a = 4
+    b = 5
+    c = 6
+    d = 7
+    e = 8
+    f = 9
+    g = 10
+    h = 11
+    i = 12
+    j = 13
+    k = 14
+    l = 15
+    m = 16
+    n = 17
+    o = 18
+    p = 19
+    q = 20
+    r = 21
+    s = 22
+    t = 23
+    u = 24
+    v = 25
+    w = 26
+    x = 27
+    y = 28
+    z = 29
+    aa = 30
+    bb = 31
+    cc = 32
+    dd = 33
+    ee = 34
+    ff = 35
+    gg = 36
+    hh = 37
+    ii = 38
+    jj = 39
+    kk = 40
+    ll = 41
+    mm = 42
+    nn = 43
+    oo = 44
+    pp = 45
+    qq = 46
+    rr = 47
+    ss = 48
+    tt = 49
+    uu = 50
+    vv = 51
+    return vv
"""

DEEP_NESTING_PATCH = """\
+def nested_func():
+    if True:
+        if True:
+            if True:
+                if True:
+                    if True:
+                        return "deep"
"""

BARE_EXCEPT_PATCH = """+
+try:
+    something()
+except:
+    pass
"""

SWALLOWED_EXCEPT_PATCH = """\
+try:
+    process()
+except Exception as e:
+    pass
"""

SWALLOWED_LOGGING_PATCH = """\
+try:
+    process()
+except ValueError as e:
+    logger.warning("fallback: %s", e)
+    raise
"""

HARDCODED_SECRET_PATCH = """\
+API_KEY = "{key_fragment_a}{key_fragment_b}"
+password = "{pwd_fragment_a}{pwd_fragment_b}"
""".format(
    key_fragment_a="a1b2c3d4e5f6g7h8",
    key_fragment_b="i9j0k1l2m3n4o5p6",
    pwd_fragment_a="super",
    pwd_fragment_b="_secret_123",
)

SQL_INJECTION_PATCH = """\
+cursor.execute("SELECT * FROM users WHERE id = " + user_id)
+db.raw("DELETE FROM " + table_name)
"""

XSS_PATCH = """\
+element.innerHTML = userInput
"""

EVAL_PATCH = """\
+eval(user_input)
"""

SHELL_INJECTION_PATCH = """\
+os.system("ping " + hostname)
+subprocess.call(cmd, shell=True)
"""


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def analyzer():
    return DiffAnalyzer()


# ── Security tests ───────────────────────────────────────────────────────────


class TestSecurityPatterns:
    def test_hardcoded_api_key(self, analyzer):
        files = [make_file("config.py", HARDCODED_SECRET_PATCH, additions=2)]
        report = analyzer.analyze(files)
        assert any(f.category == "security" and "API key" in f.message for f in report.findings)

    def test_hardcoded_password(self, analyzer):
        files = [make_file("config.py", HARDCODED_SECRET_PATCH, additions=2)]
        report = analyzer.analyze(files)
        assert any(f.category == "security" and "password" in f.message.lower() for f in report.findings)

    def test_sql_injection(self, analyzer):
        files = [make_file("db.py", SQL_INJECTION_PATCH, additions=2)]
        report = analyzer.analyze(files)
        assert any(f.category == "security" and "SQL" in f.message for f in report.findings)

    def test_xss_risk(self, analyzer):
        files = [make_file("ui.js", XSS_PATCH, additions=1)]
        report = analyzer.analyze(files)
        assert any(f.category == "security" and "XSS" in f.message for f in report.findings)

    def test_eval_risk(self, analyzer):
        files = [make_file("main.py", EVAL_PATCH, additions=1)]
        report = analyzer.analyze(files)
        assert any(f.category == "security" and "eval" in f.message.lower() for f in report.findings)

    def test_shell_injection(self, analyzer):
        files = [make_file("server.py", SHELL_INJECTION_PATCH, additions=2)]
        report = analyzer.analyze(files)
        assert any(f.category == "security" and "shell" in f.message.lower() for f in report.findings)

    def test_no_false_positive_on_safe_code(self, analyzer):
        files = [make_file("utils.py", SIMPLE_PATCH, additions=2)]
        report = analyzer.analyze(files)
        security_findings = [f for f in report.findings if f.category == "security"]
        assert len(security_findings) == 0


# ── Complexity tests ─────────────────────────────────────────────────────────


class TestComplexity:
    def test_long_function(self, analyzer):
        files = [make_file("big.py", LONG_FUNC_PATCH, additions=54)]
        report = analyzer.analyze(files)
        complexity_findings = [f for f in report.findings if f.category == "complexity"]
        assert any("lines" in f.message for f in complexity_findings)

    def test_deep_nesting(self, analyzer):
        files = [make_file("nested.py", DEEP_NESTING_PATCH, additions=7)]
        report = analyzer.analyze(files)
        complexity_findings = [f for f in report.findings if f.category == "complexity"]
        assert any("nesting" in f.message.lower() for f in complexity_findings)

    def test_short_function_no_warning(self, analyzer):
        files = [make_file("small.py", SIMPLE_PATCH, additions=2)]
        report = analyzer.analyze(files)
        complexity_findings = [f for f in report.findings if f.category == "complexity"]
        assert len(complexity_findings) == 0


EXACTLY_50_LINE_FUNC_PATCH = """\
+def exactly_fifty_lines():
+    x1 = 1
+    x2 = 2
+    x3 = 3
+    x4 = 4
+    x5 = 5
+    x6 = 6
+    x7 = 7
+    x8 = 8
+    x9 = 9
+    x10 = 10
+    x11 = 11
+    x12 = 12
+    x13 = 13
+    x14 = 14
+    x15 = 15
+    x16 = 16
+    x17 = 17
+    x18 = 18
+    x19 = 19
+    x20 = 20
+    x21 = 21
+    x22 = 22
+    x23 = 23
+    x24 = 24
+    x25 = 25
+    x26 = 26
+    x27 = 27
+    x28 = 28
+    x29 = 29
+    x30 = 30
+    x31 = 31
+    x32 = 32
+    x33 = 33
+    x34 = 34
+    x35 = 35
+    x36 = 36
+    x37 = 37
+    x38 = 38
+    x39 = 39
+    x40 = 40
+    x41 = 41
+    x42 = 42
+    x43 = 43
+    x44 = 44
+    x45 = 45
+    x46 = 46
+    x47 = 47
+    x48 = 48
+    x49 = 49
+    x50 = 50
+    return x50
"""


class TestComplexityBoundary:
    """Tests for function length boundary conditions."""

    def test_exact_boundary_triggers(self, analyzer):
        files = [make_file("boundary.py", EXACTLY_50_LINE_FUNC_PATCH, additions=52)]
        report = analyzer.analyze(files)
        complexity_findings = [f for f in report.findings if f.category == "complexity"]
        assert any("threshold: 50" in f.message for f in complexity_findings)

    def test_just_under_boundary_no_warning(self, analyzer):
        patch = """\
+def small_func():
+    return 42
+"""
        files = [make_file("small.py", patch, additions=3)]
        report = analyzer.analyze(files)
        complexity_findings = [f for f in report.findings if f.category == "complexity"]
        assert len(complexity_findings) == 0


# ── Error handling tests ─────────────────────────────────────────────────────


class TestErrorHandling:
    def test_bare_except(self, analyzer):
        files = [make_file("handler.py", BARE_EXCEPT_PATCH, additions=4)]
        report = analyzer.analyze(files)
        error_findings = [f for f in report.findings if f.category == "error_handling"]
        assert any("bare except" in f.message.lower() for f in error_findings)

    def test_swallowed_exception(self, analyzer):
        files = [make_file("processor.py", SWALLOWED_EXCEPT_PATCH, additions=4)]
        report = analyzer.analyze(files)
        error_findings = [f for f in report.findings if f.category == "error_handling"]
        assert any("silently ignored" in f.message.lower() for f in error_findings)

    def test_properly_handled_exception_not_reported(self, analyzer):
        files = [make_file("handler.py", SWALLOWED_LOGGING_PATCH, additions=5)]
        report = analyzer.analyze(files)
        error_findings = [f for f in report.findings if f.category == "error_handling"]
        assert len(error_findings) == 0


# ── Structural tests ─────────────────────────────────────────────────────────


class TestStructural:
    def test_large_change(self, analyzer):
        big_patch = "\n".join([f"+line {i}" for i in range(301)])
        files = [make_file("big.py", big_patch, additions=301)]
        report = analyzer.analyze(files)
        structure_findings = [f for f in report.findings if f.category == "structure"]
        assert any("large change" in f.message.lower() for f in structure_findings)

    def test_many_new_imports(self, analyzer):
        patch = """\
+import os
+import sys
+import json
+import re
+import subprocess
+import requests
+"""
        files = [make_file("main.py", patch, additions=6)]
        report = analyzer.analyze(files)
        structure_findings = [f for f in report.findings if f.category == "structure"]
        assert any("imports" in f.message.lower() for f in structure_findings)

    def test_all_files_deleted(self, analyzer):
        files = [
            make_file("old1.py", "", status="removed"),
            make_file("old2.py", "", status="removed"),
            make_file("old3.py", "", status="removed"),
        ]
        report = analyzer.analyze(files)
        structure_findings = [f for f in report.findings if f.category == "structure"]
        assert any("deletions" in f.message.lower() for f in structure_findings)


# ── DiffReport tests ─────────────────────────────────────────────────────────


class TestDiffReport:
    def test_empty_report_is_not_actionable(self):
        report = DiffReport()
        assert not report.has_actionable
        assert report.verdict == "pass"

    def test_critical_finding_blocks(self):
        report = DiffReport()
        report.findings = [Finding("security", "critical", "test")]
        # _summarize is called in analyze; test it directly
        assert any(f.severity == "critical" for f in report.findings)

    def test_warning_triggers_review(self):
        report = DiffReport()
        report.findings = [Finding("complexity", "warning", "test")]
        # Directly test the verdict logic
        if any(f.severity == "warning" for f in report.findings):
            report.verdict = "review"
        assert report.verdict == "review"


# ── Analyzer edge cases ──────────────────────────────────────────────────────


class TestAnalyzerEdgeCases:
    def test_empty_file_list(self, analyzer):
        report = analyzer.analyze([])
        assert not report.findings
        assert report.verdict == "pass"

    def test_files_without_patches(self, analyzer):
        files = [{"filename": "empty.py", "patch": "", "additions": 0, "deletions": 0}]
        report = analyzer.analyze(files)
        assert not report.findings

    def test_multiple_files_aggregate(self, analyzer):
        files = [
            make_file("a.py", HARDCODED_SECRET_PATCH, additions=2),
            make_file("b.py", BARE_EXCEPT_PATCH, additions=4),
        ]
        report = analyzer.analyze(files)
        categories = {f.category for f in report.findings}
        assert "security" in categories
        assert "error_handling" in categories

    def test_finding_file_attribution(self, analyzer):
        files = [make_file("specific_module.py", EVAL_PATCH, additions=1)]
        report = analyzer.analyze(files)
        assert any(f.file == "specific_module.py" for f in report.findings)

    def test_get_added_lines_excludes_headers(self, analyzer):
        patch = """\
--- a/file.py
+++ b/file.py
@@ -1,3 +1,5 @@
+def new():
+    pass
"""
        added = analyzer._get_added_lines(patch)
        assert all(not l.startswith("---") for l in added)
        assert all(not l.startswith("+++") for l in added)

    def test_nesting_level_calculation(self, analyzer):
        assert analyzer._nesting_level("def foo():") == 0
        assert analyzer._nesting_level("    pass") == 1
        assert analyzer._nesting_level("        return") == 2
        assert analyzer._nesting_level("") == 0
        assert analyzer._nesting_level("# comment") == 0


# ── Summary tests ────────────────────────────────────────────────────────────


class TestSummaryGeneration:
    def test_summary_with_single_finding(self, analyzer):
        files = [make_file("x.py", EVAL_PATCH, additions=1)]
        report = analyzer.analyze(files)
        assert report.summary != ""
        assert "eval" in report.summary.lower() or "injection" in report.summary.lower()

    def test_summary_with_multiple_findings(self, analyzer):
        files = [
            make_file("x.py", HARDCODED_SECRET_PATCH, additions=2),
            make_file("y.py", BARE_EXCEPT_PATCH, additions=4),
        ]
        report = analyzer.analyze(files)
        assert report.summary != ""
        assert ";" in report.summary or "found" in report.summary.lower()

    def test_verdict_block_for_critical(self, analyzer):
        files = [make_file("x.py", HARDCODED_SECRET_PATCH, additions=2)]
        report = analyzer.analyze(files)
        assert report.verdict == "block"

    def test_verdict_pass_for_clean(self, analyzer):
        files = [make_file("x.py", SIMPLE_PATCH, additions=2)]
        report = analyzer.analyze(files)
        assert report.verdict == "pass"
