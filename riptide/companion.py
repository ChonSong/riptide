"""
companion.py — GitHub Companion PR Agent for Riptide.

Pipeline:
  1. Fetch PR diffs from GitHub API
  2. Analyze diffs (new functions, imports, stats)
  3. Query Graphify for blast radius
  4. Generate TLDR via Ollama (qwen2.5-coder:7b)
  5. Generate ELI5 via Ollama (or skip)
  6. Post comment with GIF reaction + model attribution

NO TEMPLATE FALLBACKS: if model is down, we don't comment.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("riptide.companion")

# ── Emoji classification ─────────────────────────────────────────────────────

EMOJI_MAP = {
    "feature": "✨", "addition": "✨", "add": "✨",
    "bug": "🐛", "fix": "🐛", "bugfix": "🐛", "hotfix": "🐛",
    "perf": "⚡", "performance": "⚡", "speed": "⚡",
    "refactor": "♻️", "cleanup": "🧹", "tech debt": "🧹",
    "config": "🔧", "infra": "🔧", "infrastructure": "🔧", "ci": "🔧",
    "docs": "📝", "documentation": "📝", "doc": "📝",
    "dependencies": "📦", "dependency": "📦", "deps": "📦", "upgrade": "📦",
    "test": "🧪", "tests": "🧪", "testing": "🧪",
    "revert": "⏪", "rollback": "⏪",
}


# ── Patterns ───────────────────────────────────────────────────────────────

SKIP_RE = re.compile(r"@riptide-bot\s+companion\s+(skip|resume)", re.IGNORECASE)


# ── GIFs ───────────────────────────────────────────────────────────────────

GIFI_MAP = {
    "✨": "https://media.giphy.com/media/26tOZ42Mg6pbTUPHW/giphy.gif",  # sparkles
    "🐛": "https://media.giphy.com/media/l0HlBO7eyXzSZkJri/giphy.gif",  # bug
    "♻️": "https://media.giphy.com/media/26tn33aiTi1jkl6H6/giphy.gif",  # recycle/refactor
    "🧹": "https://media.giphy.com/media/3DnDRfZe2ubQc/giphy.gif",    # cleaning
    "🔧": "https://media.giphy.com/media/Y3kQOYHyVZcErGeMYF/giphy.gif",  # wrench/config
    "📝": "https://media.giphy.com/media/13HgwGsXF0aiGY/giphy.gif",    # writing/docs
    "📦": "https://media.giphy.com/media/3o6Zt6KHwTY5sxJZE/giphy.gif",  # package/deps
    "🧪": "https://media.giphy.com/media/3o7TKSjRrfIPjeiYxW/giphy.gif",  # test
    "⏪": "https://media.giphy.com/media/26tOZ42Mg6pbTUPHW/giphy.gif",  # rewind/revert
    "⚡": "https://media.giphy.com/media/26tOZ42Mg6pbTUPHW/giphy.gif",  # lightning/perf
}


def classify_pr_mood(title: str, changed_files: list[dict]) -> str:
    title_lower = title.lower()
    for keyword, emoji in EMOJI_MAP.items():
        if keyword in title_lower:
            return emoji
    return "✨"


class Companion:
    def __init__(self, github_client):
        self.client = github_client
        self.ollama_base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:43311")
        self.model = os.environ.get("RIPTIDE_COMPANION_MODEL", "qwen2.5-coder:7b")

        data_dir = Path(os.environ.get("RIPTIDE_DATA_DIR", "/tmp/riptide"))
        self._skip_file = data_dir / "companion_skip.json"
        self._skip_file.parent.mkdir(parents=True, exist_ok=True)
        self._skip_lock = threading.Lock()

        self._semaphore = threading.Semaphore(1)

        self.allowed_repos: set[str] = set()
        raw = os.environ.get("RIPTIDE_COMPANION_REPOS", "").strip()
        if raw:
            try:
                parts = json.loads(raw)
                self.allowed_repos = set(parts) if isinstance(parts, list) else {raw}
            except json.JSONDecodeError:
                self.allowed_repos = {r.strip() for r in raw.split(",") if r.strip()}

        self.enable_graphify = os.environ.get("COMPANION_ENABLE_GRAPHIFY", "1") == "1"

        logger.info(
            "Companion initialised: model=%s repos=%s",
            self.model, sorted(self.allowed_repos) if self.allowed_repos else "(none)",
        )
        threading.Thread(target=self.warm_up, daemon=True).start()

    def warm_up(self):
        try:
            resp = requests.post(
                f"{self.ollama_base}/api/generate",
                json={"model": self.model, "prompt": "ok", "stream": False, "options": {"num_predict": 1}},
                timeout=120,
            )
            if resp.status_code == 200:
                logger.info("Model warmed up: %s", self.model)
        except Exception as e:
            logger.warning("Warm-up failed: %s", e)

    def is_active_for(self, owner: str, repo: str) -> bool:
        return f"{owner}/{repo}" in self.allowed_repos if self.allowed_repos else False

    def run_for_pr(self, installation_id, owner, repo, pr_number, title, author, changed_files):
        if not self._semaphore.acquire(blocking=False):
            logger.warning("Busy, skipping %s/%s#%d", owner, repo, pr_number)
            return
        try:
            self._execute(installation_id, owner, repo, pr_number, title, author, changed_files)
        finally:
            self._semaphore.release()

    def handle_comment(self, installation_id, owner, repo, pr_number, comment_body, commenter):
        m = SKIP_RE.search(comment_body)
        if not m:
            return None
        action = m.group(1).lower()
        key = f"{owner}/{repo}#{pr_number}"
        if action == "skip":
            self.set_skip(owner, repo, pr_number, True)
            return "🤖 Companion will **skip** this PR. Reply `@riptide-bot companion resume` to re-enable."
        if action == "resume":
            self.set_skip(owner, repo, pr_number, False)
            return "🤖 Companion **resumed** for this PR."
        return None

    # ---- State file helpers -------------------------------------------------
    def _backup_skip_file(self):
        try:
            if self._skip_file.exists():
                ts = int(time.time())
                bak = self._skip_file.with_name(f"companion_skip.json.bak.{ts}")
                self._skip_file.rename(bak)
                logger.warning("Backed up malformed skip file to %s", bak)
        except Exception as e:
            logger.warning("Failed to backup skip file: %s", e)

    def _load_data(self) -> dict:
        """Load companion data file (structured per-PR dict)."""
        if not self._skip_file.exists():
            return {}
        raw = self._skip_file.read_text().strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse companion_skip.json: %s", e)
            # Backup the corrupted file so operators can inspect it
            self._backup_skip_file()
            return {}

    def _migrate_entry(self, entry):
        """Normalize legacy boolean skip values to structured dicts."""
        if isinstance(entry, bool):
            return {"skip": entry, "last_sha": None}
        if isinstance(entry, dict):
            return {"skip": entry.get("skip", False), "last_sha": entry.get("last_sha", None)}
        return {"skip": False, "last_sha": None}

    def _get_last_sha(self, owner, repo, pr_number) -> Optional[str]:
        key = f"{owner}/{repo}#{pr_number}"
        try:
            with self._skip_lock:
                data = self._load_data()
                entry = self._migrate_entry(data.get(key, {}))
                return entry.get("last_sha")
        except Exception:
            return None

    def _set_last_sha(self, owner, repo, pr_number, sha: Optional[str]):
        key = f"{owner}/{repo}#{pr_number}"
        try:
            with self._skip_lock:
                data = self._load_data()
                entry = self._migrate_entry(data.get(key, {}))
                data[key] = {"skip": entry["skip"], "last_sha": sha}
                self._skip_file.write_text(json.dumps(data, indent=2, sort_keys=True))
                return True
        except Exception as e:
            logger.error("SHA update failed: %s", e)
            return False

    def _claim_and_reserve_sha(self, owner, repo, pr_number, sha: str) -> Optional[str]:
        """
        Atomically claim this SHA for commenting to avoid duplicate posts.
        Returns the previous SHA (which may be None) if we successfully reserved the new SHA,
        or returns None if the current recorded SHA already matches the requested sha (no-op).
        """
        key = f"{owner}/{repo}#{pr_number}"
        try:
            with self._skip_lock:
                data = self._load_data()
                entry = self._migrate_entry(data.get(key, {}))
                prev = entry.get("last_sha")
                if prev == sha:
                    # Already at this SHA — nothing to do
                    return None
                # Reserve the SHA immediately to prevent concurrent posters
                entry["last_sha"] = sha
                data[key] = entry
                self._skip_file.write_text(json.dumps(data, indent=2, sort_keys=True))
                return prev
        except Exception as e:
            logger.error("Failed to claim SHA for %s: %s", key, e)
            return False

    # ---- Public skip API ---------------------------------------------------
    def set_skip(self, owner, repo, pr_number, skip):
        key = f"{owner}/{repo}#{pr_number}"
        try:
            with self._skip_lock:
                data = self._load_data()
                entry = self._migrate_entry(data.get(key, {}))
                if skip:
                    # Preserve last_sha when setting skip
                    data[key] = {"skip": True, "last_sha": entry.get("last_sha")}
                else:
                    # Preserve last_sha but mark skip=False
                    data[key] = {"skip": False, "last_sha": entry.get("last_sha")}
                self._skip_file.write_text(json.dumps(data, indent=2, sort_keys=True))
                return True
        except Exception as e:
            logger.error("Skip update failed: %s", e)
            return False

    def _is_skipped(self, owner, repo, pr_number):
        key = f"{owner}/{repo}#{pr_number}"
        try:
            with self._skip_lock:
                data = self._load_data()
                entry = self._migrate_entry(data.get(key, {}))
                return entry["skip"]
        except Exception:
            return False

    def _execute(self, installation_id, owner, repo, pr_number, title, author, changed_files):
        full_name = f"{owner}/{repo}"

        if self._is_skipped(owner, repo, pr_number):
            logger.info("Skipped (user) %s#%d", full_name, pr_number)
            return

        # Refresh graphify data before analyzing — cheap AST-only update
        if self.enable_graphify:
            try:
                import subprocess
                from pathlib import Path
                repo_workspace = Path.home() / "workspace" / repo
                if repo_workspace.is_dir() and (repo_workspace / "graphify-out").is_dir():
                    result = subprocess.run(
                        ["graphify", "update", "."],
                        capture_output=True, text=True, timeout=30,
                        cwd=str(repo_workspace),
                    )
                    if result.returncode == 0:
                        logger.info("Graphify updated for %s", repo)
                    else:
                        logger.warning("Graphify update stderr for %s: %s", repo, result.stderr[:200])
                else:
                    logger.debug("No graphify-out dir at %s — skipping update", repo_workspace)
            except FileNotFoundError:
                logger.debug("graphify binary not found — skipping update")
            except Exception as e:
                logger.warning("Graphify update failed for %s: %s", repo, e)

        # Attempt to fetch current PR head SHA for change tracking
        current_sha = None
        try:
            # If the github client supports get_pr_details, use it to obtain head.sha
            pr_details = None
            if hasattr(self.client, "get_pr_details"):
                pr_details = self.client.get_pr_details(installation_id, owner, repo, pr_number)
            elif hasattr(self.client, "get_pull_request"):
                pr_details = self.client.get_pull_request(installation_id, owner, repo, pr_number)
            if pr_details:
                # permissive access
                current_sha = pr_details.get("head", {}).get("sha") if isinstance(pr_details, dict) else None
        except Exception as e:
            logger.warning("Failed to fetch PR details for %s#%d: %s", full_name, pr_number, e)

        # If we have a current SHA, try to claim/reserve it atomically; if it already equals the
        # recorded SHA, skip to avoid duplicate comments.
        prev_sha = None
        if current_sha:
            claimed = self._claim_and_reserve_sha(owner, repo, pr_number, current_sha)
            if claimed is None:
                logger.info("No new commits since last comment for %s#%d — skipping", full_name, pr_number)
                return
            elif claimed is False:
                # Claim failed due to an IO error; fall back to best-effort behavior
                logger.warning("Could not atomically claim SHA for %s#%d — proceeding without claim", full_name, pr_number)
            else:
                prev_sha = claimed

        # Fetch diffs if not provided
        files = changed_files
        try:
            if not files or not any("patch" in f for f in files[:3]):
                files = self.client.get_pr_files(installation_id, owner, repo, pr_number)
        except Exception as e:
            logger.warning("Failed to fetch files: %s", e)

        emoji = classify_pr_mood(title, files)
        graph_context = self._get_graph_context(files) if self.enable_graphify else None

        # Generate TLDR — if model fails, skip the PR (no fallback)
        tldr = self._generate_tldr(title, author, files, graph_context)
        if not tldr:
            logger.warning("TLDR failed %s#%d — no comment posted", full_name, pr_number)
            # If we reserved a SHA but didn't post, restore previous SHA so we can retry later
            if current_sha and prev_sha is not None:
                self._set_last_sha(owner, repo, pr_number, prev_sha)
            return

        # Detect UI files for ProofShot section
        ui_extensions = {'.css', '.scss', '.less', '.html', '.jsx', '.tsx', '.vue', '.svelte', '.astro'}
        ui_files = [f for f in files if any(f.get("filename", "").endswith(ext) for ext in ui_extensions)]

        # Generate ELI5 (optional — skip if model fails)
        eli5 = self._generate_eli5(title, files)

        body = self._format_comment(emoji, author, tldr, graph_context, eli5, ui_files)

        try:
            self.client.post_pr_comment(installation_id, owner, repo, pr_number, body)
            logger.info("Posted TLDR for %s#%d", full_name, pr_number)
        except Exception as e:
            logger.error("Failed to post: %s", e)
            # Restore previous SHA so that future pushes can be re-processed
            if current_sha and prev_sha is not None:
                self._set_last_sha(owner, repo, pr_number, prev_sha)
            return

        # Success: ensure last_sha is set to the current commit (it should already be reserved)
        if current_sha:
            # best-effort write to ensure last_sha persisted
            self._set_last_sha(owner, repo, pr_number, current_sha)

    def _get_graph_context(self, changed_files):
        graphify_bin = os.environ.get("GRAPHIFY_BIN", "graphify")
        try:
            filenames = [f["filename"] for f in changed_files[:10] if f.get("filename")]
            if not filenames:
                return None

            lines = []
            for fname in filenames:
                try:
                    result = subprocess.run([graphify_bin, "affected", fname], capture_output=True, text=True, timeout=5)
                    if result.returncode == 0 and result.stdout.strip() and "No affected nodes found" not in result.stdout:
                        lines.append(f"--- {fname} ---\n{result.stdout.strip()}")
                except subprocess.TimeoutExpired:
                    continue

            if not lines:
                return None

            node_count = sum(1 for l in "\n".join(lines).split("\n") if l.strip().startswith("- ") or "→" in l)
            return {"raw": "\n\n".join(lines), "nodes": node_count, "files_checked": len(filenames)}
        except Exception as e:
            logger.warning("Graphify error: %s", e)
            return None

    def _analyze_diffs(self, files):
        total_add = sum(f.get("additions", 0) for f in files)
        total_del = sum(f.get("deletions", 0) for f in files)
        new_files = [f for f in files if f.get("status") == "added"]
        mod_files = [f for f in files if f.get("status") == "modified"]
        rem_files = [f for f in files if f.get("status") == "removed"]

        # Detect UI files
        ui_extensions = {'.css', '.scss', '.less', '.html', '.jsx', '.tsx', '.vue', '.svelte', '.astro', '.svg', '.png', '.jpg', '.gif', '.webp'}
        ui_files = [f for f in files if any(f.get("filename", "").endswith(ext) for ext in ui_extensions)]

        new_funcs = []
        new_classes = []
        imports_added = []
        imports_removed = []

        for f in files:
            patch = f.get("patch", "")
            if not patch:
                continue
            fname = f.get("filename", "?")
            for line in patch.split("\n"):
                if line.startswith("+def ") and not line.startswith("+++"):
                    new_funcs.append(f"{fname}::{line[5:].split('(')[0].strip()}")
                elif line.startswith("+class ") and not line.startswith("+++"):
                    new_classes.append(f"{fname}::{line[7:].split('(')[0].split(':')[0].strip()}")
                elif line.startswith("+import ") or (line.startswith("+from ") and " import" in line):
                    imports_added.append(line[1:].strip())
                elif line.startswith("-import ") or (line.startswith("-from ") and " import" in line):
                    imports_removed.append(line[1:].strip())

        parts = []
        if new_files:
            parts.append(f"New: {', '.join(f['filename'].split('/')[-1] for f in new_files[:3])}")
        if mod_files:
            parts.append(f"Modified: {', '.join(f['filename'].split('/')[-1] for f in mod_files[:3])}")
        if rem_files:
            parts.append(f"Removed: {', '.join(f['filename'].split('/')[-1] for f in rem_files[:3])}")
        if new_funcs:
            parts.append(f"Functions: {', '.join(new_funcs[:5])}")
        if new_classes:
            parts.append(f"Classes: {', '.join(new_classes[:3])}")
        if imports_added:
            parts.append(f"Imports+: {', '.join(imports_added[:3])}")
        if imports_removed:
            parts.append(f"Imports-: {', '.join(imports_removed[:3])}")
        if ui_files:
            parts.append(f"UI files: {', '.join(f['filename'].split('/')[-1] for f in ui_files[:3])}")
        parts.append(f"+{total_add}/-{total_del}")
        return ". ".join(parts) + ".", ui_files

    def _generate_tldr(self, title, author, files, graph_context):
        diff_analysis, ui_files = self._analyze_diffs(files)
        impact = f"Blast radius: {graph_context['nodes']} code paths. " if graph_context and graph_context.get("nodes", 0) > 0 else ""

        # ProofShot instruction for UI changes
        proofshot_section = ""
        if ui_files:
            ui_list = ", ".join(f.get("filename", "").split("/")[-1] for f in ui_files[:5])
            proofshot_section = f"""

 ## 📸 ProofShot Required
 UI files changed: {ui_list}
 After applying fixes, run: proofshot start → verify UI → proofshot stop → proofshot pr <number>"""

        prompt = f"""Write a 2-3 sentence TLDR for this PR. Be specific about what changed and what the author should double-check.

 PR: {title}
 By: {author}

 ## Changes:
 {diff_analysis}

 ## Impact:
 {impact or "No significant cross-file impact detected."}

 Instructions:
 - Sentence 1: What changed (mention specific files/functions/patterns)
 - Sentence 2: Impact on codebase
 - Sentence 3: What to double-check before merging
 - If UI files changed: ProofShot visual verification required{proofshot_section}

 TLDR:"""

        return self._ollama_call(prompt)

    def _generate_eli5(self, title, files):
        file_list = ", ".join(f.get("filename", "?") for f in files[:5])
        prompt = f"""Explain this PR like I'm 5. One analogy, 1-2 sentences.

 PR: {title}
 Files: {file_list}

 ELI5:"""
        return self._ollama_call(prompt)

    def _ollama_call(self, prompt):
        try:
            resp = requests.post(
                f"{self.ollama_base}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False, "options": {"num_predict": 256, "temperature": 0.3}},
                timeout=120,
            )
            if resp.status_code == 200:
                text = resp.json().get("response", "").strip()
                if text and Companion._is_valid(text):
                    return text
                logger.warning("Invalid response: %s", text[:80])
                return None
            logger.warning("HTTP %d", resp.status_code)
            return None
        except requests.exceptions.Timeout:
            logger.warning("Timeout (120s)")
            return None
        except Exception as e:
            logger.warning("Error: %s", e)
            return None

    @staticmethod
    def _is_valid(text):
        bad = ["private message", "cannot provide", "can't provide", "cannot summarize", "can't summarize", "i cannot", "i can't", "as an ai", "i'm sorry", "i am sorry", "unable to"]
        return not any(p in text.lower() for p in bad)

    def _format_comment(self, emoji, author, tldr, graph_context, eli5=None, ui_files=None):
        """
        Build the Markdown comment body using the Phase 4 TLDR spec.
        Includes optional ELI5 (Explain Like I'm 5) section.
        Includes ProofShot section if UI files changed.
        """
        parts = [f"## {emoji} TL;DR\n\n@{author} — {tldr}"]

        if graph_context and graph_context.get("nodes", 0) > 0:
            raw = graph_context["raw"]
            useful = [l.strip() for l in raw.split("\n") if l.strip() and not l.strip().startswith(("Affected", "Relations", "Depth", "---")) and "→" in l.strip()]
            if useful:
                parts.append(f"\n**📊 Blast Radius**\n{'; '.join(useful[:2])}")
            elif graph_context.get("files_checked", 0) > 0:
                parts.append(f"\n**📊 Blast Radius**\n{graph_context['files_checked']} files, {graph_context['nodes']} code paths")

        if eli5:
            parts.append(f"\n**🧒 ELI5**\n{eli5}")

        # ProofShot section for UI changes
        if ui_files:
            ui_names = ", ".join(f.get("filename", "").split("/")[-1] for f in ui_files[:5])
            parts.append(f"\n**📸 ProofShot Required**\nUI files changed: {ui_names}\nPlease run ProofShot visual verification before merging.")

        # GIF reaction
        gif_url = GIFI_MAP.get(emoji, GIFI_MAP["✨"])
        parts.append(f"\n\n![{emoji}]({gif_url})")

        # Sign-off
        parts.append(f"\n\n---\n<sub>🤖 Generated by Riptide · PR review via local Ollama ({self.model}) · `@riptide-bot companion skip` to opt out</sub>")

        return "".join(parts)
