"""Tests for riptide.pipeline.judge — security findings must survive judging."""
from riptide.pipeline.judge import Judge


def _probe(*findings):
    return {"diff_report": {"findings": list(findings)}, "previous_findings": []}


SECRET = {"file": "app.py", "line_hint": 3, "severity": "critical",
          "category": "security", "message": "Hardcoded secret: AWS key"}


def test_a_security_finding_is_kept():
    assert Judge(_probe(SECRET)).evaluate()["findings"][0]["title"] == SECRET["message"]


def test_a_warning_security_finding_is_kept():
    f = dict(SECRET, severity="warning", message="eval() on user input")
    assert len(Judge(_probe(f)).evaluate()["findings"]) == 1


def test_security_findings_win_the_three_finding_cap():
    noise = [{"file": "a.py", "line_hint": i, "severity": "warning",
              "category": "complexity", "message": f"unused variable {i}"} for i in range(5)]
    out = Judge(_probe(*noise, SECRET)).evaluate()["findings"]
    assert len(out) == 3
    assert out[0]["severity"] == "critical"


def test_out_of_focus_style_findings_are_still_dropped():
    f = {"file": "a.py", "line_hint": 1, "severity": "warning",
         "category": "complexity", "message": "function too long"}
    assert Judge(_probe(f)).evaluate()["findings"] == []
