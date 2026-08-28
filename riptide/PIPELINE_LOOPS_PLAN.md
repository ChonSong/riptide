# Pipeline Loop Architecture — Implementation Plan

## Problem

The current `create_fix_pipeline` is a **strictly linear progression**:

```
probe → judge → artisan → engine (tests + push) → ci_verifier → scribe
```

This is fragile because:
- LLMs hallucinate bad fixes → syntax errors reach CI
- Artisan edits may not match Judge's intent → wasted compute on broken tests
- CI failures are terminal → no self-correction, just a failure summary
- No iteration cap → potential infinite retry loops

## Solution: Two Feedback Loops

### Loop 1: Pre-Push Snapshot Validation (Judge Reassessment)

**Insert between Artisan and Engine:**

```
artisan edits files
       ↓
snapshot_judge: fetch diff → compare against judge's intent
       ↓
   ┌─── match? ───┐
   │ YES          │ NO (max 3 retries)
   ↓              ↓
engine          artisan (with correction context)
```

**Purpose:** Catch obvious errors (syntax, incomplete fixes, drift from intent) BEFORE wasting time on local tests or polluting the remote branch.

### Loop 2: Post-Execution Recovery (Error Feedback)

**Wrap Engine and CI_Verifier:**

```
engine runs tests
       ↓
   ┌─── pass? ───┐
   │ YES         │ NO (max 3 retries)
   ↓             ↓
push           judge (classify error) → artisan (with error context)
                       ↓
                  engine (re-run tests)
```

**Purpose:** Turn a basic script into an autonomous agent that iteratively debugs its own work.

## Architecture Changes

### 1. New Worker: `snapshot_judge.py`

```python
class SnapshotJudge:
    """Validates that artisan's diff matches judge's original intent."""
    
    def __init__(self, judge_findings: dict, diff: dict):
        self.findings = judge_findings
        self.diff = diff
    
    def validate(self) -> dict:
        """
        Returns:
            {"valid": bool, "issues": list[str], "correction_context": dict}
        """
        issues = []
        
        # Check 1: All findings have corresponding diff hunks
        for finding in self.findings.get("findings", []):
            file = finding.get("file", "")
            if not any(h.get("file") == file for h in self.diff.get("hunks", [])):
                issues.append(f"No changes for finding in {file}: {finding.get('title')}")
        
        # Check 2: No syntax errors in edited files
        for file in self.diff.get("modified_files", []):
            if file.endswith(".py"):
                try:
                    compile(open(file).read(), file, "exec")
                except SyntaxError as e:
                    issues.append(f"Syntax error in {file}: {e}")
        
        # Check 3: No obviously broken patterns
        for hunk in self.diff.get("hunks", []):
            added = hunk.get("added_lines", [])
            for line in added:
                if "TODO" in line or "FIXME" in line or "XXX" in line:
                    issues.append(f"Placeholder left in {hunk.get('file')}: {line.strip()}")
        
        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "correction_context": {
                "findings": self.findings,
                "diff": self.diff,
                "issues": issues,
            }
        }
```

### 2. Modified `conductor.py` — Loop Logic

```python
MAX_LOOP_ITERATIONS = 3

def _run_fix_pipeline_with_loops(self, track_id: str) -> dict:
    """Run fix pipeline with pre-push validation and post-execution recovery."""
    
    results = []
    
    # Phase 1: Probe (gather context)
    results.append(self._run_workstream("ws-1-probe", get_workstream(track_id, "ws-1-probe")))
    
    # Phase 2: Judge (verify findings, produce fix plan)
    results.append(self._run_workstream("ws-2-judge", get_workstream(track_id, "ws-2-judge")))
    judge_output = get_workstream(track_id, "ws-2-judge").get("outputs", {})
    
    # Phase 3: Artisan + Snapshot Judge Loop (pre-push validation)
    snapshot_valid = False
    for attempt in range(MAX_LOOP_ITERATIONS):
        results.append(self._run_workstream("ws-3-artisan", get_workstream(track_id, "ws-3-artisan")))
        artisan_output = get_workstream(track_id, "ws-3-artisan").get("outputs", {})
        
        # Snapshot judge validates the diff
        snapshot_result = self._run_snapshot_judge(track_id, judge_output, artisan_output)
        
        if snapshot_result["valid"]:
            snapshot_valid = True
            break
        
        # Feed correction context back to artisan
        self._update_workstream_inputs(track_id, "ws-3-artisan", {
            "correction_context": snapshot_result["correction_context"],
            "retry_attempt": attempt + 1,
        })
    
    if not snapshot_valid:
        return self._short_circuit_to_scribe(track_id, results, "snapshot_validation_failed")
    
    # Phase 4: Engine + Recovery Loop (post-execution feedback)
    engine_passed = False
    for attempt in range(MAX_LOOP_ITERATIONS):
        engine_result = self._run_workstream("ws-4-engine", get_workstream(track_id, "ws-4-engine"))
        results.append(engine_result)
        
        if engine_result.get("status") == "done":
            engine_passed = True
            break
        
        # Extract error logs and feed back to judge → artisan
        error_context = self._extract_error_context(engine_result)
        self._update_workstream_inputs(track_id, "ws-2-judge", {
            "error_context": error_context,
            "retry_attempt": attempt + 1,
        })
        
        # Re-run judge to classify error, then artisan to fix
        self._run_workstream("ws-2-judge", get_workstream(track_id, "ws-2-judge"))
        self._run_workstream("ws-3-artisan", get_workstream(track_id, "ws-3-artisan"))
    
    if not engine_passed:
        return self._short_circuit_to_scribe(track_id, results, "engine_failed")
    
    # Phase 5: CI Verifier + Recovery Loop
    ci_passed = False
    for attempt in range(MAX_LOOP_ITERATIONS):
        ci_result = self._run_workstream("ws-5-ci_verifier", get_workstream(track_id, "ws-5-ci_verifier"))
        results.append(ci_result)
        
        if ci_result.get("status") == "done" and ci_result.get("passed"):
            ci_passed = True
            break
        
        # Extract CI failure logs and feed back
        ci_error_context = self._extract_ci_error_context(ci_result)
        self._update_workstream_inputs(track_id, "ws-2-judge", {
            "ci_error_context": ci_error_context,
            "retry_attempt": attempt + 1,
        })
        
        # Re-run judge → artisan → engine → ci_verifier
        self._run_workstream("ws-2-judge", get_workstream(track_id, "ws-2-judge"))
        self._run_workstream("ws-3-artisan", get_workstream(track_id, "ws-3-artisan"))
        self._run_workstream("ws-4-engine", get_workstream(track_id, "ws-4-engine"))
    
    if not ci_passed:
        return self._short_circuit_to_scribe(track_id, results, "ci_failed")
    
    # Phase 6: Scribe (format summary, post comment)
    results.append(self._run_workstream("ws-6-scribe", get_workstream(track_id, "ws-6-scribe")))
    
    return {"track": track_id, "results": results, "status": "complete"}


def _run_snapshot_judge(self, track_id: str, judge_output: dict, artisan_output: dict) -> dict:
    """Run snapshot judge to validate artisan's diff against judge's intent."""
    from .snapshot_judge import SnapshotJudge
    
    diff = artisan_output.get("diff", {})
    judge_findings = judge_output.get("findings", {})
    
    sj = SnapshotJudge(judge_findings, diff)
    result = sj.validate()
    
    # Record snapshot judge result in track key_facts
    update_key_facts(track_id, {
        "snapshot_judge": result,
        "snapshot_attempt": get_track(track_id).get("key_facts", {}).get("snapshot_attempt", 0) + 1,
    })
    
    return result


def _extract_error_context(self, engine_result: dict) -> dict:
    """Extract structured error context from engine failure."""
    output = engine_result.get("output", {})
    return {
        "stderr": output.get("stderr", ""),
        "stdout": output.get("stdout", ""),
        "exit_code": output.get("exit_code", -1),
        "command": output.get("command", ""),
    }


def _extract_ci_error_context(self, ci_result: dict) -> dict:
    """Extract structured error context from CI failure."""
    output = ci_result.get("output", {})
    return {
        "failed_checks": output.get("failed", []),
        "fixable_checks": output.get("fixable", []),
        "non_fixable_checks": output.get("non_fixable", []),
    }


def _short_circuit_to_scribe(self, track_id: str, results: list, reason: str) -> dict:
    """When loops exhaust retries, go to scribe with failure context."""
    # Update scribe inputs with failure context
    self._update_workstream_inputs(track_id, "ws-6-scribe", {
        "action": "post_failure_summary",
        "failure_reason": reason,
        "results": results,
    })
    scribe_result = self._run_workstream("ws-6-scribe", get_workstream(track_id, "ws-6-scribe"))
    results.append(scribe_result)
    return {"track": track_id, "results": results, "status": f"failed:{reason}"}
```

### 3. Modified `create_fix_pipeline` — Add Snapshot Judge Workstream

```python
def create_fix_pipeline(
    owner: str,
    repo: str,
    pr_number: int,
    pr_details: dict,
    files: list[dict],
    description: str = '',
    push_eligible: bool = True,
    max_iterations: int = 3,  # NEW: loop cap
) -> dict:
    """Create a 7-workstream fix pipeline with validation loops."""
    track_id = f'riptide-fix-{owner}-{repo}-{pr_number}'
    track = get_track(track_id)
    if not track:
        track = create_track(track_id, name=f'Riptide Fix #{pr_number}', phase='Fix',
                            repos={repo: {'owner': owner, 'pr': pr_number}})
    head_sha = pr_details.get('head', {}).get('sha', '')
    if not head_sha:
        raise ValueError(f"PR #{pr_number} has no head SHA — cannot create fix pipeline")
    
    state_dir = Path.home() / '.local/share/riptide' / 'fix-pipelines' / owner / repo / str(pr_number)
    state_dir.mkdir(parents=True, exist_ok=True)
    
    workstream_ids = [
        'ws-1-probe', 'ws-2-judge', 'ws-3-artisan',
        'ws-4-snapshot-judge',  # NEW: pre-push validation
        'ws-5-engine', 'ws-6-ci-verifier', 'ws-7-scribe',
    ]
    existing = track.get('workstreams', {})
    
    # ... (static maps as before, plus snapshot_judge) ...
    inputs_map = {
        # ... existing ...
        'ws-4-snapshot-judge': {
            'judge_findings_path': str(state_dir / 'findings.json'),
            'diff_path': str(state_dir / 'artisan_diff.json'),
            'max_issues': 0,  # zero tolerance for pre-push
        },
        # ... rest ...
    }
    role_map = {
        # ... existing ...
        'ws-4-snapshot-judge': 'snapshot_judge',
        # ... rest ...
    }
    
    # ... create workstreams ...
```

### 4. Modified `recovery.py` — Add Loop Recovery Actions

```python
class LoopRecovery:
    """Recovery actions specific to pipeline loops."""
    
    @staticmethod
    def snapshot_failure(issues: list[str], attempt: int, max_attempts: int) -> dict:
        """Recovery action for snapshot judge failure."""
        if attempt >= max_attempts:
            return {"action": "escalate", "reason": "max_snapshot_retries_exceeded"}
        return {
            "action": "retry_artisan",
            "correction_context": {"issues": issues},
        }
    
    @staticmethod
    def engine_failure(error_context: dict, attempt: int, max_attempts: int) -> dict:
        """Recovery action for engine test failure."""
        if attempt >= max_attempts:
            return {"action": "escalate", "reason": "max_engine_retries_exceeded"}
        
        # Classify error type
        stderr = error_context.get("stderr", "")
        if "SyntaxError" in stderr or "IndentationError" in stderr:
            error_type = "syntax"
        elif "ImportError" in stderr or "ModuleNotFoundError" in stderr:
            error_type = "import"
        elif "AssertionError" in stderr or "FAILED" in stderr:
            error_type = "test"
        else:
            error_type = "unknown"
        
        return {
            "action": "retry_with_errors",
            "error_type": error_type,
            "error_context": error_context,
        }
    
    @staticmethod
    def ci_failure(ci_context: dict, attempt: int, max_attempts: int) -> dict:
        """Recovery action for CI failure."""
        if attempt >= max_attempts:
            return {"action": "escalate", "reason": "max_ci_retries_exceeded"}
        
        fixable = ci_context.get("fixable_checks", [])
        non_fixable = ci_context.get("non_fixable_checks", [])
        
        if non_fixable:
            return {
                "action": "escalate",
                "reason": "non_fixable_ci_failures",
                "non_fixable": non_fixable,
            }
        
        return {
            "action": "retry_with_ci_errors",
            "failed_checks": ci_context.get("failed_checks", []),
        }
```

### 5. New File: `pipeline/snapshot_judge.py`

```python
#!/usr/bin/env python3
"""snapshot_judge.py — Pre-push validation of artisan's diff against judge's intent."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Optional


class SnapshotJudge:
    """Validates that artisan's diff matches judge's original intent.
    
    Catches errors BEFORE engine runs tests or pushes to remote:
    - Syntax errors in edited files
    - Findings not addressed in diff
    - Placeholder comments left behind
    - Drift from judge's fix plan
    """
    
    def __init__(self, judge_findings: dict, diff: dict, strict: bool = True):
        self.findings = judge_findings
        self.diff = diff
        self.strict = strict
    
    def validate(self) -> dict:
        """Run all validation checks."""
        issues = []
        
        # Check 1: Syntax validation for Python files
        issues.extend(self._check_syntax())
        
        # Check 2: All findings have corresponding changes
        issues.extend(self._check_findings_addressed())
        
        # Check 3: No placeholder patterns
        issues.extend(self._check_no_placeholders())
        
        # Check 4: No obviously broken patterns
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
        """Check for syntax errors in edited Python files."""
        issues = []
        for file_path in self.diff.get("modified_files", []):
            if not file_path.endswith(".py"):
                continue
            try:
                with open(file_path) as f:
                    ast.parse(f.read(), filename=file_path)
            except SyntaxError as e:
                issues.append(f"Syntax error in {file_path}:{e.lineno}: {e.msg}")
        return issues
    
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
        """Check for placeholder comments that indicate incomplete work."""
        issues = []
        placeholder_patterns = ["TODO", "FIXME", "XXX", "HACK", "PLACEHOLDER"]
        
        for hunk in self.diff.get("hunks", []):
            for line in hunk.get("added_lines", []):
                stripped = line.strip()
                for pattern in placeholder_patterns:
                    if pattern in stripped:
                        issues.append(f"Placeholder in {hunk.get('file')}: {stripped}")
        return issues
    
    def _check_broken_patterns(self) -> list[str]:
        """Check for obviously broken code patterns."""
        issues = []
        broken_patterns = [
            ("except:", "bare except clause"),
            ("pass\n", "empty pass block"),
        ]
        
        for hunk in self.diff.get("hunks", []):
            for line in hunk.get("added_lines", []):
                for pattern, description in broken_patterns:
                    if pattern in line:
                        issues.append(f"{description} in {hunk.get('file')}: {line.strip()}")
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
            else:
                suggestions.append(f"Review: {issue}")
        return suggestions
```

## Implementation Order

1. **`pipeline/snapshot_judge.py`** — New file, no dependencies
2. **`pipeline/recovery.py`** — Add `LoopRecovery` class
3. **`pipeline/conductor.py`** — Add loop logic to `_run_fix_pipeline_with_loops`
4. **`pipeline/conductor.py`** — Modify `create_fix_pipeline` to add snapshot judge workstream
5. **`tests/test_pipeline_loops.py`** — Tests for all loop behavior
6. **`tests/test_snapshot_judge.py`** — Tests for snapshot judge

## Testing Strategy

| Test | What it verifies |
|------|-----------------|
| `test_snapshot_judge_catches_syntax_error` | Syntax errors are caught before engine |
| `test_snapshot_judge_catches_unaddressed_findings` | Findings without diff hunks flagged |
| `test_snapshot_judge_catches_placeholders` | TODO/FIXME patterns flagged |
| `test_pre_push_loop_retries_artisan` | Failed snapshot → artisan retry with context |
| `test_pre_push_loop_escalates_after_max` | Max retries → scribe with failure |
| `test_post_execution_loop_retries_on_test_failure` | Engine failure → judge → artisan retry |
| `test_post_execution_loop_escalates_on_max` | Max retries → scribe with failure |
| `test_ci_loop_retries_on_fixable_failures` | CI failure → judge → artisan retry |
| `test_ci_loop_escalates_on_non_fixable` | Non-fixable CI → immediate escalate |
| `test_max_iterations_cap` | Loops never exceed MAX_LOOP_ITERATIONS |

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Infinite loops | Hard cap at `MAX_LOOP_ITERATIONS = 3` |
| Flaky tests | Each loop iteration is idempotent (re-runs from judge) |
| State corruption | Each iteration updates workstream inputs, not track state |
| Overhead | Snapshot judge is cheap (AST parse + string match) |
| False positives | `strict=False` mode for lenient validation |

## Files Changed

| File | Change |
|------|--------|
| `pipeline/snapshot_judge.py` | **NEW** — Pre-push validation worker |
| `pipeline/recovery.py** | Add `LoopRecovery` class |
| `pipeline/conductor.py` | Add `_run_fix_pipeline_with_loops`, modify `create_fix_pipeline` |
| `tests/test_pipeline_loops.py` | **NEW** — Loop behavior tests |
| `tests/test_snapshot_judge.py` | **NEW** — Snapshot judge unit tests |
