#!/usr/bin/env python3
"""
deepthink.py — Bot 2: Riptide Review (autonomous deep-think PR analysis).

Polls open PRs and spawns Hermes deep-think sessions when:
  1. PR has total changes (additions + deletions) > 100 LOC
  2. PR hasn't been updated in >= 30 minutes (settled)
  3. Either we own the repo (ChonSong org) OR we authored the PR

Also handles on-demand @riptide-bot review commands via handle_review_command(),
called directly from webhook.py when a user comments @riptide-bot review on a PR.

Dedup: tracks pr_number + head_sha to avoid re-spawning on the same revision.
Uses `gh` CLI (already authenticated as ChonSong) for all GitHub queries.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("riptide.deepthink")

# ── Config ───────────────────────────────────────────────────────────────────

REVIEW_RE = re.compile(r"@riptide-bot\s+(review|deepthink|full\s*review)", re.IGNORECASE)

WATCHED_REPOS = [
    r.strip()
    for r in os.environ.get(
        "RIPTIDE_WATCHED_REPOS",
        "ChonSong/riptide,ChonSong/hermes-webui,ChonSong/hermes-webui-extensions,ChonSong/seans-reporepo,ChonSong/pr-review,ChonSong/everything-claude-code,codeovertcp/gto-wizard-clone-v2,nesquena/hermes-webui",
    ).split(",")
    if r.strip()
]

OUR_USERNAME = os.environ.get("RIPTIDE_OUR_USERNAME", "ChonSong")
OUR_ORG = os.environ.get("RIPTIDE_OUR_ORG", "ChonSong")
STALENESS_MINUTES = int(os.environ.get("RIPTIDE_STALENESS_MINUTES", "30"))
MIN_LOC_CHANGED = int(os.environ.get("RIPTIDE_MIN_LOC_CHANGED", "100"))

STATE_FILE = Path(
    os.environ.get("RIPTIDE_DATA_DIR", "/tmp/riptide-data")
) / "deepthink_acted_prs.json"


def _load_state() -> dict[str, dict]:
    """Load processed PR state: {owner/repo#number: {head_sha, reviewed_at}}"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(state: dict[str, dict]):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _was_reviewed_today(owner: str, repo: str, pr_number: int) -> bool:
    """Check if this PR was reviewed in the last 24 hours."""
    pr_key = f"{owner}/{repo}#{pr_number}"
    state = _load_state()
    entry = state.get(pr_key, {})
    reviewed_at = entry.get("reviewed_at", "")
    if not reviewed_at:
        return False
    try:
        reviewed_time = datetime.fromisoformat(reviewed_at)
        return (datetime.now(timezone.utc) - reviewed_time) < timedelta(hours=24)
    except (ValueError, TypeError):
        return False


def handle_review_command(
    client,
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
) -> str | None:
    """Handle @riptide-bot review command — spawn an on-demand deep-think review.

    Called from webhook.py when a user comments @riptide-bot review on a PR.
    Fetches PR details via GitHub API client, spawns the deep-think session,
    and returns a user-facing confirmation message (or error message).
    """
    try:
        pr_details = client.get_pr_details(installation_id, owner, repo, pr_number)
    except Exception as e:
        log.warning("Failed to fetch PR details for review: %s", e)
        return (
            f"⚠️ Could not fetch PR #{pr_number} details ({e}). "
            f"Make sure the PR exists and the app is installed."
        )

    title = pr_details.get("title", f"PR #{pr_number}")
    author = pr_details.get("user", {}).get("login", "unknown")
    additions = pr_details.get("additions", 0)
    deletions = pr_details.get("deletions", 0)
    total_loc = additions + deletions
    head_sha = pr_details.get("head", {}).get("sha", "")

    try:
        _spawn_deepthink(owner, repo, pr_number, title, author, total_loc, head_sha)
    except Exception as e:
        log.error("Failed to spawn deep-think: %s", e)
        return f"⚠️ Failed to spawn deep-think review for #{pr_number}: {e}"

    log.info("On-demand review spawned for %s/%s#%d by %s", owner, repo, pr_number, commenter)
    return (
        f"🧠 **Riptide Review triggered for #{pr_number}!**\n\n"
        f"A Hermes deep-think session has been scheduled and will begin within 2 minutes. "
        f"The review will analyze the full diff, run graphify blast-radius analysis, "
        f"post inline suggestions, and generate an Excalidraw architecture diagram.\n\n"
        f"**PR:** {title}\n"
        f"**Author:** @{author}\n"
        f"**Changes:** +{additions}/-{deletions} ({total_loc} LOC)\n"
        f"**Commit:** `{head_sha[:12]}`"
    )


def _is_cron_available() -> bool:
    """Check that `hermes cron create` works."""
    result = subprocess.run(
        ["which", "hermes"], capture_output=True, text=True, timeout=5
    )
    return bool(result.returncode == 0 and result.stdout.strip())


def _spawn_deepthink(
    owner: str,
    repo: str,
    pr_number: int,
    pr_title: str,
    pr_author: str,
    total_loc: int,
    head_sha: str,
) -> bool:
    """Spawn a Hermes cron session for deep-think review on this PR.

    Retries up to 3 times with exponential backoff (5s/15s/30s).
    Only records state on successful spawn.
    Returns True if spawned successfully, False otherwise.
    """
    max_retries = 3
    base_delay = 5  # seconds
    name = f"riptide-review-{owner}-{repo}-{pr_number}"
    run_at = (datetime.now() + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")

    prompt = (
        f"PR #{pr_number} in {owner}/{repo} has >100 LOC changed ({total_loc} LOC total) "
        f"and has been stable for 30+ minutes. You are performing a **Riptide Review** — "
        f"an autonomous deep-think code review.\n\n"
        f"## PR Details\n"
        f"- Title: {pr_title}\n"
        f"- Author: {pr_author}\n"
        f"- Changes: {total_loc} LOC\n"
        f"- HEAD SHA: {head_sha[:12]}\n\n"
        f"## Your Task\n"
        f"You are a senior engineer. Use **graphify** to understand the current implementation "
        f"and **deep-think** to advise next steps.\n\n"
        f"### Step 1: Understand the Implementation (Graphify)\n"
        f"1. Fetch the PR diff: `gh pr diff {pr_number} --repo {owner}/{repo}`\n"
        f"2. If this repo has graphify-out/ (check ~/workspace/{repo}/graphify-out/), run:\n"
        f"   `cd ~/workspace/{repo} && graphify update . && graphify query 'what does this PR affect?'`\n"
        f"3. Read the GRAPH_REPORT.md or graphify analysis for cross-file relationships.\n\n"
        f"### Step 2: Deep-Think Analysis (Mandatory)\n"
        f"Load the deep-think skill with `skill_view('deep-think')` and run the full loop:\n"
        f"1. SURFACE — Restate what this PR changes and why.\n"
        f"2. EXPLORE — What code paths are affected? Edge cases? Blast radius from graphify?\n"
        f"3. CHALLENGE — Could this change have side effects? Simpler approach? Missing tests?\n"
        f"4. SYNTHESIZE — Advise next steps (approve, request changes, suggest follow-ups).\n"
        f"5. VALIDATE — If claims are testable, run the test suite.\n\n"
        f"### Step 3: Post Inline Review Comments with Suggestions\n"
        f"For each substantive issue, post an **inline review comment** with a **GitHub suggestion block** "
        f"so the author can apply your change with one click.\n\n"
        f"**Suggestion format** (wrap proposed code in triple backticks with `suggestion` language tag):\n"
        f"```suggestion\n"
        f"proposed new code here\n"
        f"```\n\n"
        f"**To post an inline comment:**\n"
        f"```\n"
        f"gh api repos/{owner}/{repo}/pulls/{pr_number}/comments \\\\\n"
        f"  --method POST \\\\\n"
        f"  -f body='**🔴 Critical:** explanation here\\\\n```suggestion\\\\nproposed code here\\\\n```' \\\\\n"
        f"  -f commit_id='{head_sha}' \\\\\n"
        f"  -f path='<file_path>' \\\\\n"
        f"  -F line=<line_number> \\\\\n"
        f"  -f side='RIGHT'\n"
        f"```\n\n"
        f"**How to calculate line numbers:**\n"
        f"Parse the `@@` hunk headers from `gh pr diff`. Each hunk looks like "
        f"`@@ -old_start,old_count +new_start,new_count @@`. The `new_start` is the first "
        f"line number in the modified file. Count forward from there for added/modified lines.\n\n"
        f"**Comment format:**\n"
        f"```suggestion\n"
        f"proposed replacement code\n"
        f"```\n"
        f"Prepend a severity marker before the suggestion:\n"
        f"- `**CRITICAL:**` — definite bug, security issue, data loss risk\n"
        f"- `**WARNING:**` — potential issue, performance concern, code smell\n"
        f"- `**SUGGESTION:**` — style improvement, minor refactor, nitpick\n\n"
        f"Post inline comments for substantive findings only (1-3 per PR maximum). "
        f"Focus on real issues — do not comment on every line or nitpick style.\n\n"
        f"### Step 4: Generate Excalidraw Diagram\n"
        f"After all inline review comments are posted, generate an Excalidraw diagram "
        f"visualizing your findings using the **excalidraw_renderer** module.\n\n"
        f"The diagram is a flowing narrative: Codebase Landscape (all modules with PR files "
        f"highlighted) -> PR Scope -> Graphify Analysis (god nodes + communities) -> "
        f"Code Chunks with WHY -> Human-Readable Narrative -> Findings with Severity -> "
        f"Suggested Changes -> Legend. All sections connected by arrows.\n\n"
        f"```python\n"
        f"import sys\n"
        f"sys.path.insert(0, '/home/sc/workspace')\n"
        f"from riptide.grafiphy.excalidraw_renderer import render_review, upload_excalidraw\n"
        f"\n"
        f"findings = [\n"
        f"    dict(severity='critical', title='...', detail='...', file='...', line=...),\n"
        f"    dict(severity='warning', title='...', detail='...', file='...'),\n"
        f"]\n"
        f"\n"
        f"graph_data = dict(\n"
        f"    god_nodes=[dict(name=..., edges=..., why=...)],\n"
        f"    communities=[dict(name=..., members=[...], why=...)],\n"
        f")\n"
        f"\n"
        f"# NEW: Collect full repo graph for codebase landscape\n"
        f"repo_graph = [\n"
        f"    dict(name='routes.py', type='module', file='api/routes.py', why='HTTP routing'),\n"
        f"    dict(name='models.py', type='module', file='api/models.py', why='Core data models'),\n"
        f"]\n"
        f"\n"
        f"# NEW: Suggestions from inline review comments (Bot 2)\n"
        f"suggestions = [\n"
        f"    dict(file='api/draft.py', line=42, old_code='old', new_code='new',\n"
        f"         severity='critical', reasoning='Avoids null pointer'),\n"
        f"]\n"
        f"\n"
        f"# NEW: Distance map for network-radius layout\n"
        f"distance_map = {{\n"
        f"    'companion.py': dict(hops=0, relation='epicenter', community='github_webhook', degree=0),\n"
        f"    'webhook.py': dict(hops=1, relation='affected by companion.py', community='github_webhook', degree=8),\n"
        f"}}\n"
        f"\n"
        f"url = upload_excalidraw(\n"
        f"    render_review(\n"
        f"        pr_data=dict(number={pr_number}, title=\"{pr_title[:50]}\",\n"
        f"                    repo=\"{owner}/{repo}\", loc={total_loc}),\n"
        f"        findings=findings,\n"
        f"        graph_data=graph_data,\n"
        f"        repo_graph=repo_graph,     # NEW: landscape view\n"
        f"        suggestions=suggestions,   # NEW: suggestion blocks\n"
        f"        distance_map=distance_map, # NEW: distance-radius layout\n"
        f"        output_path='/tmp/review.excalidraw',\n"
        f"    )\n"
        f")\n"
        f"print(f'Excalidraw: {{url}}')\\n"
        f"```\n\n"
        f"The renderer creates 9 connected sections: distance-radius network map (nodes arranged "
        f"by network distance from PR changes), codebase landscape (all modules with "
        f"PR files highlighted), PR scope, graphify analysis (god nodes + communities with WHY), "
        f"code chunks with detailed WHY, human-readable narrative, findings with severity colors, "
        f"suggested changes (code diffs), and legend.\n"
        f"Include the returned URL in your summary.\n\n"
        f"### Step 5: Post Summary Review\n"
        f"After posting all inline comments and generating the Excalidraw, post a **summary review**:\n"
        f"`gh pr comment {pr_number} --repo {owner}/{repo} --body '<review>'`\n\n"
        f"Structure your review as:\n"
        f"- **Summary**: What this PR does (1-2 sentences, no filler)\n"
        f"- **Findings**: Only real issues (not style nits or hypotheticals)\n"
        f"- **Inline Comments**: N findings posted (see specific lines)\n"
        f"- **Excalidraw**: Link to visual evidence diagram\n"
        f"- **Next Steps**: Specific actionable advice (max 3 items)\n\n"
        f"**Quality gate**: If you have no critical/warning findings, say so briefly — "
        f"do not invent problems or pad the review.\n\n"
        f"**Sign-off**: End your summary with:\n"
        f"```\n"
        f"---\n"
        f"<sub>Riptide Review via Hermes</sub>\n"
        f"```\n\n"
        f"REPO PATH: ~/workspace/{repo}/\n"
    )

    cmd = [
        "hermes", "cron", "create", run_at,
        prompt,
        "--name", name,
        "--skill", "github-pr-lifecycle",
        "--skill", "deep-think",
        "--skill", "excalidraw",
        "--deliver", "origin",
    ]

    for attempt in range(max_retries):
        if attempt > 0:
            delay = base_delay * (2 ** attempt)  # 5s, 10s, 20s
            log.info(f"Retry {attempt+1}/{max_retries} for {owner}/{repo}#{pr_number} in {delay}s...")
            time.sleep(delay)

        # Check hermes availability before each attempt
        if not _is_cron_available():
            log.warning(f"hermes not available on attempt {attempt+1} for {owner}/{repo}#{pr_number}")
            continue

        log.info(f"Spawning: hermes cron create {run_at} --name {name} (attempt {attempt+1})")
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                log.info(f"✓ Spawned deep-think for {owner}/{repo}#{pr_number}: {result.stdout[:200]}")
                return True
            else:
                log.error(f"✗ Spawn failed (attempt {attempt+1}): {result.stderr[:300]}")
        except subprocess.TimeoutExpired:
            log.warning(f"Timeout spawning deep-think (attempt {attempt+1})")
        except Exception as e:
            log.error(f"Error spawning deep-think (attempt {attempt+1}): {e}")

    log.error(f"All {max_retries} attempts failed for {owner}/{repo}#{pr_number}")
    return False


def run():
    """Poll watched repos and spawn deep-think sessions on qualifying PRs."""
    if not _is_cron_available():
        log.error("hermes binary not found — can't spawn sessions")
        sys.exit(1)

    state = _load_state()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=STALENESS_MINUTES)
    triggered = 0
    skipped_stale = 0
    skipped_loc = 0
    skipped_ownership = 0
    skipped_dedup = 0

    for repo_full in WATCHED_REPOS:
        owner, repo_name = repo_full.split("/", 1)
        log.info(f"Checking {repo_full}...")

        # Get open PRs via gh CLI
        prs = subprocess.run(
            ["gh", "pr", "list", "--repo", repo_full, "--state", "open",
             "--json", "number,title,headRefName,headRefOid,author,additions,deletions,createdAt,updatedAt,url,state",
             "--limit", "50"],
            capture_output=True, text=True, timeout=30,
        )
        if prs.returncode != 0:
            log.warning(f"  gh pr list failed for {repo_full}: {prs.stderr[:200]}")
            continue

        try:
            open_prs = json.loads(prs.stdout)
        except json.JSONDecodeError:
            log.warning(f"  JSON parse failed for {repo_full}")
            continue

        for pr in open_prs:
            pr_number = pr["number"]
            pr_title = pr.get("title", "")
            pr_author = pr.get("author", {}).get("login", "")
            total_loc = pr.get("additions", 0) + pr.get("deletions", 0)
            updated_at_str = pr.get("updatedAt", "")
            head_sha = pr.get("headRefOid", "")

            # Filter 3: Ownership
            if owner != OUR_ORG and pr_author != OUR_USERNAME:
                log.info(f"  #{pr_number} skip — not our repo ({owner}) nor our PR ({pr_author})")
                skipped_ownership += 1
                continue

            # Filter 1: LOC
            if total_loc <= MIN_LOC_CHANGED:
                log.info(f"  #{pr_number} skip — only {total_loc} LOC changes (<={MIN_LOC_CHANGED})")
                skipped_loc += 1
                continue

            # Filter 2: Staleness
            try:
                updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                log.warning(f"  #{pr_number} skip — can't parse updatedAt: {updated_at_str}")
                skipped_stale += 1
                continue

            if updated_at > cutoff:
                log.info(f"  #{pr_number} skip — last updated {updated_at_str}, not yet stale")
                skipped_stale += 1
                continue

            # Dedup: same head SHA already processed OR reviewed in last 24h
            pr_key = f"{repo_full}#{pr_number}"
            if state.get(pr_key, {}).get("head_sha") == head_sha:
                log.info(f"  #{pr_number} skip — already processed (SHA {head_sha[:12]})")
                skipped_dedup += 1
                continue

            if _was_reviewed_today(owner, repo_name, pr_number):
                log.info(f"  #{pr_number} skip — reviewed in last 24h")
                skipped_dedup += 1
                continue

            # All filters passed — spawn deep-think
            log.info(
                f"  #{pr_number} TRIGGER — {total_loc} LOC changed, "
                f"stale since {updated_at_str}, SHA={head_sha[:12]}"
            )
            if _spawn_deepthink(owner, repo_name, pr_number, pr_title, pr_author, total_loc, head_sha):
                # Record dedup only on successful spawn
                state[pr_key] = {"head_sha": head_sha, "reviewed_at": datetime.now(timezone.utc).isoformat()}
                _save_state(state)
                triggered += 1
            else:
                log.warning(f"  #{pr_number} spawn failed after retries — not recording state")

    # Summary
    log.info(
        f"Done. Triggered={triggered}, "
        f"skipped(LOC)={skipped_loc}, skipped(not-stale)={skipped_stale}, "
        f"skipped(ownership)={skipped_ownership}, skipped(dedup)={skipped_dedup}"
    )


if __name__ == "__main__":
    run()
