#!/usr/bin/env python3
"""proofshotter.py — Bot 3: Riptide ProofShot (visual verification).

Cron-polled worker that:
  1. Polls open PRs for UI changes
  2. Runs proofshot Playwright captures on the dev instance
     (proofshot.config.json is optional — defaults to localhost:8788
      if absent; include one in the PR root for custom captures/seed)
  3. Posts visual evidence (GIF) as a GitHub PR comment

Dedup: tracks pr_number + head_sha to avoid re-running on the same revision.
New commits with UI changes automatically retrigger (no 24h cooldown).
Manual override: @riptide-bot proofshot bypasses dedup.

LLM gate: lightweight local Ollama model decides if a PR needs visual
verification. Defaults to YES if the model is unreachable.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("riptide.proofshotter")

# ── Config ───────────────────────────────────────────────────────────────────

WATCHED_REPOS = [
    r.strip()
    for r in os.environ.get(
        "RIPTIDE_WATCHED_REPOS",
        "ChonSong/riptide,ChonSong/hermes-webui,ChonSong/hermes-webui-extensions,"
        "ChonSong/seans-reporepo,ChonSong/pr-review,ChonSong/everything-claude-code,"
        "codeovertcp/gto-wizard-clone-v2,nesquena/hermes-webui",
    ).split(",")
    if r.strip()
]

OUR_USERNAME = os.environ.get("RIPTIDE_OUR_USERNAME", "ChonSong")
OUR_ORG = os.environ.get("RIPTIDE_OUR_ORG", "ChonSong")
STALENESS_MINUTES = int(os.environ.get("RIPTIDE_PROOFSHOT_STALENESS_MINUTES", "10"))
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_PROOFSHOT_MODEL", "qwen2.5-coder:7b")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_PROOFSHOT_TIMEOUT", "15"))

STATE_FILE = Path(
    os.environ.get("RIPTIDE_DATA_DIR", "/tmp/riptide-data")
) / "proofshotter_acted_prs.json"

PROOFSHOT_CLI = Path(
    os.environ.get("RIPTIDE_PROOFSHOT_CLI", "/home/sc/workspace/proofshot/cli.py")
)
PROOFSHOT_ROOT = PROOFSHOT_CLI.parent

# UI file extensions that trigger ProofShot
UI_EXTENSIONS = {".css", ".scss", ".less", ".html", ".jsx", ".tsx", ".vue", ".svelte", ".astro", ".svg"}

PROOFSHOT_MARKER = "\U0001f4f8 ProofShot"


# ── State ──────────────────────────────────────────────────────────────────────


def _load_state() -> dict[str, dict]:
    """Load processed PR state: {owner/repo#number: {head_sha, reviewed_at, triggered_by}}."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(state: dict[str, dict]):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def was_visualized(owner: str, repo: str, pr_number: int, head_sha: str) -> bool:
    """Check if a PR's head SHA has already been visualized (dedup gate)."""
    state = _load_state()
    pr_key = f"{owner}/{repo}#{pr_number}"
    return state.get(pr_key, {}).get("head_sha") == head_sha


def mark_visualized(owner: str, repo: str, pr_number: int, head_sha: str, triggered_by: str = "webhook"):
    """Mark a PR's head SHA as visualized."""
    state = _load_state()
    pr_key = f"{owner}/{repo}#{pr_number}"
    state[pr_key] = {
        "head_sha": head_sha,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "triggered_by": triggered_by,
    }
    _save_state(state)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _check_proofshot_config(owner: str, repo: str, pr_number: int, head_sha: str) -> Optional[dict]:
    """Fetch proofshot.config.json from the PR's head SHA via GitHub API.

    Returns parsed JSON on success, None if the file does not exist or is invalid.
    """
    result = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/contents/proofshot.config.json?ref={head_sha}",
         "--jq", ".content"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        return None
    try:
        raw = base64.b64decode(result.stdout.strip()).decode("utf-8")
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("  Failed to parse proofshot.config.json for #%d: %s", pr_number, exc)
        return None


def _checkout_pr(owner: str, repo: str, pr_number: int) -> Optional[Path]:
    """Clone and checkout the PR branch into /tmp/proofshot-pr-{owner}-{repo}-N/.

    Returns the working directory Path on success, None on failure.
    """
    work_dir = Path(f"/tmp/proofshot-pr-{owner}-{repo}-{pr_number}")
    try:
        if not (work_dir / ".git").exists():
            work_dir.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["git", "clone", "--depth", "1",
                 f"https://github.com/{owner}/{repo}.git", str(work_dir)],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                log.warning("  git clone failed for #%d: %s", pr_number, result.stderr[:200])
                return None

        # Fetch and checkout the PR head — verify each step
        result = subprocess.run(
            ["git", "fetch", "origin", f"pull/{pr_number}/head"],
            cwd=work_dir, capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            log.warning("  git fetch failed for #%d: %s", pr_number, result.stderr[:200])
            return None

        result = subprocess.run(
            ["git", "checkout", "-B", f"pr-{pr_number}", "FETCH_HEAD"],
            cwd=work_dir, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.warning("  git checkout failed for #%d: %s", pr_number, result.stderr[:200])
            return None

        return work_dir
    except Exception as exc:
        log.warning("  Checkout failed for #%d: %s", pr_number, exc)
        return None


def _run_proofshot(
    pr_number: int,
    url: str,
    seed_path: Optional[str],
    output_dir: Path,
    captures: list[dict],
) -> Optional[dict]:
    """Run the proofshot visual verification workflow.

    If captures are defined in proofshot.config.json, drives the session manually
    for each capture step. Otherwise uses the default 'proofshot pr' workflow.

    Returns {'gif': str} on success, None on failure.
    """
    if not PROOFSHOT_CLI.exists():
        log.warning("  Proofshot CLI not found at %s — skipping verification", PROOFSHOT_CLI)
        return None

    if captures:
        return _run_proofshot_custom(pr_number, url, seed_path, output_dir, captures)
    else:
        return _run_proofshot_default(pr_number, url, seed_path, output_dir)


def _run_proofshot_default(
    pr_number: int,
    url: str,
    seed_path: Optional[str],
    output_dir: Path,
) -> Optional[dict]:
    """Run the default proofshot PR workflow via the CLI."""
    cmd = [
        sys.executable or "python3",
        str(PROOFSHOT_CLI),
        "pr", str(pr_number),
        "--url", url,
        "--output", str(output_dir),
    ]
    if seed_path:
        cmd.extend(["--seed", seed_path])

    log.info("  Running: proofshot pr %d --url %s --output %s", pr_number, url, output_dir)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        log.warning("  proofshot pr failed (exit %d): %s", result.returncode, result.stderr[:300])
        return None

    gif_path = output_dir / "proofshot.gif"
    if gif_path.exists():
        return {"gif": str(gif_path)}

    log.warning("  proofshot did not produce a GIF at %s", gif_path)
    return None


def _run_proofshot_custom(
    pr_number: int,
    url: str,
    seed_path: Optional[str],
    output_dir: Path,
    captures: list[dict],
) -> Optional[dict]:
    """Drive ProofshotSession manually for custom capture sequences.

    Builds and runs an inline Python script that uses the ProofshotSession class
    from the proofshot CLI module, following each capture defined in the config.
    """
    captures_json = json.dumps(captures)
    seed_repr = json.dumps(seed_path) if seed_path else "None"

    script = (
        "import json, sys, time; "
        f"sys.path.insert(0, {json.dumps(str(PROOFSHOT_ROOT))}); " \
        "from cli import ProofshotSession; "
        f"s = ProofshotSession({json.dumps(url)}, {json.dumps(str(output_dir))}, {seed_repr}); "
        "s.start(); "
        f"caps = {captures_json}; "
        "for c in caps:\n"
        "    time.sleep(c.get('wait', 0) / 1000);\n"
        "    s.capture(selector=c.get('selector'), output=f\"{c['name']}.png\");\n"
        "r = s.stop(gif_output='proofshot.gif');\n"
        "print(json.dumps({'gif': r.get('gif')}))"
    )

    log.info("  Running custom proofshot for #%d (%d captures)", pr_number, len(captures))
    result = subprocess.run(
        [sys.executable or "python3", "-c", script],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        log.warning("  Custom proofshot failed (exit %d): %s", result.returncode, result.stderr[:300])
        return None

    try:
        data = json.loads(result.stdout.strip())
        if data.get("gif"):
            return data
        log.warning("  Custom proofshot produced no GIF output")
        return None
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("  Failed to parse custom proofshot output: %s", exc)
        return None


def _upload_gif(gif_path: str, pr_number: int, commit_sha: str = "") -> Optional[str]:
    """Upload GIF to the grafiphy-assets GitHub release and return its download URL."""
    try:
        asset_name = f"pr{pr_number}-{commit_sha[:8]}-proofshot.gif" if commit_sha else f"pr{pr_number}-proofshot.gif"

        # Ensure the grafiphy-assets release exists
        result = subprocess.run(
            ["gh", "release", "view", "grafiphy-assets", "--json", "url"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            subprocess.run(
                ["gh", "release", "create", "grafiphy-assets",
                 "--title", "Grafiphy Assets",
                 "--notes", "Auto-generated visual evidence for PR reviews"],
                capture_output=True, timeout=30,
            )

        # Upload with --clobber to overwrite existing asset of the same name
        result = subprocess.run(
            ["gh", "release", "upload", "grafiphy-assets", gif_path, "--clobber"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            log.warning("  Upload failed: %s", result.stderr[:200])
            return None

        # Resolve the download URL from the release assets
        result = subprocess.run(
            ["gh", "release", "view", "grafiphy-assets", "--json", "assets"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            assets = json.loads(result.stdout).get("assets", [])
            for asset in assets:
                if asset["name"] == asset_name:
                    return asset["url"]
        return None
    except Exception as exc:
        log.warning("  Upload error: %s", exc)
        return None


def _post_proofshot_comment(
    owner: str, repo: str, pr_number: int,
    gif_url: str, commit_sha: str = "", commit_message: str = "",
) -> bool:
    """Post the ProofShot visual evidence comment on the PR."""
    body_parts = [
        f"## {PROOFSHOT_MARKER} Visual Evidence\n",
    ]
    if commit_sha:
        # Shorten commit message to first line, max 60 chars
        msg = commit_message.split("\n")[0][:60] if commit_message else ""
        body_parts.append(f"**Commit `{commit_sha[:8]}`:** {msg}\n")
    body_parts.append("ProofShot visual verification completed for the UI changes in this PR.\n")
    body_parts.append(f"![ProofShot GIF]({gif_url})\n")

    body_parts.append(
        "\n---\n"
        "<sub>\U0001f916 Generated by Riptide · ProofShot visual verification</sub>"
    )

    body = "".join(body_parts)
    result = subprocess.run(
        ["gh", "pr", "comment", str(pr_number), "--repo", f"{owner}/{repo}", "--body", body],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        log.warning("  Failed to post comment on #%d: %s", pr_number, result.stderr[:200])
        return False
    log.info("  ✓ ProofShot comment posted on %s/%s#%d", owner, repo, pr_number)
    return True


# ── Per-commit diff mapping ────────────────────────────────────────────────────


def _get_commit_file_map(owner: str, repo: str, pr_number: int) -> list[dict]:
    """Get per-commit file change map for a PR.

    Returns list of {sha, message, files, ui_files} for each commit,
    ordered oldest → newest.
    """
    # Get commit list with SHAs and messages
    commits_result = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/pulls/{pr_number}/commits",
         "--jq", ".[].{sha: .sha, message: .commit.message}"],
        capture_output=True, text=True, timeout=30,
    )
    if commits_result.returncode != 0:
        log.warning("  Failed to fetch commits for %s/%s#%d", owner, repo, pr_number)
        return []

    commits = []
    for line in commits_result.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            commits.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # For each commit, get its files
    for commit in commits:
        sha = commit["sha"]
        files_result = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/commits/{sha}",
             "--jq", ".files[].filename"],
            capture_output=True, text=True, timeout=30,
        )
        if files_result.returncode != 0:
            commit["files"] = []
            commit["ui_files"] = []
            continue

        all_files = [f for f in files_result.stdout.strip().split("\n") if f]
        ui_files = [f for f in all_files if f.endswith(tuple(UI_EXTENSIONS))]
        commit["files"] = all_files
        commit["ui_files"] = ui_files

    return commits


# ── Lightweight LLM gate ──────────────────────────────────────────────────────


def _should_run_visual(
    owner: str,
    repo: str,
    pr_number: int,
    title: str,
    body: str,
    ui_files: list[str],
    commit_messages: list[str],
) -> tuple[bool, str]:
    """Ask a lightweight LLM whether this PR needs visual verification.

    Returns (should_run: bool, reason: str).
    If the LLM is unreachable, defaults to True (safe: run it anyway).
    """
    # Build a compact summary for the LLM
    msg_blob = " | ".join(m.split("\n")[0][:40] for m in commit_messages[:5])
    diff_snippet = ""
    try:
        # Get first ~600 chars of actual diff for UI files
        diff_result = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/pulls/{pr_number}/files",
             "--jq", ".[] | select(.filename as $f | $ui | index($f) != null) | .patch[:200]",
             "--paginate"],
            capture_output=True, text=True, timeout=30,
        )
        if diff_result.returncode == 0:
            diff_snippet = diff_result.stdout[:800]
    except Exception:
        pass

    prompt = (
        "Review this PR. Decide if visual verification is needed.\n\n"
        f"Title: {title}\n"
        f"Body: {(body or '')[:300]}\n"
        f"UI files: {', '.join(ui_files[:10])}\n"
        f"Commits: {msg_blob}\n"
        f"Diff excerpt:\n{diff_snippet}\n\n"
        "Answer EXACTLY: YES — <one line reason> or NO — <one line reason>"
    )

    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 80, "temperature": 0.2},
            },
            timeout=OLLAMA_TIMEOUT,
        )
        if resp.status_code == 200:
            text = resp.json().get("response", "").strip()
            log.info("  LLM gate response for #%d: %s", pr_number, text[:100])
            upper = text.upper()
            if upper.startswith("YES"):
                return True, text
            elif upper.startswith("NO"):
                return False, text
            else:
                # Ambiguous — safe default
                log.warning("  LLM gate ambiguous for #%d: %s", pr_number, text[:60])
                return True, f"Ambiguous LLM response: {text[:60]}"
    except requests.exceptions.Timeout:
        log.warning("  LLM gate timeout for #%d — defaulting to YES", pr_number)
    except Exception as exc:
        log.warning("  LLM gate failed for #%d: %s — defaulting to YES", pr_number, exc)

    return True, "LLM gate unreachable — safe default (YES)"


# ── Main entry point ──────────────────────────────────────────────────────────


def run():
    """Poll watched repos and run ProofShot on qualifying PRs."""
    state = _load_state()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=STALENESS_MINUTES)
    triggered = 0
    skipped_no_ui = 0
    skipped_stale = 0
    skipped_draft = 0
    skipped_dedup = 0
    skipped_llm = 0
    skipped_error = 0

    for repo_full in WATCHED_REPOS:
        try:
            owner, repo_name = repo_full.split("/", 1)
        except ValueError:
            log.warning("  Invalid repo format: %s", repo_full)
            continue

        log.info("Checking %s...", repo_full)

        # ── Get open PRs via gh CLI ──────────────────────────────────────
        prs = subprocess.run(
            ["gh", "pr", "list", "--repo", repo_full, "--state", "open",
             "--json", "number,title,headRefName,headRefOid,author,"
                       "additions,deletions,createdAt,updatedAt,url,state,isDraft",
             "--limit", "50"],
            capture_output=True, text=True, timeout=30,
        )
        if prs.returncode != 0:
            log.warning("  gh pr list failed for %s: %s", repo_full, prs.stderr[:200])
            continue

        try:
            open_prs = json.loads(prs.stdout)
        except json.JSONDecodeError:
            log.warning("  JSON parse failed for %s", repo_full)
            continue

        for pr in open_prs:
            pr_number = pr["number"]
            pr_title = pr.get("title", "")
            pr_author = pr.get("author", {}).get("login", "")
            head_sha = pr.get("headRefOid", "")
            updated_at_str = pr.get("updatedAt", "")
            is_draft = pr.get("isDraft", False)

            # ── Filter: skip draft PRs ──────────────────────────────────
            if is_draft:
                log.info("  #%d skip — draft PR", pr_number)
                skipped_draft += 1
                continue

            pr_key = f"{repo_full}#{pr_number}"

            # ── Dedup: same head SHA already processed ──────────────────
            if state.get(pr_key, {}).get("head_sha") == head_sha:
                log.info("  #%d skip — already processed (SHA %s)", pr_number, head_sha[:12])
                skipped_dedup += 1
                continue

            # ── Filter: staleness (let the author push more commits) ────
            try:
                updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                log.warning("  #%d skip — can't parse updatedAt: %s", pr_number, updated_at_str)
                skipped_stale += 1
                continue

            if updated_at > cutoff:
                log.info("  #%d skip — last updated %s, not yet stale", pr_number, updated_at_str)
                skipped_stale += 1
                continue

            # ── Filter: check for UI file changes ───────────────────────
            files_result = subprocess.run(
                ["gh", "api", f"repos/{owner}/{repo_name}/pulls/{pr_number}/files",
                 "--jq", ".[].filename", "--paginate"],
                capture_output=True, text=True, timeout=30,
            )
            if files_result.returncode != 0:
                log.warning("  #%d skip — can't fetch files: %s",
                            pr_number, files_result.stderr[:200])
                skipped_error += 1
                continue

            all_files = [f for f in files_result.stdout.strip().split("\n") if f]
            ui_files = [f for f in all_files if f.endswith(tuple(UI_EXTENSIONS))]

            if not ui_files:
                log.info("  #%d skip — no UI files changed", pr_number)
                skipped_no_ui += 1
                continue

            # ── LLM gate: does this PR need visual verification? ────────
            commit_map = _get_commit_file_map(owner, repo_name, pr_number)
            commit_messages = [c.get("message", "") for c in commit_map]

            should_run, reason = _should_run_visual(
                owner, repo_name, pr_number, pr_title, "",
                ui_files, commit_messages,
            )
            if not should_run:
                log.info("  #%d skip — LLM gate: %s", pr_number, reason[:80])
                skipped_llm += 1
                continue

            # ── All filters passed — run proofshot per commit ───────────
            log.info(
                "  #%d TRIGGER — UI files: %s, LLM: %s",
                pr_number, ", ".join(ui_files[:5]), reason[:50],
            )

            # Checkout PR branch for seed file resolution
            work_dir = _checkout_pr(owner, repo_name, pr_number)
            if work_dir is None:
                log.warning("  #%d failed to checkout — skipping", pr_number)
                skipped_error += 1
                continue

            # Check for proofshot.config.json (optional enrichment)
            config = _check_proofshot_config(owner, repo_name, pr_number, head_sha)
            if config is None:
                log.info("  #%d no proofshot.config.json — using defaults", pr_number)
                config = {"url": "http://localhost:8788", "captures": []}

            # Resolve seed path (relative to repo root in the checkout)
            seed_path: Optional[str] = None
            raw_seed = config.get("seed")
            if raw_seed:
                candidate = work_dir / raw_seed
                if candidate.exists():
                    seed_path = str(candidate.resolve())
                else:
                    log.warning("  Seed file %s not found at %s", raw_seed, candidate)

            url = config.get("url", "http://localhost:8788")
            captures = config.get("captures", [])

            # Filter to only commits with UI changes
            ui_commits = [c for c in commit_map if c["ui_files"]]

            # Run proofshot for each commit that touched UI files
            for commit in ui_commits:
                commit_sha = commit["sha"]
                commit_msg = commit.get("message", "")
                output_dir = Path(f"/tmp/proofshot-pr-{owner}-{repo_name}-{pr_number}-{commit_sha[:8]}")
                output_dir.mkdir(parents=True, exist_ok=True)

                log.info(
                    "  Capturing commit %s (%s)",
                    commit_sha[:8], ", ".join(commit["ui_files"][:3]),
                )

                result = _run_proofshot(pr_number, url, seed_path, output_dir, captures)
                if result is None:
                    log.warning("  #%d commit %s proofshot failed — skipping", pr_number, commit_sha[:8])
                    skipped_error += 1
                    continue

                # Upload GIF to release assets
                gif_url = _upload_gif(result["gif"], pr_number, commit_sha)
                if gif_url is None:
                    log.warning("  #%d commit %s upload failed", pr_number, commit_sha[:8])
                    skipped_error += 1
                    continue

                # Post the evidence comment
                if _post_proofshot_comment(
                    owner, repo_name, pr_number, gif_url,
                    commit_sha=commit_sha, commit_message=commit_msg,
                ):
                    triggered += 1

            # Mark PR as processed (full head SHA)
            if triggered > 0:
                mark_visualized(owner, repo_name, pr_number, head_sha, triggered_by="poll")

    # ── Summary ─────────────────────────────────────────────────────────
    log.info(
        "Done. Triggered=%d, skipped(draft)=%d, skipped(stale)=%d, "
        "skipped(no-UI)=%d, "
        "skipped(dedup)=%d, skipped(LLM)=%d, skipped(error)=%d",
        triggered, skipped_draft, skipped_stale,
        skipped_no_ui,
        skipped_dedup, skipped_llm, skipped_error,
    )


# ── Manual override command ──────────────────────────────────────────────────

PROOFSHOT_RE = re.compile(
    r"@riptide-bot\s+(proofshot|visual|capture|proof)",
    re.IGNORECASE,
)


def handle_manual_command(
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
) -> Optional[str]:
    """Handle @riptide-bot proofshot manual override — bypass dedup.

    Returns reply text on success/failure, None if PR is not visualizable.
    """
    log.info("Manual proofshot requested by %s on %s/%s#%d", commenter, owner, repo, pr_number)

    # Get PR details
    pr_result = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--repo", f"{owner}/{repo}",
         "--json", "headRefOid,title,state,isDraft"],
        capture_output=True, text=True, timeout=30,
    )
    if pr_result.returncode != 0:
        return f"❌ Could not fetch PR #{pr_number} details."

    pr_data = json.loads(pr_result.stdout)
    head_sha = pr_data.get("headRefOid", "")
    pr_title = pr_data.get("title", "")
    is_draft = pr_data.get("isDraft", False)

    if is_draft:
        return "❌ Cannot run ProofShot on a draft PR."

    # Get PR files
    files_result = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/pulls/{pr_number}/files",
         "--jq", ".[].filename", "--paginate"],
        capture_output=True, text=True, timeout=30,
    )
    if files_result.returncode != 0:
        return f"❌ Could not fetch PR #{pr_number} files."

    all_files = [f for f in files_result.stdout.strip().split("\n") if f]
    ui_files = [f for f in all_files if f.endswith(tuple(UI_EXTENSIONS))]

    if not ui_files:
        return "📷 No UI files changed in this PR — nothing to capture."

    # Checkout
    work_dir = _checkout_pr(owner, repo, pr_number)
    if work_dir is None:
        return "❌ Failed to checkout PR branch."

    # Config
    config = _check_proofshot_config(owner, repo, pr_number, head_sha)
    if config is None:
        config = {"url": "http://localhost:8788", "captures": []}

    # Seed
    seed_path: Optional[str] = None
    raw_seed = config.get("seed")
    if raw_seed:
        candidate = work_dir / raw_seed
        if candidate.exists():
            seed_path = str(candidate.resolve())

    url = config.get("url", "http://localhost:8788")
    captures = config.get("captures", [])
    output_dir = Path(f"/tmp/proofshot-pr-{owner}-{repo}-{pr_number}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Run
    result = _run_proofshot(pr_number, url, seed_path, output_dir, captures)
    if result is None:
        return "❌ ProofShot capture failed. Check service logs."

    # Upload
    gif_url = _upload_gif(result["gif"], pr_number)
    if gif_url is None:
        return "❌ Failed to upload GIF."

    # Post
    _post_proofshot_comment(owner, repo, pr_number, gif_url)

    # Mark as visualized (manual trigger)
    mark_visualized(owner, repo, pr_number, head_sha, triggered_by="manual")

    return f"📸 ProofShot complete — visual evidence posted on PR #{pr_number}."


if __name__ == "__main__":
    run()
