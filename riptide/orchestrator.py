#!/usr/bin/env python3
"""
orchestrator.py — Multi-tier review orchestration (T0 dispatcher).

Top-level dispatcher that classifies a PR and dispatches to review tiers
(T1 quick scan, T2 companion TL;DR, T3 visual/architectural deep-dive).

Uses riptide.state.StateStore for job tracking and dedup.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from riptide.state import StateStore

log = logging.getLogger("riptide.orchestrator")

# ── Concurrency limits ───────────────────────────────────────────────────────

_T0_SEMAPHORE = threading.Semaphore(int(os.environ.get("RIPTIDE_T0_MAX_CONCURRENT", "3")))
_MAX_COMMENT_RETRIES = 3
_MAX_COMMENT_LENGTH = 60000


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
            any(p in (f.get("filename", "") or f.get("path", "")) for p in arch_patterns)
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


# ── T0 Orchestrator ─────────────────────────────────────────────────────────


class T0Orchestrator:
    """
    Top-level dispatcher. Classifies PR and dispatches to review tiers.
    
    Concurrency:
      - Class-level semaphore (_T0_SEMAPHORE) caps concurrent T0 reviews
      - review_pr() acquires on entry, releases on exit
    
    Usage:
        orch = T0Orchestrator(companion=companion, github_client=github_client())
        result = orch.review_pr(profile, mode="parallel")
    """
    
    def __init__(self, companion=None, github_client=None, t3_timeout=180, state_store=None):
        self.companion = companion
        self.github = github_client
        self.t3_timeout = t3_timeout
        # Use the new StateStore from state.py (shared with deepthink/fixer/poller)
        self.state = state_store or StateStore()
        self.classifier = TaskClassifier()
    
    def review_pr(self, profile: TaskProfile, mode: str = "parallel") -> dict:
        """
        Classify and review a PR.
        
        Args:
            profile: classified task profile
            mode: "parallel" (production) or "serial" (verification)
        
        Returns:
            dict with unified review result
        """
        acquired = _T0_SEMAPHORE.acquire(blocking=True, timeout=30)
        if not acquired:
            log.warning(f"T0 semaphore timeout for {profile.owner}/{profile.repo}#{profile.pr_number} — skipping")
            return {"status": "skipped", "reason": "concurrency_limit"}
        
        try:
            log.info(f"T0 reviewing {profile.owner}/{profile.repo}#{profile.pr_number} (mode={mode})")
            
            t2_result = self._dispatch_t2(profile)
            results = self._parallel_review(profile, t2_result)
            
            unified = self._synthesize(results, profile)
            self._post_comment(profile, unified)
            
            return unified
        finally:
            _T0_SEMAPHORE.release()
    
    def _parallel_review(self, profile: TaskProfile, t2_result: dict) -> dict:
        """Dispatch T1 + T3 simultaneously (production)."""
        results = {"t2": t2_result}
        
        if profile.needs_t1:
            job_id = f"{profile.pr_number}-t1"
            self.state.create_job(job_id, profile.pr_number, "t1")
            t1_thread = threading.Thread(
                target=self._dispatch_t1_async,
                args=(profile, job_id),
                daemon=True,
                name=f"t1-{profile.pr_number}",
            )
            t1_thread.start()
            results["t1"] = {"status": "dispatched", "tier": "t1", "body": "Deep review dispatched (async)"}
        
        if profile.needs_t3_visual:
            job_id = f"{profile.pr_number}-t3-visual"
            self.state.create_job(job_id, profile.pr_number, "t3_visual")
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self._dispatch_t3_visual, profile)
                try:
                    results["t3_visual"] = future.result(timeout=self.t3_timeout)
                    self.state.mark_complete(job_id)
                except FuturesTimeout:
                    results["t3_visual"] = {
                        "status": "timeout",
                        "body": f"⏰ Visual evidence timed out after {self.t3_timeout}s",
                    }
                    self.state.mark_failed(job_id)
                except Exception as e:
                    results["t3_visual"] = {
                        "status": "error",
                        "body": f"❌ Visual evidence failed: {str(e)}",
                    }
                    self.state.mark_failed(job_id)
        
        if profile.needs_t3_arch:
            job_id = f"{profile.pr_number}-t3-arch"
            self.state.create_job(job_id, profile.pr_number, "t3_arch")
            t3a_thread = threading.Thread(
                target=self._dispatch_t1_async,
                args=(profile, job_id),
                daemon=True,
                name=f"t3a-{profile.pr_number}",
            )
            t3a_thread.start()
            results["t3_arch"] = {"status": "dispatched", "tier": "t3_arch", "body": "Architecture diagram dispatched"}
        
        return results
    
    def _dispatch_t2(self, profile: TaskProfile) -> dict:
        """T2: Quick TL;DR via companion (cheap, always runs)."""
        if not self.companion:
            return {"status": "skipped", "tier": "t2", "body": ""}
        
        from riptide.companion import classify_pr_mood, select_gif
        emoji = "✨"
        gif_url = ""
        try:
            emoji = classify_pr_mood(profile.title, profile.files)
            gif_url = select_gif(emoji, profile.title, profile.files)
        except Exception as e:
            log.warning(f"T2 emoji/GIF classification failed: {e}")
        
        tldr = None
        graph_context = None
        try:
            graph_context = self.companion._get_graph_context(profile.files)
            tldr = self.companion._generate_tldr(
                profile.title, profile.author, profile.files, graph_context
            )
        except Exception as e:
            log.warning(f"T2 TL;DR generation failed: {e}")
        
        return {
            "status": "complete" if tldr else "partial",
            "tier": "t2",
            "emoji": emoji,
            "gif_url": gif_url,
            "tldr": tldr,
            "graph_context": graph_context,
            "body": tldr or "",
        }
    
    def _dispatch_t1_async(self, profile: TaskProfile, job_id: str):
        """Non-blocking T1 dispatch (fire-and-forget with state tracking)."""
        try:
            result = self._dispatch_t1(profile)
            if result.get("status") == "dispatched":
                self.state.mark_complete(job_id)
            else:
                self.state.mark_failed(job_id)
        except Exception as e:
            self.state.mark_failed(job_id)
            log.warning(f"T1 async dispatch failed for job {job_id}: {e}")
    
    def _dispatch_t1(self, profile: TaskProfile) -> dict:
        """Dispatch to T1 (deepthink via Hermes cron)."""
        try:
            from riptide.deepthink import _spawn_deepthink
            result = _spawn_deepthink(
                profile.owner, profile.repo, profile.pr_number,
                profile.title, profile.author, profile.total_loc,
                profile.head_sha,
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
            from riptide.proofshotter import _checkout_pr, _run_proofshot, _upload_gif, _post_proofshot_comment
            
            work_dir = _checkout_pr(profile.owner, profile.repo, profile.pr_number)
            if not work_dir:
                return {"status": "error", "tier": "t3_visual", "body": "Checkout failed"}
            
            result = _run_proofshot(
                profile.pr_number,
                url=os.environ.get("RIPTIDE_PROOFSHOT_URL", "http://localhost:8788"),
                seed_path=None,
                output_dir=Path(f"/tmp/proofshot-pr-{profile.owner}-{profile.repo}-{profile.pr_number}"),
                captures=[],
            )
            if not result:
                return {"status": "error", "tier": "t3_visual", "body": "Capture failed"}
            
            gif_path = result.get("gif", "")
            gif_url = _upload_gif(gif_path, profile.pr_number)
            if gif_url and self.github:
                _post_proofshot_comment(
                    profile.owner, profile.repo, profile.pr_number,
                    gif_url, screenshots=result.get("screenshots", [])
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
        """Dispatch to T3 (excalidraw architecture diagram via deepthink)."""
        try:
            from riptide.deepthink import _spawn_deepthink
            result = _spawn_deepthink(
                profile.owner, profile.repo, profile.pr_number,
                f"[ARCHITECTURE DIAGRAM] {profile.title}", profile.author, profile.total_loc,
                head_sha=profile.head_sha,
            )
            return {
                "status": "dispatched" if result else "failed",
                "tier": "t3_arch",
                "body": "Architecture diagram dispatched" if result else "Architecture diagram dispatch failed",
                "spawned": result,
            }
        except Exception as e:
            log.warning(f"T3 arch dispatch failed: {e}")
            return {"status": "error", "tier": "t3_arch", "body": f"Architecture diagram failed: {str(e)}"}
    
    def _synthesize(self, results: dict, profile: TaskProfile) -> dict:
        """Aggregate all tier results into unified comment."""
        parts = []
        t2 = results.get("t2", {})
        
        emoji = t2.get("emoji", "✨")
        tldr = t2.get("tldr", "")
        author = profile.author
        
        if tldr:
            parts.append(f"## {emoji} TL;DR\n\n@{author} — {tldr}")
        else:
            parts.append(f"## {emoji} TL;DR\n\n@{author} — reviewing...")
        
        gif_url = t2.get("gif_url")
        if gif_url:
            parts.append(f"\n\n![{emoji}]({gif_url})")
        
        graph_context = t2.get("graph_context", {})
        if graph_context and graph_context.get("nodes", 0) > 0:
            parts.append(f"\n\n📊 Blast radius: {graph_context['nodes']} nodes affected")
        
        if "t1" in results:
            t1 = results["t1"]
            if t1.get("spawned"):
                parts.append("\n\n🔍 Deep review dispatched — findings will be posted separately")
            elif t1.get("status") == "error":
                parts.append(f"\n\n⚠️ Deep review: {t1.get('body', 'failed')}")
        
        if "t3_visual" in results:
            t3v = results["t3_visual"]
            if t3v.get("status") == "complete":
                parts.append(f"\n\n📸 Visual evidence: {t3v.get('body', '')}")
            else:
                parts.append(f"\n\n⚠️ Visual evidence: {t3v.get('body', 'not available')}")
        
        if self.companion:
            bot2_status = self.companion._get_bot2_status(profile.owner, profile.repo, profile.pr_number)
            if bot2_status:
                parts.append(f"\n\n---\n{bot2_status}")
        
        parts.append("\n\n---\n_Reviewed by Riptide T0 · `@riptide-bot review` for re-review_")
        
        body = "\n".join(parts)
        if len(body) > _MAX_COMMENT_LENGTH:
            body = body[:_MAX_COMMENT_LENGTH - 100] + "\n\n... (truncated)"
        
        return {
            "status": "complete",
            "pr_number": profile.pr_number,
            "body": body,
            "tiers_used": list(results.keys()),
            "emoji": emoji,
        }
    
    def _post_comment(self, profile: TaskProfile, unified: dict):
        """Post unified comment to PR with retry."""
        if not self.github:
            log.warning("No github client, skipping comment post")
            return
        
        body = unified.get("body", "")
        if not body:
            return
        
        for attempt in range(1, _MAX_COMMENT_RETRIES + 1):
            try:
                self.github.post_pr_comment(
                    profile.installation_id,
                    profile.owner, profile.repo, profile.pr_number, body
                )
                log.info(f"Posted unified comment on {profile.owner}/{profile.repo}#{profile.pr_number}")
                return
            except Exception as e:
                log.warning(f"Comment post attempt {attempt}/{_MAX_COMMENT_RETRIES} failed: {e}")
                if attempt < _MAX_COMMENT_RETRIES:
                    time.sleep(2 * attempt)
        
        log.error(f"Failed to post comment after {_MAX_COMMENT_RETRIES} attempts")
