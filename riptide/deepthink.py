#!/usr/bin/env python3
"""
deepthink.py — Bot 2: Riptide Review (autonomous deep-think PR analysis).

Polls open PRs and spawns Hermes deep-think sessions when:
  1. PR has total changes (additions + deletions) > 100 LOC
  2. PR hasn't been updated in >= 30 minutes (settled)
  3. Either we own the repo (ChonSong org) OR we authored the PR

Dedup: tracks pr_number + head_sha to avoid re-spawning on the same revision.
Uses `gh` CLI (already authenticated as ChonSong) for all GitHub queries.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("riptide.deepthink")

# ── Config ───────────────────────────────────────────────────────────────────

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


def _load_state() -> dict[str, str]:
    """Load processed PR state: {owner/repo#number: head_sha}"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(state: dict[str, str]):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


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
):
    """Spawn a Hermes cron session for deep-think review on this PR."""
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
        f"- HEAD SHA: {head_sha}\n\n"
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
        f"### Step 3: Post Inline Comments for Each Finding (NEW — like CodeRabbit)\n"
        f"For each substantive issue, risk, or suggestion you find during deep-think, "
        f"post an **inline review comment** on the specific line of code.\n\n"
        f"To post an inline comment, use:\n"
        f"```\n"
        f"gh api repos/{owner}/{repo}/pulls/{pr_number}/comments \\\n"
        f"  --method POST \\\n"
        f"  -f body='<your comment>' \\\n"
        f"  -f commit_id='{head_sha}' \\\n"
        f"  -f path='<file_path>' \\\n"
        f"  -f line=<line_number> \\\n"
        f"  -f side='RIGHT'\n"
        f"```\n\n"
        f"**How to get line numbers**: Parse the `@@` hunk headers in the `gh pr diff` output. "
        f"Each hunk looks like `@@ -old_start,count +new_start,count @@`. "
        f"The `+new_start` tells you the first new-file line number in that hunk. "
        f"Count forward from there for added/modified lines. "
        f"Use `side='RIGHT'` for new lines (added or modified), `side='LEFT'` for deleted lines.\n\n"
        f"**Comment format**: Prefix each inline comment with a severity marker:\n"
        f"- `**🔴 Critical:**` — definite bug, security issue, data loss risk\n"
        f"- `**🟡 Warning:**` — potential issue, performance concern, code smell\n"
        f"- `**ℹ️ Suggestion:**` — style improvement, minor refactor, nitpick\n\n"
        f"Post inline comments for substantive findings only (2-5 per PR is normal). "
        f"Don't comment on every line — focus on real issues.\n\n"
        f"### Step 4: Post Summary Review\n"
        f"After posting all inline comments, post a **summary review comment**:\n"
        f"`gh pr comment {pr_number} --repo {owner}/{repo} --body '<review>'`\n\n"
        f"Structure your summary as:\n"
        f"- **Summary**: What this PR does (1-2 sentences)\n"
        f"- **Graphify Analysis**: Cross-file impact, blast radius\n"
        f"- **Inline Comments**: File-level overview of what you flagged (N findings — see specific lines)\n"
        f"- **Next Steps**: Specific actionable advice for the author\n\n"
        f"If everything looks good and you have no inline findings, "
        f"say so with reasoning — don't invent problems.\n\n"
        f"**Sign-off**: End your summary with:\n"
        f"```\n"
        f"---\n"
        f"<sub>🤖 Riptide Review via Hermes · model: `hermes` (LongCat-2.0)</sub>\n"
        f"```\n\n"
        f"REPO PATH: ~/workspace/{repo}/\n"
    )

    cmd = [
        "hermes", "cron", "create", run_at,
        "--name", name,
        "--skill", "github-pr-lifecycle",
        "--skill", "deep-think",
        "--model", "custom:LongCat-2.0",
        "--deliver", "origin",
    ]

    log.info(f"Spawning: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            log.info(f"✓ Spawned deep-think for {owner}/{repo}#{pr_number}: {result.stdout[:200]}")
        else:
            log.error(f"✗ Spawn failed for {owner}/{repo}#{pr_number}: {result.stderr[:300]}")
    except subprocess.TimeoutExpired:
        log.warning(f"Timeout spawning deep-think for {owner}/{repo}#{pr_number}")
    except Exception as e:
        log.error(f"Error spawning deep-think: {e}")


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

            # Dedup: same head SHA already processed
            pr_key = f"{repo_full}#{pr_number}"
            if state.get(pr_key) == head_sha:
                log.info(f"  #{pr_number} skip — already processed (SHA {head_sha[:12]})")
                skipped_dedup += 1
                continue

            # All filters passed — spawn deep-think
            log.info(
                f"  #{pr_number} TRIGGER — {total_loc} LOC changed, "
                f"stale since {updated_at_str}, SHA={head_sha[:12]}"
            )
            _spawn_deepthink(owner, repo_name, pr_number, pr_title, pr_author, total_loc, head_sha)

            # Record dedup immediately to prevent double-spawn
            state[pr_key] = head_sha
            _save_state(state)
            triggered += 1

    # Summary
    log.info(
        f"Done. Triggered={triggered}, "
        f"skipped(LOC)={skipped_loc}, skipped(not-stale)={skipped_stale}, "
        f"skipped(ownership)={skipped_ownership}, skipped(dedup)={skipped_dedup}"
    )


if __name__ == "__main__":
    run()
