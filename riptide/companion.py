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
import zlib
from datetime import datetime, timezone
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
# Curated GIF sources per mood. Each mood maps to a list of content-tags.
# At selection time, we pick the tag that best matches the PR title + files,
# then query the GIF API for a relevant result — not a random static URL.
#
# Priority: keyword relevance > content hash > fallback

# Mood → search tags ordered by relevance (first = most specific)
GIF_TAGS = {
    "✨": ["feature launch", "new feature", "sparkle celebration", "shiny"],
    "🐛": ["bug fix", "debugging", "squash bug", "error crash"],
    "♻️": ["refactor", "code cleanup", "restructure", "recycle"],
    "🧹": ["cleaning", "tidying up", "declutter", "sweep"],
    "🔧": ["tools fix", "wrench", "mechanic", "configuration"],
    "📝": ["writing notes", "documentation", "typing", "journal"],
    "📦": ["package delivery", "shipping box", "unboxing", "delivery"],
    "🧪": ["science experiment", "lab test", "chemistry", "testing"],
    "⏪": ["rewind", "go back", "undo", "reverse"],
    "⚡": ["lightning fast", "speed", "fast", "turbo"],
}

# Keyword boosters: if title/files contain these, prefer matching tag
KEYWORD_TAG_BOOST = {
    "feature": "✨", "add": "✨", "new": "✨",
    "fix": "🐛", "bug": "🐛", "hotfix": "🐛", "crash": "🐛", "error": "🐛",
    "refactor": "♻️", "cleanup": "🧹", "clean": "🧹",
    "config": "🔧", "infra": "🔧", "ci": "🔧", "deploy": "🔧",
    "docs": "📝", "readme": "📝", "doc": "📝",
    "test": "🧪", "spec": "🧪",
    "perf": "⚡", "speed": "⚡", "fast": "⚡", "optimize": "⚡",
    "revert": "⏪", "rollback": "⏪",
    "deps": "📦", "upgrade": "📦", "package": "📦",
}

# Static fallback URLs (used when no API key available)
GIFI_MAP = {
    "✨": "https://media.giphy.com/media/26tOZ42Mg6pbTUPHW/giphy.gif",
    "🐛": "https://media.giphy.com/media/l0HlBO7eyXzSZkJri/giphy.gif",
    "♻️": "https://media.giphy.com/media/26tn33aiTi1jkl6H6/giphy.gif",
    "🧹": "https://media.giphy.com/media/3DnDRfZe2ubQc/giphy.gif",
    "🔧": "https://media.giphy.com/media/Y3kQOYHyVZcErGeMYF/giphy.gif",
    "📝": "https://media.giphy.com/media/13HgwGsXF0aiGY/giphy.gif",
    "📦": "https://media.giphy.com/media/3o6Zt6KHwTY5sxJZE/giphy.gif",
    "🧪": "https://media.giphy.com/media/3o7TKSjRrfIPjeiYxW/giphy.gif",
    "⏪": "https://media.giphy.com/media/26tOZ42Mg6pbTUPHW/giphy.gif",
    "⚡": "https://media.giphy.com/media/26tOZ42Mg6pbTUPHW/giphy.gif",
}


def _pick_best_tag(emoji: str, pr_title: str, changed_files: list[dict] = None) -> str:
    """
    Pick the most relevant search tag for a PR based on title + file analysis.
    
    Scoring:
    1. Title keyword match → boost matching tag
    2. File extension patterns → refine tag
    3. Content hash → pick among tied alternatives for variety
    """
    title_lower = pr_title.lower()
    tags = GIF_TAGS.get(emoji, GIF_TAGS["✨"])
    
    # Score each tag by keyword overlap with PR title
    scored = []
    for tag in tags:
        score = 0
        tag_words = tag.lower().split()
        for word in tag_words:
            if word in title_lower:
                score += 2  # Direct title match
        # Specific keyword boosts (tag-specific, not emoji-wide)
        for keyword, boost_emoji in KEYWORD_TAG_BOOST.items():
            if boost_emoji == emoji and keyword in title_lower:
                # Boost matching tags more than non-matching ones
                if keyword in tag_words:
                    score += 3  # Specific match
                else:
                    score += 1  # General emoji match
        scored.append((score, tag))
    
    # Sort by score descending, then pick among top-scoring tags
    scored.sort(key=lambda x: (-x[0], x[1]))
    top_score = scored[0][0] if scored else 0
    top_tags = [tag for score, tag in scored if score == top_score]
    
    if len(top_tags) == 1:
        return top_tags[0]
    
    # Multiple tied → use content hash for deterministic variety
    content_key = pr_title + str(len(changed_files or []))
    if changed_files:
        content_key += "".join(f.get("filename", "")[:10] for f in changed_files[:3])
    idx = zlib.crc32(content_key.encode()) % len(top_tags)
    return top_tags[idx]


def select_gif(emoji: str, pr_title: str = "", changed_files: list[dict] = None) -> str:
    """
    Select a GIF URL based on PR mood and content relevance.
    
    Priority:
    1. Giphy API (if GIPHY_API_KEY set) with keyword-relevant tag
    2. Tenor API (if TENOR_API_KEY set) with keyword-relevant tag  
    3. Static fallback (uses tag from _pick_best_tag for relevance)
    """
    if not pr_title:
        return GIFI_MAP.get(emoji, GIFI_MAP["✨"])
    
    best_tag = _pick_best_tag(emoji, pr_title, changed_files)
    
    # Try Giphy API
    giphy_key = os.environ.get("GIPHY_API_KEY", "")
    if giphy_key:
        try:
            url = _search_giphy(best_tag, giphy_key)
            if url:
                return url
        except Exception:
            pass
    
    # Try Tenor API (better relevance than Giphy for technical content)
    tenor_key = os.environ.get("TENOR_API_KEY", "")
    if tenor_key:
        try:
            url = _search_tenor(best_tag, tenor_key)
            if url:
                return url
        except Exception:
            pass
    
    # Static fallback: use the specific tag to pick a relevant GIF
    return _static_gif_for_tag(emoji, best_tag)


def _static_gif_for_tag(emoji: str, tag: str) -> str:
    """
    Map a specific tag to a deterministic static GIF URL.
    Each unique tag gets its own GIF — so "bug fix" and "debugging"
    produce different results even within the same emoji.
    """
    # Canonical tag → Giphy ID mapping
    TAG_GIF_MAP = {
        # 🐛 bug
        "bug fix": "l0HlBO7eyXzSZkJri",
        "debugging": "3o7TKMt12RVebpyZ0c",
        "squash bug": "xT0xeJpnrWC4XWblEk",
        "error crash": "26tn33aiTi1jkl6H6",
        # ✨ feature
        "feature launch": "26tOZ42Mg6pbTUPHW",
        "new feature": "l46Cbqvg6gxGvh2PS",
        "sparkle celebration": "3o7TKSjRrfIPjeiYxW",
        "shiny": "l0HlNQ03J5JxX2rGU",
        # ♻️ refactor
        "refactor": "3o7qDEq2bMbcbPRQ2c",
        "code cleanup": "l0HlNQ03J5JxX2rGU",
        "restructure": "26tOZ42Mg6pbTUPHW",
        "recycle": "26tn33aiTi1jkl6H6",
        # 🧹 cleanup
        "cleaning": "3DnDRfZe2ubQc",
        "tidying up": "l0HlBO7eyXzSZkJri",
        "declutter": "3o7TKMt12RVebpyZ0c",
        "sweep": "l0HlNQ03J5JxX2rGU",
        # 🔧 config
        "tools fix": "Y3kQOYHyVZcErGeMYF",
        "wrench": "3o7qDEq2bMbcbPRQ2c",
        "mechanic": "l46Cbqvg6gxGvh2PS",
        "configuration": "l0HlBO7eyXzSZkJri",
        # 📝 docs
        "writing notes": "13HgwGsXF0aiGY",
        "documentation": "3o7TKSjRrfIPjeiYxW",
        "typing": "l0HlNQ03J5JxX2rGU",
        "journal": "l46Cbqvg6gxGvh2PS",
        # 📦 deps
        "package delivery": "3o6Zt6KHwTY5sxJZE",
        "shipping box": "l46Cbqvg6gxGvh2PS",
        "unboxing": "3o7qDEq2bMbcbPRQ2c",
        "delivery": "26tOZ42Mg6pbTUPHW",
        # 🧪 test
        "science experiment": "3o7TKSjRrfIPjeiYxW",
        "lab test": "l0HlNQ03J5JxX2rGU",
        "chemistry": "26tOZ42Mg6pbTUPHW",
        "testing": "l46Cbqvg6gxGvh2PS",
        # ⏪ revert
        "rewind": "26tOZ42Mg6pbTUPHW",
        "go back": "3o7TKMt12RVebpyZ0c",
        "undo": "l46Cbqvg6gxGvh2PS",
        "reverse": "l0HlBO7eyXzSZkJri",
        # ⚡ perf
        "lightning fast": "26tOZ42Mg6pbTUPHW",
        "speed": "3o7qDEq2bMbcbPRQ2c",
        "fast": "l0HlBO7eyXzSZkJri",
        "turbo": "l46Cbqvg6gxGvh2PS",
    }
    
    gif_id = TAG_GIF_MAP.get(tag)
    if gif_id:
        return f"https://media.giphy.com/media/{gif_id}/giphy.gif"
    
    # Unknown tag → fallback to emoji default
    return GIFI_MAP.get(emoji, GIFI_MAP["✨"])


def _search_giphy(tag: str, api_key: str, limit: int = 5) -> str | None:
    """Search Giphy for a GIF matching the tag. Returns MP4 or GIF URL."""
    import urllib.request
    import urllib.parse
    
    encoded_tag = urllib.parse.quote(tag)
    url = f"https://api.giphy.com/v1/gifs/search?api_key={api_key}&q={encoded_tag}&limit={limit}&rating=g"
    
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    
    results = data.get("data", [])
    if not results:
        return None
    
    # Pick based on deterministic hash of tag for variety
    idx = zlib.crc32(tag.encode()) % len(results)
    images = results[idx].get("images", {})
    # Prefer fixed_height GIF (smaller, consistent)
    gif = images.get("fixed_height", images.get("original", {}))
    return gif.get("url") or gif.get("mp4")


def _search_tenor(tag: str, api_key: str, limit: int = 5) -> str | None:
    """Search Tenor for a GIF matching the tag. Returns GIF URL."""
    import urllib.request
    import urllib.parse
    
    encoded_tag = urllib.parse.quote(tag)
    url = f"https://tenor.googleapis.com/v2/search?q={encoded_tag}&key={api_key}&limit={limit}&contentfilter=medium"
    
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    
    results = data.get("results", [])
    if not results:
        return None
    
    idx = zlib.crc32(tag.encode()) % len(results)
    media = results[idx].get("media_formats", {})
    # Prefer gif (smaller) over tinygif/mp4
    gif = media.get("gif", media.get("tinygif", media.get("mp4", {})))
    return gif.get("url")


def classify_pr_mood(title: str, changed_files: list[dict] | None = None) -> str:
    """
    Classify PR mood based on title keywords and changed file patterns.
    
    Analyzes both the PR title and the types of files changed to pick
    the most appropriate emoji reaction.
    """
    title_lower = title.lower()
    
    # Check title keywords first (explicit signals)
    priority_keywords = ["revert", "rollback", "hotfix", "bugfix", "security"]
    for keyword in priority_keywords:
        if keyword in title_lower:
            return EMOJI_MAP.get(keyword, "🐛")
    
    # Check file extensions for signals
    if changed_files:
        extensions = set()
        for f in changed_files:
            fname = f.get("filename", "")
            if "." in fname:
                extensions.add("." + fname.rsplit(".", 1)[-1])
        
        # Test files dominant
        test_exts = {".test.", "_test.", ".spec.", "_spec.", "test_"}
        test_files = [f for f in changed_files if
            any(t in f.get("filename", "") for t in test_exts) or
            "/tests/" in f.get("filename", "") or
            "/test/" in f.get("filename", "") or
            f.get("filename", "").split("/")[-1].startswith("test_") or
            f.get("filename", "").split("/")[-1].startswith("spec_")]
        if test_files and len(test_files) > len(changed_files) / 2:
            return "🧪"
        
        # CSS/UI files dominant
        ui_exts = {".css", ".scss", ".less", ".html", ".jsx", ".tsx", ".vue", ".svelte"}
        ui_files = [f for f in changed_files if any(f.get("filename", "").endswith(ext) for ext in ui_exts)]
        if ui_files and len(ui_files) > len(changed_files) / 2:
            return "✨"  # Feature/UI work
        
        # Config/infra files
        config_exts = {".yml", ".yaml", ".toml", ".json", ".ini", ".cfg"}
        config_files = [f for f in changed_files if any(f.get("filename", "").endswith(ext) for ext in config_exts)]
        if config_files and len(config_files) > len(changed_files) / 2:
            return "🔧"
    
    # Fall back to title keyword matching
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

        body = self._format_comment(emoji, author, tldr, graph_context, eli5, ui_files,
                                    owner=owner, repo=repo, pr_number=pr_number,
                                    title=title, files=files, is_delta=is_delta)

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

    @staticmethod
    def _get_bot2_status(owner: str, repo: str, pr_number: int) -> Optional[str]:
        """Read deepthink state and return a Bot 2 status line for the comment footer."""
        state_file = Path(
            os.environ.get("RIPTIDE_DATA_DIR", "/tmp/riptide-data")
        ) / "deepthink_acted_prs.json"
        pr_key = f"{owner}/{repo}#{pr_number}"

        try:
            if not state_file.exists():
                return None
            state = json.loads(state_file.read_text())
            entry = state.get(pr_key)
            if not entry:
                return None
            reviewed_at = entry.get("reviewed_at", "")
            if not reviewed_at:
                return None
            reviewed_time = datetime.fromisoformat(reviewed_at)
            hours_ago = int((datetime.now(timezone.utc) - reviewed_time).total_seconds() / 3600)
            if hours_ago < 24:
                return f"🤖 Bot 2: reviewed {hours_ago}h ago · `@riptide-bot review` for re-review"
            return f"🤖 Bot 2: last reviewed {hours_ago}h+ ago · will auto-review after 30min staleness"
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            return None

    def _format_comment(self, emoji, author, tldr, graph_context, eli5=None, ui_files=None,
                        owner=None, repo=None, pr_number=None, title=None, files=None, is_delta=False):
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
        gif_url = select_gif(emoji, title or "", files or [])
        parts.append(f"\n\n![{emoji}]({gif_url})")

        # Bot 2 status footer
        if owner and repo and pr_number:
            bot2_status = self._get_bot2_status(owner, repo, pr_number)
            if bot2_status:
                parts.append(f"\n{bot2_status}")

        # Sign-off
        parts.append(f"\n\n---\n<sub>🤖 Generated by Riptide · PR review via local Ollama ({self.model}) · `@riptide-bot companion skip` to opt out")
        if owner and repo:
            parts.append(f" · `@riptide-bot review` for deep-think</sub>")
        else:
            parts.append("</sub>")

        return "".join(parts)
