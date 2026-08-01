# riptide/orchestrator.py
"""
T0 Orchestrator: classify PR tasks, dispatch to tiers, validate results.

Modes:
  "parallel" — dispatch T1 + T3 simultaneously (production, fast)
  "serial"   — dispatch tier-by-tier, verify before escalating (verification)
"""

import os
import time
import sqlite3
import threading
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout


# ── Task Classification ──────────────────────────────────────────────────────

@dataclass
class TaskProfile:
    """Classified PR review task."""
    pr_number: int
    owner: str
    repo: str
    title: str
    files: list
    ui_files: list
    total_loc: int
    
    @property
    def needs_t1(self) -> bool:
        """Multi-file analysis needed."""
        return len(self.files) >= 3 or self.total_loc > 200
    
    @property
    def needs_t3_visual(self) -> bool:
        """UI/UX evidence needed."""
        return len(self.ui_files) > 0
    
    @property
    def needs_t3_arch(self) -> bool:
        """Architecture diagram needed."""
        arch_patterns = ("server.", "webhook.", "orchestrator.", "deepthink.", "companion.")
        return any(
            any(p in f.get("filename", "") for p in arch_patterns)
            for f in self.files
        )


class TaskClassifier:
    """Classify PR tasks for tier dispatch."""
    
    def __init__(self):
        self.ui_extensions = {
            ".tsx", ".jsx", ".vue", ".css", ".scss", ".html", ".svg",
            ".less", ".sass",
        }
    
    def classify(self, pr_number, owner, repo, title, files, total_loc) -> TaskProfile:
        ui_files = self._detect_ui_files(files)
        return TaskProfile(
            pr_number=pr_number,
            owner=owner,
            repo=repo,
            title=title,
            files=files,
            ui_files=ui_files,
            total_loc=total_loc,
        )
    
    def _detect_ui_files(self, files):
        return [
            f for f in files
            if Path(f.get("filename", "")).suffix in self.ui_extensions
        ]


# ── Result Validation ────────────────────────────────────────────────────────

@dataclass
class ValidationReport:
    valid: bool
    confidence: float
    issues: list


class ResultValidator:
    """Validate subagent results before T0 uses them."""
    
    def validate(self, result: Optional[dict] = None) -> ValidationReport:
        if not result:
            return ValidationReport(valid=False, confidence=0.0, issues=["Empty result"])
        
        issues = []
        confidence = 1.0
        
        # Source-check: does it cite actual code?
        if not result.get("cited_files"):
            issues.append("No source citations found")
            confidence *= 0.7
        
        # Coherence: does it make sense?
        body = result.get("body", "")
        if not body or len(body) < 20:
            issues.append("Output suspiciously short or empty")
            confidence *= 0.5
        
        # Completeness: did it answer the full question?
        if result.get("truncated"):
            issues.append("Output was truncated")
            confidence *= 0.8
        
        return ValidationReport(
            valid=len(issues) == 0 or confidence >= 0.7,
            confidence=confidence,
            issues=issues,
        )


# ── State Store ──────────────────────────────────────────────────────────────

class StateStore:
    """SQLite-backed state for tracking parallel job completion and dedup."""
    
    def __init__(self, db_path: str = "/tmp/riptide_state.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()
    
    @property
    def _conn(self):
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path)
        return self._local.conn
    
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                pr_number INTEGER,
                tier TEXT,
                status TEXT,
                created_at REAL,
                completed_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS deliveries (
                delivery_id TEXT PRIMARY KEY,
                received_at REAL
            )
        """)
        conn.commit()
        conn.close()
    
    def reserve_delivery(self, delivery_id: str) -> bool:
        """Try to reserve a delivery ID. Returns False if already processed."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO deliveries (delivery_id, received_at) VALUES (?, ?)",
                (delivery_id, time.time()),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()
    
    def create_job(self, job_id: str, pr_number: int, tier: str):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO jobs (id, pr_number, tier, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                (job_id, pr_number, tier, time.time()),
            )
            conn.commit()
        finally:
            conn.close()
    
    def mark_complete(self, job_id: str):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE jobs SET status = 'complete', completed_at = ? WHERE id = ?",
                (time.time(), job_id),
            )
            conn.commit()
        finally:
            conn.close()
    
    def mark_failed(self, job_id: str):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE jobs SET status = 'failed', completed_at = ? WHERE id = ?",
                (time.time(), job_id),
            )
            conn.commit()
        finally:
            conn.close()
    
    def pending_jobs(self, pr_number: int) -> list:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT id, tier FROM jobs WHERE pr_number = ? AND status = 'pending'",
                (pr_number,),
            )
            return [{"id": row[0], "tier": row[1]} for row in cursor.fetchall()]
        finally:
            conn.close()
    
    def wait_for_all(self, pr_number: int, timeout: int = 180) -> bool:
        """Wait for all dispatched tiers to complete. Returns False on timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            pending = self.pending_jobs(pr_number)
            if not pending:
                return True
            time.sleep(2)
        return False
    
    def get_results(self, pr_number: int) -> dict:
        """Get all completed job results for a PR."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT tier, status FROM jobs WHERE pr_number = ? AND status IN ('complete', 'failed')",
                (pr_number,),
            )
            results = {}
            for row in cursor.fetchall():
                tier, status = row
                results[tier] = {"status": status}
            return results
        finally:
            conn.close()


# ── T0 Orchestrator ─────────────────────────────────────────────────────────

class T0Orchestrator:
    """
    PR review orchestrator with two modes.
    
    Mode A (parallel): Dispatch T1 + T3 simultaneously, wait for both.
    Mode B (serial):   Dispatch tier-by-tier, verify before escalating.
    """
    
    def __init__(self, mode: str = "parallel", state_store: Optional[StateStore] = None):
        self.mode = mode  # "parallel" or "serial"
        self.classifier = TaskClassifier()
        self.validator = ResultValidator()
        self.state = state_store or StateStore()
        self.t3_timeout = 180  # 3 minutes max for visual evidence
    
    def review_pr(self, pr_number, owner, repo, title, files, total_loc) -> dict:
        """Main entry: classify, dispatch, validate, synthesize."""
        profile = self.classifier.classify(pr_number, owner, repo, title, files, total_loc)
        
        if self.mode == "parallel":
            return self._parallel_review(profile)
        else:
            return self._serial_review(profile)
    
    def _parallel_review(self, profile: TaskProfile) -> dict:
        """Dispatch T1 + T3 simultaneously, wait for both."""
        results = {}
        
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_tier = {}
            
            if profile.needs_t1:
                job_id = f"{profile.pr_number}-t1"
                self.state.create_job(job_id, profile.pr_number, "t1")
                future = executor.submit(self._dispatch_t1, profile)
                future_to_tier[future] = ("t1", job_id)
            
            if profile.needs_t3_visual:
                job_id = f"{profile.pr_number}-t3-visual"
                self.state.create_job(job_id, profile.pr_number, "t3_visual")
                future = executor.submit(self._dispatch_t3_visual, profile)
                future_to_tier[future] = ("t3_visual", job_id)
            
            if profile.needs_t3_arch:
                job_id = f"{profile.pr_number}-t3-arch"
                self.state.create_job(job_id, profile.pr_number, "t3_arch")
                future = executor.submit(self._dispatch_t3_arch, profile)
                future_to_tier[future] = ("t3_arch", job_id)
            
            # Wait with timeout
            for future, (tier, job_id) in future_to_tier.items():
                try:
                    result = future.result(timeout=self.t3_timeout)
                    results[tier] = result
                    self.state.mark_complete(job_id)
                except FuturesTimeout:
                    results[tier] = {
                        "status": "timeout",
                        "body": f"⏰ {tier} timed out after {self.t3_timeout}s — results partial",
                    }
                    self.state.mark_failed(job_id)
                except Exception as e:
                    results[tier] = {
                        "status": "error",
                        "body": f"❌ {tier} failed: {str(e)}",
                    }
                    self.state.mark_failed(job_id)
        
        # Validate each result
        validated = {}
        for name, result in results.items():
            report = self.validator.validate(result)
            validated[name] = {
                "result": result,
                "valid": report.valid,
                "confidence": report.confidence,
                "issues": report.issues,
            }
        
        return self._synthesize(validated, profile)
    
    def _serial_review(self, profile: TaskProfile) -> dict:
        """Dispatch tier-by-tier, verify before escalating."""
        results = {}
        
        # T2 first (fast, cheap) — only for small PRs
        if profile.total_loc < 100 and len(profile.files) <= 3:
            t2_result = self._dispatch_t2(profile)
            report = self.validator.validate(t2_result)
            if report.confidence >= 0.8:
                results["t2"] = t2_result
                return self._synthesize(results, profile)
        
        # T1 next (multi-file analysis)
        if profile.needs_t1:
            t1_result = self._dispatch_t1(profile)
            report = self.validator.validate(t1_result)
            results["t1"] = t1_result
            
            if report.confidence >= 0.7:
                return self._synthesize(results, profile)
        
        # T3 visual (most expensive)
        if profile.needs_t3_visual:
            try:
                results["t3_visual"] = self._dispatch_t3_visual(profile)
            except Exception:
                results["t3_visual"] = {
                    "status": "error",
                    "body": "⏰ Visual evidence failed",
                }
        
        # T3 arch (if architecture changed)
        if profile.needs_t3_arch:
            try:
                results["t3_arch"] = self._dispatch_t3_arch(profile)
            except Exception:
                results["t3_arch"] = {
                    "status": "error",
                    "body": "⏰ Architecture diagram failed",
                }
        
        return self._synthesize(results, profile)
    
    def _dispatch_t1(self, profile: TaskProfile) -> dict:
        """Dispatch to T1 (v4-flash via Hermes cron)."""
        from riptide.deepthink import _spawn_deepthink
        _spawn_deepthink(
            profile.owner, profile.repo, profile.pr_number,
            profile.title, "unknown", profile.total_loc,
            head_sha=""  # optional for dispatch
        )
        return {"status": "dispatched", "tier": "t1", "body": "T1 review dispatched"}
    
    def _dispatch_t3_visual(self, profile: TaskProfile) -> dict:
        """Dispatch to T3 (proofshot)."""
        # Calls proofshotter.py
        return {"status": "dispatched", "tier": "t3_visual", "body": "Visual capture dispatched"}
    
    def _dispatch_t3_arch(self, profile: TaskProfile) -> dict:
        """Dispatch to T3 (excalidraw)."""
        return {"status": "dispatched", "tier": "t3_arch", "body": "Architecture diagram dispatched"}
    
    def _dispatch_t2(self, profile: TaskProfile) -> dict:
        """Dispatch to T2 (Ollama direct)."""
        from riptide.companion import Companion
        c = Companion(github_client=None)
        body = c._generate_tldr(profile.title, "unknown", profile.files, None)
        return {"status": "complete", "tier": "t2", "body": body or "", "cited_files": []}
    
    def _synthesize(self, results: dict, profile: TaskProfile) -> dict:
        """Aggregate all tier results into unified comment."""
        parts = []
        
        if "t1" in results:
            parts.append(f"### Deep Review\n{results['t1'].get('body', '')}")
        
        if "t2" in results:
            parts.append(f"### Quick Summary\n{results['t2'].get('body', '')}")
        
        if "t3_visual" in results:
            parts.append(f"### Visual Evidence\n{results['t3_visual'].get('body', '')}")
        
        if "t3_arch" in results:
            parts.append(f"### Architecture\n{results['t3_arch'].get('body', '')}")
        
        # Next steps for author
        parts.append("### Next Steps for Author\n")
        if "t1" in results and results["t1"].get("findings"):
            parts.append("1. Address findings from Deep Review")
        if "t3_visual" in results:
            parts.append("2. Verify visual evidence matches expected behavior")
        parts.append("3. Request re-review after changes")
        
        return {
            "status": "complete",
            "pr_number": profile.pr_number,
            "body": "\n\n".join(parts),
            "tiers_used": list(results.keys()),
        }
