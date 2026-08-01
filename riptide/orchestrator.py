# riptide/orchestrator.py
"""
T0 Orchestrator: classify PR tasks, dispatch to tiers, validate results.

Modes:
  "parallel" — dispatch T1 + T3 simultaneously (production, fast)
  "serial"   — dispatch tier-by-tier, verify before escalating (verification)

Architecture:
  T0 (orchestrator) classifies PR → dispatches to T1/T2/T3 tiers
  T1 = deepthink (Hermes cron, multi-file analysis) — on-demand via _spawn_deepthink
  T2 = companion quick summary (TL;DR for small PRs)
  T3 visual = proofshot (UI evidence capture)
  T3 arch = excalidraw (architecture diagram)
"""

import os
import time
import sqlite3
import threading
import logging
import subprocess
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

log = logging.getLogger("riptide.orchestrator")


# ── Task Classification ──────────────────────────────────────────────────────

@dataclass
class TaskProfile:
    """Classified PR review task."""
    pr_number: int
    owner: str
    repo: str
    title: str
    author: str
    files: list
    ui_files: list
    total_loc: int
    installation_id: int = 0
    head_sha: str = ""
    
    @property
    def needs_t1(self) -> bool:
        """Multi-file analysis needed (deepthink)."""
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
    
    def classify(self, pr_number, owner, repo, title, author, files, total_loc, installation_id=0, head_sha="") -> TaskProfile:
        ui_files = self._detect_ui_files(files)
        return TaskProfile(
            pr_number=pr_number,
            owner=owner,
            repo=repo,
            title=title,
            author=author,
            files=files,
            ui_files=ui_files,
            total_loc=total_loc,
            installation_id=installation_id,
            head_sha=head_sha,
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
                "UPDATE jobs SET status='complete', completed_at=? WHERE id=?",
                (time.time(), job_id),
            )
            conn.commit()
        finally:
            conn.close()
    
    def mark_failed(self, job_id: str):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE jobs SET status='failed', completed_at=? WHERE id=?",
                (time.time(), job_id),
            )
            conn.commit()
        finally:
            conn.close()


# ── T0 Orchestrator ──────────────────────────────────────────────────────────

class T0Orchestrator:
    """
    Top-level dispatcher. Classifies PR and dispatches to review tiers.
    
    Usage:
        orch = T0Orchestrator(companion=companion, github_client=github_client())
        result = orch.review_pr(profile, mode="parallel")
    """
    
    def __init__(self, companion=None, github_client=None, t3_timeout=180, state_store=None):
        self.companion = companion
        self.github = github_client
        self.t3_timeout = t3_timeout
        self.state = state_store or StateStore()
        self.classifier = TaskClassifier()
        self.validator = ResultValidator()
    
    def review_pr(self, profile: TaskProfile, mode: str = "parallel") -> dict:
        """
        Classify and review a PR.
        
        Args:
            profile: classified task profile
            mode: "parallel" (production) or "serial" (verification)
        
        Returns:
            dict with unified review result
        """
        log.info(f"T0 reviewing {profile.owner}/{profile.repo}#{profile.pr_number} (mode={mode})")
        
        # T2 first — always generate TL;DR (quick, cheap)
        t2_result = self._dispatch_t2(profile)
        
        if mode == "parallel":
            results = self._parallel_review(profile, t2_result)
        else:
            results = self._serial_review(profile, t2_result)
        
        # Post unified comment
        unified = self._synthesize(results, profile)
        self._post_comment(profile, unified)
        
        return unified
    
    def _parallel_review(self, profile: TaskProfile, t2_result: dict) -> dict:
        """Dispatch T1 + T3 simultaneously (production)."""
        results = {"t2": t2_result}
        
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
        
        return results
    
    def _serial_review(self, profile: TaskProfile, t2_result: dict) -> dict:
        """Dispatch tier-by-tier, verify before escalating (verification)."""
        results = {"t2": t2_result}
        
        # T1 next (multi-file analysis)
        if profile.needs_t1:
            t1_result = self._dispatch_t1(profile)
            report = self.validator.validate(t1_result)
            results["t1"] = t1_result
            
            if report.confidence >= 0.7:
                return results
        
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
        
        return results
    
    def _dispatch_t2(self, profile: TaskProfile) -> dict:
        """T2: Quick TL;DR via companion (cheap, always runs)."""
        if not self.companion:
            return {"status": "skipped", "tier": "t2", "body": ""}
        
        try:
            emoji = self.companion.classify_pr_mood(profile.title, profile.files)
            gif_url = self.companion.select_gif(emoji, profile.title, profile.files)
            graph_context = self.companion._get_graph_context(profile.files)
            
            # Generate TL;DR (non-blocking, local Ollama call)
            tldr = self.companion._generate_tldr(
                profile.title, profile.author, profile.files, graph_context
            )
            
            return {
                "status": "complete",
                "tier": "t2",
                "emoji": emoji,
                "gif_url": gif_url,
                "tldr": tldr,
                "graph_context": graph_context,
                "body": tldr or "",
            }
        except Exception as e:
            log.warning(f"T2 generation failed: {e}")
            return {"status": "error", "tier": "t2", "body": "", "error": str(e)}
    
    def _dispatch_t1(self, profile: TaskProfile) -> dict:
        """Dispatch to T1 (deepthink via Hermes cron)."""
        try:
            from riptide.deepthink import _spawn_deepthink
            result = _spawn_deepthink(
                profile.owner, profile.repo, profile.pr_number,
                profile.title, profile.author, profile.total_loc,
                head_sha=profile.head_sha,
            )
            return {
                "status": "dispatched" if result else "failed",
                "tier": "t1",
                "body": "Deep review dispatched" if result else "Deep review dispatch failed",
                "spawned": result,
            }
        except Exception as e:
            log.warning(f"T1 dispatch failed: {e}")
            return {"status": "error", "tier": "t1", "body": f"Dispatch failed: {str(e)}"}
    
    def _dispatch_t3_visual(self, profile: TaskProfile) -> dict:
        """Dispatch to T3 (proofshot visual capture)."""
        try:
            from riptide.proofshotter import _checkout_pr, _run_proofshot_default, _upload_gif, _post_proofshot_comment
            
            work_dir = _checkout_pr(profile.owner, profile.repo, profile.pr_number)
            if not work_dir:
                return {"status": "error", "tier": "t3_visual", "body": "Checkout failed"}
            
            result = _run_proofshot_default(
                profile.pr_number,
                url="http://localhost:8788",
                seed_path=None,
                output_dir=Path(f"/tmp/proofshot-pr-{profile.owner}-{profile.repo}-{profile.pr_number}"),
            )
            if not result:
                return {"status": "error", "tier": "t3_visual", "body": "Capture failed"}
            
            gif_path = result.get("gif", "")
            screenshots = result.get("screenshots", [])
            gif_url = _upload_gif(gif_path, profile.pr_number)
            if gif_url and self.github:
                _post_proofshot_comment(
                    profile.owner, profile.repo, profile.pr_number,
                    gif_url, screenshots=screenshots
                )
            
            return {
                "status": "complete",
                "tier": "t3_visual",
                "body": f"![ProofShot]({gif_url})" if gif_url else "Visual capture complete",
                "gif_url": gif_url,
            }
        except Exception as e:
            log.warning(f"T3 visual dispatch failed: {e}")
            return {"status": "error", "tier": "t3_visual", "body": f"Visual capture failed: {str(e)}"}
    
    def _dispatch_t3_arch(self, profile: TaskProfile) -> dict:
        """Dispatch to T3 (excalidraw architecture diagram)."""
        # Architecture diagram via deepthink's excalidraw skill
        return {"status": "dispatched", "tier": "t3_arch", "body": "Architecture diagram dispatched"}
    
    def _synthesize(self, results: dict, profile: TaskProfile) -> dict:
        """Aggregate all tier results into unified comment."""
        parts = []
        t2 = results.get("t2", {})
        
        # TL;DR header
        emoji = t2.get("emoji", "✨")
        tldr = t2.get("tldr", "")
        author = profile.author
        
        if tldr:
            parts.append(f"## {emoji} TL;DR\n\n@{author} — {tldr}")
        else:
            parts.append(f"## {emoji} TL;DR\n\n@{author} — reviewing...")
        
        # GIF reaction
        gif_url = t2.get("gif_url")
        if gif_url:
            parts.append(f"\n\n![{emoji}]({gif_url})")
        
        # Graph context
        graph_context = t2.get("graph_context", {})
        if graph_context and graph_context.get("nodes", 0) > 0:
            parts.append(f"\n\n📊 Blast radius: {graph_context['nodes']} nodes affected")
        
        # T1 deep review status
        if "t1" in results:
            t1 = results["t1"]
            if t1.get("spawned"):
                parts.append("\n\n🔍 Deep review dispatched — findings will be posted separately")
            elif t1.get("status") == "error":
                parts.append(f"\n\n⚠️ Deep review: {t1.get('body', 'failed')}")
        
        # T3 visual evidence
        if "t3_visual" in results:
            t3v = results["t3_visual"]
            if t3v.get("status") == "complete":
                parts.append(f"\n\n📸 Visual evidence: {t3v.get('body', '')}")
            else:
                parts.append(f"\n\n⚠️ Visual evidence: {t3v.get('body', 'not available')}")
        
        # Bot 2 status footer
        if self.companion:
            bot2_status = self.companion._get_bot2_status(profile.owner, profile.repo, profile.pr_number)
            if bot2_status:
                parts.append(f"\n\n---\n{bot2_status}")
        
        # Sign-off
        parts.append("\n\n---\n_Reviewed by Riptide T0 · `@riptide-bot review` for re-review_")
        
        return {
            "status": "complete",
            "pr_number": profile.pr_number,
            "body": "\n".join(parts),
            "tiers_used": list(results.keys()),
            "emoji": emoji,
        }
    
    def _post_comment(self, profile: TaskProfile, unified: dict):
        """Post unified comment to PR."""
        if not self.github:
            log.warning("No github client, skipping comment post")
            return
        
        try:
            body = unified.get("body", "")
            if body:
                self.github.post_pr_comment(
                    profile.installation_id,
                    profile.owner, profile.repo, profile.pr_number, body
                )
                log.info(f"Posted unified comment on {profile.owner}/{profile.repo}#{profile.pr_number}")
        except Exception as e:
            log.error(f"Failed to post comment: {e}")
