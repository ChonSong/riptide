#!/usr/bin/env python3
"""pipeline_loops.py — Loop execution logic for the Riptide fix pipeline.

Implements two feedback loops:
1. Pre-Push Snapshot Loop: validates artisan's diff before running tests
2. Post-Execution Recovery Loop: feeds errors back to judge/artisan for retry

Key design decisions:
- Append-only failure history (prevents trajectory amnesia)
- Log truncation (prevents context window blowout)
- Hard iteration cap (prevents infinite loops)
- AST-based validation (avoids false positives)
"""

from __future__ import annotations

import logging
from typing import Optional

from .work_state import (
    get_track, get_workstream, update_workstream, update_key_facts,
)
from .snapshot_judge import SnapshotJudge, truncate_error_log

log = logging.getLogger("riptide.pipeline.loops")

# Maximum retry attempts per loop
MAX_SNAPSHOT_RETRIES = 3
MAX_ENGINE_RETRIES = 3
MAX_CI_RETRIES = 3


class PipelineLoopRunner:
    """Executes fix pipeline with validation and recovery loops."""
    
    def __init__(self, track_id: str):
        self.track_id = track_id
        self.track = get_track(track_id)
        if not self.track:
            raise ValueError(f"Track {track_id} not found")
    
    def run(self) -> dict:
        """Execute the full fix pipeline with loops."""
        results = []
        
        # Phase 1: Probe (gather context)
        results.append(self._run_workstream("ws-1-probe"))
        
        # Phase 2: Judge (verify findings, produce fix plan)
        results.append(self._run_workstream("ws-2-judge"))
        
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
        results.append(self._run_workstream("ws-6-scribe"))
        
        return {"track": self.track_id, "results": results, "status": "complete"}
    
    def _run_pre_push_loop(self) -> dict:
        """Run artisan + snapshot judge loop until valid or max retries."""
        results = []
        
        for attempt in range(MAX_SNAPSHOT_RETRIES):
            # Run artisan
            artisan_result = self._run_workstream("ws-3-artisan")
            results.append(artisan_result)
            
            # Run snapshot judge
            snapshot_result = self._run_snapshot_judge()
            results.append(snapshot_result)
            
            if snapshot_result.get("valid", False):
                return {"success": True, "results": results}
            
            # Append failure to history (NOT overwrite)
            self._append_failure_history("ws-3-artisan", {
                "attempt": attempt + 1,
                "issues": snapshot_result.get("issues", []),
                "correction_context": snapshot_result.get("correction_context", {}),
            })
        
        return {"success": False, "results": results}
    
    def _run_post_execution_loop(self) -> dict:
        """Run engine + CI with error feedback loop."""
        results = []
        
        for attempt in range(MAX_ENGINE_RETRIES):
            # Run engine (local tests)
            engine_result = self._run_workstream("ws-4-engine")
            results.append(engine_result)
            
            if engine_result.get("status") == "done":
                # Engine passed, now check CI
                ci_result = self._run_ci_loop()
                results.extend(ci_result["results"])
                
                if ci_result["success"]:
                    return {"success": True, "results": results}
                
                # CI failed after max retries
                return {"success": False, "results": results}
            
            # Engine failed — extract error and feed back
            error_context = self._extract_error_context(engine_result)
            
            # Append to failure history
            self._append_failure_history("ws-3-artisan", {
                "attempt": attempt + 1,
                "error_type": "engine",
                "error_context": error_context,
            })
            
            # Re-run judge with error context
            self._update_workstream_inputs("ws-2-judge", {
                "error_context": error_context,
                "failure_history": self._get_failure_history("ws-3-artisan"),
            })
            self._run_workstream("ws-2-judge")
            
            # Re-run artisan with correction context
            self._update_workstream_inputs("ws-3-artisan", {
                "error_context": error_context,
                "failure_history": self._get_failure_history("ws-3-artisan"),
            })
        
        return {"success": False, "results": results}
    
    def _run_ci_loop(self) -> dict:
        """Run CI verifier with error feedback loop."""
        results = []
        
        for attempt in range(MAX_CI_RETRIES):
            ci_result = self._run_workstream("ws-5-ci_verifier")
            results.append(ci_result)
            
            if ci_result.get("passed", False):
                return {"success": True, "results": results}
            
            # CI failed — extract error context
            ci_error_context = self._extract_ci_error_context(ci_result)
            
            # Check for non-fixable failures (short-circuit)
            non_fixable = ci_error_context.get("non_fixable_checks", [])
            if non_fixable:
                log.info(f"Non-fixable CI failures: {non_fixable}")
                return {"success": False, "results": results}
            
            # Append to failure history
            self._append_failure_history("ws-3-artisan", {
                "attempt": attempt + 1,
                "error_type": "ci",
                "error_context": ci_error_context,
            })
            
            # Re-run judge with CI error context
            self._update_workstream_inputs("ws-2-judge", {
                "ci_error_context": ci_error_context,
                "failure_history": self._get_failure_history("ws-3-artisan"),
            })
            self._run_workstream("ws-2-judge")
            
            # Re-run artisan with correction context
            self._update_workstream_inputs("ws-3-artisan", {
                "ci_error_context": ci_error_context,
                "failure_history": self._get_failure_history("ws-3-artisan"),
            })
            self._run_workstream("ws-3-artisan")
            
            # Re-run engine
            self._run_workstream("ws-4-engine")
        
        return {"success": False, "results": results}
    
    def _run_snapshot_judge(self) -> dict:
        """Run snapshot judge to validate artisan's diff."""
        judge_output = get_workstream(self.track_id, "ws-2-judge") or {}
        artisan_output = get_workstream(self.track_id, "ws-3-artisan") or {}
        
        judge_findings = judge_output.get("outputs", {}).get("findings", {})
        diff = artisan_output.get("outputs", {}).get("diff", {})
        
        sj = SnapshotJudge(judge_findings, diff)
        result = sj.validate()
        
        # Record in track key_facts
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
        
        # Classify error type
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
        
        # Truncate error messages
        for check in failed_checks:
            if "message" in check:
                check["message"] = truncate_error_log(check["message"], max_chars=2000)
        
        return {
            "failed_checks": failed_checks,
            "fixable_checks": fixable_checks,
            "non_fixable_checks": non_fixable_checks,
        }
    
    def _append_failure_history(self, ws_id: str, entry: dict) -> None:
        """Append a failure entry to the workstream's failure history.
        
        This is append-only to prevent trajectory amnesia — the artisan
        remembers all past attempts and their failures.
        """
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
        """Run a single workstream (placeholder — actual dispatch in conductor)."""
        # This is a placeholder — actual implementation dispatches to conductor
        ws = get_workstream(self.track_id, ws_id)
        if not ws:
            return {"workstream": ws_id, "status": "not_found"}
        return {"workstream": ws_id, "status": "dispatched"}
    
    def _short_circuit_to_scribe(self, results: list, reason: str) -> dict:
        """When loops exhaust retries, go to scribe with failure context."""
        scribe_ws = get_workstream(self.track_id, "ws-6-scribe") or {}
        inputs = scribe_ws.get("inputs", {})
        inputs["action"] = "post_failure_summary"
        inputs["failure_reason"] = reason
        inputs["results"] = results
        update_workstream(self.track_id, "ws-6-scribe", inputs=inputs)
        
        scribe_result = self._run_workstream("ws-6-scribe")
        results.append(scribe_result)
        
        return {"track": self.track_id, "results": results, "status": f"failed:{reason}"}
