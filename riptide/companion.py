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

# ── Patterns ─────────────────────────────────────────────────────────────────

SKIP_RE = re.compile(r"@riptide-bot\s+companion\s+(skip|resume)", re.IGNORECASE)

# ── GIFs ─────────────────────────────────────────────────────────────────────

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

    def _load_data(self) -> dict:
        """Load companion data file (structured per-PR dict)."""
        if not self._skip_file.exists():
            return {}
        raw = self._skip_file.read_text().strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _migrate_entry(self, entry):
        """Normalize legacy boolean skip values to structured dicts."""
        if isinstance(entry, bool):
            return {"skip": entry, "last_sha": None}
        if isinstance(entry, dict):
            return {"skip": entry.get("skip", False), "last_sha": entry.get("last_sha", None)}
        return {"skip": False, "last_sha": None}

    def set_skip(self, owner, repo, pr_number, skip):
        key = f"{owner}/{repo}#{pr_number}"
        try:
            with self._skip_lock:
                data = self._load_data()
                entry = self._migrate_entry(data.get(key, {}))
                if skip:
                    data[key] = {"skip": True, "last_sha": entry.get("last_sha")}
                else:
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
            pass
        return False

    def _get_last_sha(self, owner, repo, pr_number) -> Optional[str]:
        """Get the last commented commit SHA for a PR, or None if first time."""
        key = f"{owner}/{repo}#{pr_number}"
        try:
            with self._skip_lock:
                data = self._load_data()
                entry = self._migrate_entry(data.get(key, {}))
                return entry.get("last_sha")
        except Exception:
            return None

    def _set_last_sha(self, owner, repo, pr_number, sha: str):
        """Record the commit SHA this PR was last commented on."""
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

    def _execute(self, installation_id, owner, repo, pr_number, title, author, changed_files):
        full_name = f"{owner}/{repo}"

        if self._is_skipped(owner, repo, pr_number):
            logger.info("Skipped (user) %s#%d", full_name, pr_number)
            return

        # Get PR head SHA for change tracking
        pr_details = None
        current_sha = None
        try:
            pr_details = self.client.get_pr_details(installation_id, owner, repo, pr_number)
            current_sha = pr_details.get("head", {}).get("sha")
        except Exception as e:
            logger.warning("Failed to fetch PR details: %s", e)

        last_sha = self._get_last_sha(owner, repo, pr_number) if current_sha else None

        # If same SHA as last comment, skip (no new changes)
        if last_sha and current_sha and last_sha == current_sha:
            logger.info("No new commits since last comment for %s#%d — skipping", full_name, pr_number)
            return

        # Fetch files to analyze
        files = changed_files or []
        is_delta = bool(last_sha) and bool(current_sha)

        if is_delta:
            # Get only the diff between last SHA and current HEAD
            try:
                compare = self.client.compare_commits(installation_id, owner, repo, last_sha, current_sha)
                files = compare.get("files", files)
                delta_commits = compare.get("total_commits", 0)
                logger.info(
                    "Delta for %s#%d: %d new commit(s), %d file(s) changed",
                    full_name, pr_number, delta_commits, len(files),
                )
            except Exception as e:
                logger.warning("Failed to compare commits: %s, falling back to full PR diff", e)
                is_delta = False
                try:
                    files = self.client.get_pr_files(installation_id, owner, repo, pr_number)
                except Exception as e2:
                    logger.warning("Failed to fetch files: %s", e2)

        if not files:
            try:
                files = self.client.get_pr_files(installation_id, owner, repo, pr_number)
            except Exception as e:
                logger.warning("Failed to fetch files: %s", e)

        emoji = classify_pr_mood(title, files)
        graph_context = self._get_graph_context(files) if self.enable_graphify else None

        # Generate TL;DR — pass is_delta flag for focus
        tldr = self._generate_tldr(title, author, files, graph_context, is_delta=is_delta)
        if not tldr:
            logger.warning("TLDR failed %s#%d — no comment posted", full_name, pr_number)
            return

        # Detect UI files for ProofShot section
        ui_extensions = {'.css', '.scss', '.less', '.html', '.jsx', '.tsx', '.vue', '.svelte', '.astro'}
        ui_files = [f for f in files if any(f.get("filename", "").endswith(ext) for ext in ui_extensions)]

        # Generate ELI5 (optional — skip if model fails)
        eli5 = self._generate_eli5(title, files, is_delta=is_delta)

        body = self._format_comment(emoji, author, tldr, graph_context, eli5, ui_files, is_delta=is_delta)

        try:
            self.client.post_pr_comment(installation_id, owner, repo, pr_number, body)
            logger.info("Posted TLDR for %s#%d", full_name, pr_number)
            # Record the SHA we just commented on
            if current_sha:
                self._set_last_sha(owner, repo, pr_number, current_sha)
        except Exception as e:
            logger.error("Failed to post: %s", e)

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

    def _generate_tldr(self, title, author, files, graph_context, is_delta=False):
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

        if is_delta:
            prompt = f"""Write a 2-3 sentence TL;DR focusing on what CHANGED in this latest push to the PR.

PR: {title}
By: {author}

## New changes in this push:
{diff_analysis}

## Impact:
{impact or "No significant cross-file impact detected."}

Instructions:
- This is an UPDATE to an existing PR. Focus ONLY on the new changes in this push.
- Sentence 1: What files/functions/patterns were added or modified in this push
- Sentence 2: How these new changes affect the codebase
- Sentence 3: What to double-check before merging
- If UI files changed: ProofShot visual verification required{proofshot_section}

TLDR:"""
        else:
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

    def _generate_eli5(self, title, files, is_delta=False):
        file_list = ", ".join(f.get("filename", "?") for f in files[:5])
        context = "new changes in this push of " if is_delta else ""
        prompt = f"""Explain {context}this PR like I'm 5. One analogy, 1-2 sentences.

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

    def _format_comment(self, emoji, author, tldr, graph_context, eli5=None, ui_files=None, is_delta=False):
        """
        Build the Markdown comment body using the Phase 4 TLDR spec.
        Includes optional ELI5 (Explain Like I'm 5) section.
        Includes ProofShot section if UI files changed.
        """
        prefix = "🔄 " if is_delta else ""
        parts = [f"## {prefix}{emoji} TL;DR\n\n@{author} — {tldr}"]

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
