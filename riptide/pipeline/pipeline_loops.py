#!/usr/bin/env python3
"""pipeline_loops.py — Loop execution logic for the Riptide fix pipeline.

Implements two feedback loops:
1. Pre-Push Snapshot Loop: validates artisan's diff before running tests
2. Post-Execution Recovery Loop: feeds errors back to judge/artisan for retry
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .work_state import (
    get_track, get_workstream, update_workstream, update_key_facts,
)
from .snapshot_judge import SnapshotJudge, truncate_error_log

log = logging.getLogger("riptide.pipeline.loops")


class PipelineLoopRunner:
    """Executes fix pipeline with validation and recovery loops."""
    
    # Stage IDs for the 7-workstream pipeline
    WS_PROBE = "ws-1-probe"
    WS_JUDGE = "ws-2-judge"
    WS_ARTISAN = "ws-3-artisan"
    WS_SNAPSHOT_JUDGE = "ws-4-snapshot-judge"
    WS_ENGINE = "ws-5-engine"
    WS_CI_VERIFIER = "ws-6-ci-verifier"
    WS_SCRIBE = "ws-7-scribe"
    
    DEFAULT_MAX_ITERATIONS = 3
    
    def __init__(self, track_id: str):
        self.track_id = track_id
        self.track = get_track(track_id)
        if not self.track:
            raise ValueError(f"Track {track_id} not found")
        self.max_iterations = self._get_max_iterations()
    
    def _get_max_iterations(self) -> int:
        """Get max_iterations from pipeline config (default 3)."""
        probe_ws = get_workstream(self.track_id, self.WS_PROBE)
        if probe_ws and probe_ws.get("inputs", {}).get("max_iterations"):
            return probe_ws["inputs"]["max_iterations"]
        return self.DEFAULT_MAX_ITERATIONS
    
    def run(self) -> dict:
        """Execute the full fix pipeline with loops."""
        results = []
        
        # Phase 1: Probe (gather context)
        results.append(self._run_workstream(self.WS_PROBE))
        
        # Phase 2: Judge (verify findings, produce fix plan)
        results.append(self._run_workstream(self.WS_JUDGE))
        
        # Phase 3: Pre-Push Snapshot Loop (artisan + validation)
        snapshot_result = self._run_pre_push_loop()
        results.extend(snapshot_result["results"])
        
        if not snapshot_result["success"]:
            return self._short_circuit_to_scribe(results, "snapshot_validation_failed")
        
        # Phase 4: Post-Execution Recovery Loop (engine + CI)
        execution_result = self._run_post_execution_loop()
        results.extend(execution_result["results"])
        
        if not execution_result["success"]:
            return self._short_circuit_to_scribe(results, "execution_failed")
        
        # Phase 5: Scribe (format summary, post comment)
        results.append(self._run_workstream(self.WS_SCRIBE))
        
        return {"track": self.track_id, "results": results, "status": "complete"}
    
    def _run_pre_push_loop(self) -> dict:
        """Run artisan + snapshot judge loop until valid or max retries."""
        results = []
        
        for attempt in range(self.max_iterations):
            artisan_result = self._run_workstream(self.WS_ARTISAN)
            results.append(artisan_result)
            
            snapshot_result = self._run_snapshot_judge()
            results.append(snapshot_result)
            
            if snapshot_result.get("valid", False):
                return {"success": True, "results": results}
            
            self._append_failure_history(self.WS_ARTISAN, {
                "attempt": attempt + 1,
                "issues": snapshot_result.get("issues", []),
                "correction_context": snapshot_result.get("correction_context", {}),
            })
        
        return {"success": False, "results": results}
    
    def _run_post_execution_loop(self) -> dict:
        """Run engine + CI with error feedback loop."""
        results = []
        
        for attempt in range(self.max_iterations):
            engine_result = self._run_workstream(self.WS_ENGINE)
            results.append(engine_result)
            
            if engine_result.get("status") == "done":
                ci_result = self._run_ci_loop()
                results.extend(ci_result["results"])
                
                if ci_result["success"]:
                    return {"success": True, "results": results}
                return {"success": False, "results": results}
            
            error_context = self._extract_error_context(engine_result)
            self._append_failure_history(self.WS_ARTISAN, {
                "attempt": attempt + 1,
                "error_type": "engine",
                "error_context": error_context,
            })
            
            self._update_workstream_inputs(self.WS_JUDGE, {
                "error_context": error_context,
                "failure_history": self._get_failure_history(self.WS_ARTISAN),
            })
            self._run_workstream(self.WS_JUDGE)
            self._update_workstream_inputs(self.WS_ARTISAN, {
                "error_context": error_context,
                "failure_history": self._get_failure_history(self.WS_ARTISAN),
            })
        
        return {"success": False, "results": results}
    
    def _run_ci_loop(self) -> dict:
        """Run CI verifier with error feedback loop."""
        results = []
        
        for attempt in range(self.max_iterations):
            ci_result = self._run_workstream(self.WS_CI_VERIFIER)
            results.append(ci_result)
            
            if ci_result.get("passed", False):
                return {"success": True, "results": results}
            
            ci_error_context = self._extract_ci_error_context(ci_result)
            non_fixable = ci_error_context.get("non_fixable_checks", [])
            if non_fixable:
                log.info(f"Non-fixable CI failures: {non_fixable}")
                return {"success": False, "results": results}
            
            self._append_failure_history(self.WS_ARTISAN, {
                "attempt": attempt + 1,
                "error_type": "ci",
                "error_context": ci_error_context,
            })
            
            self._update_workstream_inputs(self.WS_JUDGE, {
                "ci_error_context": ci_error_context,
                "failure_history": self._get_failure_history(self.WS_ARTISAN),
            })
            self._run_workstream(self.WS_JUDGE)
            self._update_workstream_inputs(self.WS_ARTISAN, {
                "ci_error_context": ci_error_context,
                "failure_history": self._get_failure_history(self.WS_ARTISAN),
            })
            self._run_workstream(self.WS_ARTISAN)
            self._run_workstream(self.WS_ENGINE)
        
        return {"success": False, "results": results}
    
    def _run_snapshot_judge(self) -> dict:
        """Run snapshot judge — reads from staged file paths."""
        judge_ws = get_workstream(self.track_id, self.WS_JUDGE) or {}
        artisan_ws = get_workstream(self.track_id, self.WS_ARTISAN) or {}
        
        # Read from file paths that the workers wrote to
        judge_findings_path = judge_ws.get("inputs", {}).get("findings_path", "")
        diff_path = artisan_ws.get("inputs", {}).get("diff_path", "")
        
        judge_findings = {}
        if judge_findings_path and Path(judge_findings_path).exists():
            with open(judge_findings_path) as f:
                judge_findings = json.load(f)
        
        diff = {}
        if diff_path and Path(diff_path).exists():
            with open(diff_path) as f:
                diff = json.load(f)
        
        sj = SnapshotJudge(judge_findings, diff)
        result = sj.validate()
        
        update_key_facts(self.track_id, {
            "snapshot_judge": result,
            "snapshot_attempt": self.track.get("key_facts", {}).get("snapshot_attempt", 0) + 1,
        })
        
        return result
    
    def _extract_error_context(self, engine_result: dict) -> dict:
        """Extract truncated error context from engine failure."""
        output = engine_result.get("output", {})
        stderr = truncate_error_log(output.get("stderr", ""))
        stdout = truncate_error_log(output.get("stdout", ""))
        
        error_type = "unknown"
        combined = stderr + stdout
        if "SyntaxError" in combined or "IndentationError" in combined:
            error_type = "syntax"
        elif "ImportError" in combined or "ModuleNotFoundError" in combined:
            error_type = "import"
        elif "AssertionError" in combined or "FAILED" in combined:
            error_type = "test"
        elif "Timeout" in combined or "TIMEOUT" in combined:
            error_type = "timeout"
        
        return {
            "stderr": stderr,
            "stdout": stdout,
            "exit_code": output.get("exit_code", -1),
            "command": output.get("command", ""),
            "error_type": error_type,
        }
    
    def _extract_ci_error_context(self, ci_result: dict) -> dict:
        """Extract truncated CI error context."""
        output = ci_result.get("output", {})
        failed_checks = output.get("failed", [])
        fixable_checks = output.get("fixable", [])
        non_fixable_checks = output.get("non_fixable", [])
        
        for check in failed_checks:
            if "message" in check:
                check["message"] = truncate_error_log(check["message"], max_chars=2000)
        
        return {
            "failed_checks": failed_checks,
            "fixable_checks": fixable_checks,
            "non_fixable_checks": non_fixable_checks,
        }
    
    def _append_failure_history(self, ws_id: str, entry: dict) -> None:
        """Append a failure entry to the workstream's failure history."""
        ws = get_workstream(self.track_id, ws_id) or {}
        inputs = ws.get("inputs", {})
        history = inputs.get("failure_history", [])
        history.append(entry)
        inputs["failure_history"] = history
        update_workstream(self.track_id, ws_id, inputs=inputs)
    
    def _get_failure_history(self, ws_id: str) -> list[dict]:
        """Get the full failure history for a workstream."""
        ws = get_workstream(self.track_id, ws_id) or {}
        return ws.get("inputs", {}).get("failure_history", [])
    
    def _update_workstream_inputs(self, ws_id: str, updates: dict) -> None:
        """Update workstream inputs with new context."""
        ws = get_workstream(self.track_id, ws_id) or {}
        inputs = ws.get("inputs", {})
        inputs.update(updates)
        update_workstream(self.track_id, ws_id, inputs=inputs)
    
    def _run_workstream(self, ws_id: str) -> dict:
        """Run a single workstream by dispatching to the appropriate worker."""
        ws = get_workstream(self.track_id, ws_id)
        if not ws:
            return {"workstream": ws_id, "status": "not_found"}
        
        update_workstream(self.track_id, ws_id, status="in_progress")
        
        try:
            role = ws.get("role", "engine")
            output = self._dispatch_worker(role, ws)
            update_workstream(self.track_id, ws_id, status="done", outputs=output)
            return {"workstream": ws_id, "status": "done", "output": output}
        except Exception as e:
            update_workstream(self.track_id, ws_id, status="failed")
            return {"workstream": ws_id, "status": "failed", "error": str(e)}
    
    def _dispatch_worker(self, role: str, ws: dict) -> dict:
        """Dispatch to the appropriate worker based on role."""
        inputs = ws.get("inputs", {})
        
        if role == "probe":
            return self._execute_probe(inputs)
        elif role == "judge":
            return self._execute_judge(inputs)
        elif role == "artisan":
            return self._execute_artisan(inputs)
        elif role == "snapshot_judge":
            return self._execute_snapshot_judge(inputs)
        elif role == "engine":
            return self._execute_engine(inputs)
        elif role == "ci_verifier":
            return self._execute_ci_verifier(inputs)
        elif role == "scribe":
            return self._execute_scribe(inputs)
        else:
            raise ValueError(f"Unknown role: {role}")
    
    def _execute_probe(self, inputs: dict) -> dict:
        """Execute probe worker — gather context."""
        from .probe import Probe
        
        pr = inputs.get("pr_number", 1)
        owner = inputs.get("owner", "ChonSong")
        repo = inputs.get("repo", "riptide")
        
        probe = Probe(pr, owner, repo)
        context = probe.gather()
        
        # Write to output_path -> this becomes context_path for judge
        output_path = inputs.get("output_path", "/tmp/output.json")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(context, f, indent=2, default=str)
        
        return {"context_path": output_path, "gathered": True}
    
    def _execute_judge(self, inputs: dict) -> dict:
        """Execute judge worker — evaluate diff, produce findings."""
        from .judge import Judge
        
        # Read context from probe's output
        context_path = inputs.get("context_path", "/tmp/output.json")
        with open(context_path) as f:
            context = json.load(f)
        
        judge = Judge(context)
        result = judge.evaluate()
        
        # Write findings to findings_path for artisan and snapshot judge
        findings_path = inputs.get("findings_path", "/tmp/findings.json")
        Path(findings_path).parent.mkdir(parents=True, exist_ok=True)
        with open(findings_path, 'w') as f:
            json.dump(result, f, indent=2)
        
        return {"findings_path": findings_path, "findings_count": len(result.get("findings", []))}
    
    def _execute_artisan(self, inputs: dict) -> dict:
        """Execute artisan worker — edit files, apply fixes."""
        from .artisan import Artisan
        
        artisan = Artisan()
        files = inputs.get("files", [])
        created = []
        for file_spec in files:
            result = artisan.create_file(file_spec["path"], file_spec["content"])
            created.append(result)
        
        return {"created": created}
    
    def _execute_snapshot_judge(self, inputs: dict) -> dict:
        """Execute snapshot judge — validate diff against intent."""
        from .snapshot_judge import SnapshotJudge
        
        judge_findings = inputs.get("judge_findings", {})
        diff = inputs.get("diff", {})
        
        sj = SnapshotJudge(judge_findings, diff)
        result = sj.validate()
        
        return {"snapshot_result": result, "valid": result["valid"]}
    
    def _execute_engine(self, inputs: dict) -> dict:
        """Execute engine worker — run tests."""
        from .engine import Engine
        
        engine = Engine()
        command = inputs.get("command", "")
        result = engine.run(command, expected_exit=inputs.get("expected_exit", 0))
        
        return result
    
    def _execute_ci_verifier(self, inputs: dict) -> dict:
        """Execute CI verifier worker — poll CI checks."""
        from .ci_verifier import CIVerifier
        
        owner = inputs.get("owner", "ChonSong")
        repo = inputs.get("repo", "riptide")
        pr_number = inputs.get("pr_number", 0)
        timeout = inputs.get("timeout", 600)
        
        verifier = CIVerifier(owner, repo, pr_number)
        result = verifier.poll(timeout=timeout)
        
        return {
            "status": result.get("status", "unknown"),
            "passed": result.get("status") == "success",
            "failed_count": len(result.get("failed", [])),
            "fixable_count": len(result.get("fixable", [])),
            "non_fixable_count": len(result.get("non_fixable", [])),
        }
    
    def _execute_scribe(self, inputs: dict) -> dict:
        """Execute scribe worker — format summary, post comment."""
        from .scribe import Scribe
        
        scribe = Scribe()
        action = inputs.get("action", "update_workstream")
        
        if action == "update_workstream":
            return scribe.update_workstream(
                self.track_id,
                inputs.get("workstream", ""),
                inputs.get("status", "done"),
                inputs.get("outputs"),
            )
        elif action == "post_review":
            return scribe.post_review_with_assembler(
                inputs.get("owner", "ChonSong"),
                inputs.get("repo", "riptide"),
                inputs.get("pr_number", 0),
                inputs.get("findings", []),
                inputs.get("diagram_url"),
            )
        elif action == "post_fix_summary":
            return scribe.post_pr_comment(
                inputs.get("owner", "ChonSong"),
                inputs.get("repo", "riptide"),
                inputs.get("pr_number", 0),
                inputs.get("summary", "Fix attempt completed"),
            )
        
        return {"error": f"Unknown scribe action: {action}"}
    
    def _short_circuit_to_scribe(self, results: list, reason: str) -> dict:
        """When loops exhaust retries, go to scribe with failure context."""
        scribe_ws = get_workstream(self.track_id, self.WS_SCRIBE) or {}
        inputs = scribe_ws.get("inputs", {})
        inputs["action"] = "post_failure_summary"
        inputs["failure_reason"] = reason
        inputs["results"] = results
        update_workstream(self.track_id, self.WS_SCRIBE, inputs=inputs)
        
        scribe_result = self._run_workstream(self.WS_SCRIBE)
        results.append(scribe_result)
        
        return {"track": self.track_id, "results": results, "status": f"failed:{reason}"}
