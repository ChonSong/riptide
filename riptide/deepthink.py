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
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from riptide.state import StateStore
from riptide.depth import ReviewDepth, classify_review_depth, select_skills  # noqa: F401 (re-exported for back-compat)

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
# Pin the inference config on spawned cron jobs — the global config drifts,
# and unpinned jobs are skipped to prevent unintended spend (Hermes #44585).
# The riptide profile runs LongCat-2.0 via the custom LongCat provider.
DEEPTHINK_MODEL = os.environ.get("RIPTIDE_DEEPTHINK_MODEL", "LongCat-2.0")
DEEPTHINK_PROVIDER = os.environ.get("RIPTIDE_DEEPTHINK_PROVIDER", "longcat")
STATE_FILE = Path(
    os.environ.get("RIPTIDE_DATA_DIR", "/tmp/riptide-data")
) / "deepthink_acted_prs.json"


def _load_state() -> dict[str, dict]:
    """Back-compat: return all PR heuristics as {pr_key: {head_sha, reviewed_at}}.

    WS-3 Stage 0: the authority is StateStore.pr_heuristics; this wrapper
    preserves the old dict shape for callers/tests.
    """
    store = StateStore()
    conn = store._get_conn()
    rows = conn.execute(
        "SELECT pr_key, last_sha, reviewed_at FROM pr_heuristics"
    ).fetchall()
    return {
        pr_key: {"head_sha": last_sha, "reviewed_at": reviewed_at}
        for pr_key, last_sha, reviewed_at in rows
    }


def _save_state(state: dict[str, dict]):
    """Back-compat: persist PR heuristics into StateStore.pr_heuristics."""
    store = StateStore()
    for pr_key, entry in state.items():
        if entry.get("head_sha"):
            store.set_pr_last_sha(pr_key, entry["head_sha"])
        if entry.get("reviewed_at"):
            store.set_pr_reviewed_at(pr_key, entry["reviewed_at"])


def _was_reviewed_today(owner: str, repo: str, pr_number: int) -> bool:
    """Check if this PR was reviewed in the last 24 hours (StateStore-backed)."""
    pr_key = f"{owner}/{repo}#{pr_number}"
    reviewed_at = StateStore().get_pr_heuristics(pr_key)["reviewed_at"]
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
    """Run a PR review via the Riptide Pipeline.
    
    The pipeline orchestrates:
    1. Probe — deterministic context gathering
    2. Judge — LLM deep-think analysis (spawns Hermes session)
    3. Artisan — deterministic file creation
    4. Engine — deterministic shell execution
    5. Scribe — LLM review summary (spawns Hermes session)
    
    Uses work_state.json for durable state and recovery.py for self-healing.
    """
    from riptide.pipeline.conductor import create_pr_review_pipeline, Conductor
    
    name = f"riptide-review-{owner}-{repo}-{pr_number}"
    track_id = f"{owner}-{repo}-{pr_number}"
    
    # Cross-session awareness: clean up stale jobs, then atomically reserve
    from riptide.state import StateStore
    state = StateStore()
    state.cleanup_stale_pending()
    job_id = f"{name}-{head_sha[:12]}-{uuid.uuid4().hex[:12]}"
    if not state.reserve_job(job_id, pr_number, "pipeline", name):
        log.info(f"Skipping {owner}/{repo}#{pr_number} — review already pending")
        return False
    
    try:
        # Create the pipeline (idempotent — safe to call multiple times)
        create_pr_review_pipeline(track_id, pr_number, owner, repo)
        
        # Build the LLM spawn callback
        def spawn_llm(prompt: str, name: str, skills: list[str]) -> bool:
            """Spawn a Hermes cron session for LLM reasoning."""
            return _spawn_hermes_session(prompt, name, skills)
        
        # Run the conductor (orchestrates deterministic + LLM workers)
        conductor = Conductor(track_id, spawn_llm=spawn_llm)
        
        # Run in background thread (non-blocking for webhook/poller)
        import threading
        def _run_pipeline():
            try:
                result = conductor.run()
                if all(r.get("status") == "done" for r in result.get("results", [])):
                    state.mark_complete(job_id)
                else:
                    state.mark_failed(job_id)
            except Exception as e:
                log.error(f"Pipeline failed for {owner}/{repo}#{pr_number}: {e}")
                state.mark_failed(job_id)
        
        thread = threading.Thread(target=_run_pipeline, daemon=True, name=f"pipeline-{name}")
        thread.start()
        
        log.info(f"✓ Pipeline started for {owner}/{repo}#{pr_number} (track: {track_id})")
        return True
        
    except Exception as e:
        state.mark_failed(job_id)
        log.error(f"Failed to start pipeline for {owner}/{repo}#{pr_number}: {e}")
        return False


def _spawn_hermes_session(prompt: str, name: str, skills: list[str]) -> bool:
    """Spawn a Hermes cron session for LLM reasoning.
    
    Retries up to 3 times with exponential backoff (5s/15s/30s).
    Returns True if spawned successfully, False otherwise.
    """
    max_retries = 3
    base_delay = 5
    run_at = (datetime.now() + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")
    
    for attempt in range(max_retries):
        if attempt > 0:
            delay = base_delay * (2 ** attempt)
            log.info(f"Retry {attempt+1}/{max_retries} for {name} in {delay}s...")
            time.sleep(delay)
        
        if not _is_cron_available():
            log.warning(f"hermes not available on attempt {attempt+1} for {name}")
            continue
        
        cmd = [
            "hermes", "cron", "create", run_at,
            prompt,
            "--name", name,
        ]
        for skill in skills:
            cmd.extend(["--skill", skill])
        cmd.extend([
            "--model", DEEPTHINK_MODEL,
            "--provider", DEEPTHINK_PROVIDER,
            "--deliver", "origin",
        ])
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                log.info(f"✓ Spawned Hermes session for {name}: {result.stdout[:200]}")
                return True
            else:
                log.error(f"✗ Spawn failed (attempt {attempt+1}): {result.stderr[:300]}")
        except subprocess.TimeoutExpired:
            log.warning(f"Timeout spawning Hermes (attempt {attempt+1})")
        except Exception as e:
            log.error(f"Error spawning Hermes (attempt {attempt+1}): {e}")
    
    log.error(f"All {max_retries} attempts failed for {name}")
    return False


def _gather_review_data(
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
) -> dict:
    """Pre-gather review data in Python before spawning the Hermes session.

    Returns a dict with:
    - files_changed: list of {filename, additions, deletions}
    - diff_raw: raw diff text (capped at 50k chars)
    - repo_tree: list of file paths
    - god_nodes: list of {name, edges}
    - communities: list of {name, members}
    - graph_context: raw graphify output
    """
    data = {
        "files_changed": [],
        "diff_raw": "",
        "repo_tree": [],
        "god_nodes": [],
        "communities": [],
        "graph_context": {},
    }

    # 1. Fetch PR diff
    try:
        result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number), "--repo", f"{owner}/{repo}"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            data["diff_raw"] = result.stdout[:50000]
    except Exception as e:
        log.warning(f"Failed to fetch diff: {e}")

    # 2. Fetch PR files
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", f"{owner}/{repo}",
             "--json", "files"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            files_data = json.loads(result.stdout)
            # Normalize: gh returns {path, additions, deletions}; we use "filename"
            raw_files = files_data.get("files", [])
            data["files_changed"] = [
                {
                    "filename": f.get("path", "?"),
                    "additions": f.get("additions", 0),
                    "deletions": f.get("deletions", 0),
                }
                for f in raw_files
            ]
    except Exception as e:
        log.warning(f"Failed to fetch PR files: {e}")

    # 2b. Fetch per-file patches (deterministic input for the context bundle)
    # gh pr view --json files does NOT include patch content; the pulls API does.
    # This lets DiffAnalyzer (security/complexity/error_handling) see real added lines.
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/pulls/{pr_number}/files",
             "--paginate"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            api_files = json.loads(result.stdout)
            if isinstance(api_files, list):
                patch_by_name = {f.get("filename"): f for f in api_files}
                for entry in data["files_changed"]:
                    api_entry = patch_by_name.get(entry["filename"])
                    if api_entry:
                        entry["patch"] = api_entry.get("patch") or ""
                        entry["status"] = api_entry.get("status", "modified")
    except Exception as e:
        log.warning(f"Failed to fetch per-file patches: {e}")

    # 3. Fetch repo tree (from local workspace if available)
    workspace = Path.home() / "workspace" / repo
    if workspace.is_dir():
        try:
            result = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", head_sha],
                capture_output=True, text=True, timeout=30,
                cwd=str(workspace),
            )
            if result.returncode == 0:
                data["repo_tree"] = result.stdout.strip().split("\n")
        except Exception as e:
            log.warning(f"Failed to fetch repo tree: {e}")

        # 4. Run graphify if available (skip if fresh to avoid dirtying workspace)
        graphify_dir = workspace / "graphify-out"
        if graphify_dir.is_dir():
            graph_json = graphify_dir / "graph.json"
            if graph_json.exists():
                age_minutes = (time.time() - graph_json.stat().st_mtime) / 60
                if age_minutes < 15:
                    log.info("Skipping graphify update — graph is fresh (<15 min)")
                else:
                    try:
                        subprocess.run(
                            ["graphify", "update", "."],
                            capture_output=True, text=True, timeout=60,
                            cwd=str(workspace),
                        )
                    except Exception as e:
                        log.warning(f"Graphify update failed: {e}")
            else:
                # No graph yet — run update
                try:
                    subprocess.run(
                        ["graphify", "update", "."],
                        capture_output=True, text=True, timeout=60,
                        cwd=str(workspace),
                    )
                except Exception as e:
                    log.warning(f"Graphify update failed: {e}")

            # Query for PR impact (runs regardless of update path)
            try:
                result = subprocess.run(
                    ["graphify", "query", f"what does PR #{pr_number} affect?"],
                    capture_output=True, text=True, timeout=30,
                    cwd=str(workspace),
                )
                if result.returncode == 0:
                    data["graph_context"] = {"raw": result.stdout.strip()}

                # Get god nodes
                result = subprocess.run(
                    ["graphify", "god-nodes", "--top", "10"],
                    capture_output=True, text=True, timeout=30,
                    cwd=str(workspace),
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split("\n"):
                        match = re.match(r"\s*\d+\.\s+(.+?)\s+-\s+(\d+)\s+edges", line)
                        if match:
                            data["god_nodes"].append({
                                "name": match.group(1),
                                "edges": int(match.group(2)),
                            })

                # Get communities
                result = subprocess.run(
                    ["graphify", "query", "list communities"],
                    capture_output=True, text=True, timeout=30,
                    cwd=str(workspace),
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split("\n"):
                        if line.strip().startswith("- "):
                            comm_name = line.strip()[2:].strip()
                            data["communities"].append({"name": comm_name, "members": []})

            except Exception as e:
                log.warning(f"Graphify failed: {e}")

    return data


def _build_orchestrator_prompt(
    owner: str,
    repo: str,
    pr_number: int,
    pr_title: str,
    pr_author: str,
    total_loc: int,
    head_sha: str,
    data: dict,
    diagram_url: Optional[str] = None,
    deterministic: Optional[dict] = None,
) -> str:
    """
    Build a small orchestrator prompt that delegates to subagents.

    The prompt is ~40 lines instead of ~280 lines.
    All data is pre-gathered in Python and passed as structured context.
    If diagram_url is provided, the LLM references it instead of generating.
    If deterministic is provided (WS-3 Stage 1 context bundle), its DiffReport
    findings + verdict are embedded so the session starts from the deterministic
    analysis instead of re-deriving it.
    """
    # Format files changed
    files_str = "\n".join(
        f"  - {f.get('filename', '?')} (+{f.get('additions', 0)}/-{f.get('deletions', 0)})"
        for f in data["files_changed"][:20]
    )

    # Format diff summary (first 12k chars)
    diff_summary = data["diff_raw"][:12000]
    if len(data["diff_raw"]) > 12000:
        diff_summary += f"\n  ... ({len(data['diff_raw'])} chars total)"

    # Format graphify context
    graph_str = ""
    if data["god_nodes"]:
        graph_str += "God Nodes:\n"
        for node in data["god_nodes"][:5]:
            graph_str += f"  - {node['name']} ({node['edges']} edges)\n"
    if data["communities"]:
        graph_str += "Communities:\n"
        for comm in data["communities"][:5]:
            graph_str += f"  - {comm['name']}\n"
    if not graph_str:
        graph_str = "(No graphify analysis available)"

    diagram_section = f"\n## Pre-generated Architecture Diagram\n[View Diagram]({diagram_url})\n" if diagram_url else ""
    diagram_step = "\n### Step 4: Architecture Diagram\nThe architecture diagram is pre-generated and embedded above. Reference it in your Code Analysis section.\n" if diagram_url else ""

    # Deterministic analysis section (WS-3 Stage 1): pre-computed DiffReport findings
    deterministic_section = ""
    if deterministic:
        verdict = deterministic.get("verdict", "pass")
        findings = deterministic.get("findings", [])
        stats = deterministic.get("stats", {})
        concepts = deterministic.get("aggregate", {}).get("concepts", [])
        lines = ["## Deterministic Analysis (pre-computed)", ""]
        lines.append(f"Verdict: **{verdict}** — {len(findings)} finding(s), "
                     f"{stats.get('total_add', 0)}+/"
                     f"{stats.get('total_del', 0)}- across "
                     f"{stats.get('file_count', 0)} file(s).")
        if concepts:
            lines.append(f"Concepts touched: {', '.join(concepts)}")
        if findings:
            lines.append("")
            lines.append("Findings (verify against the diff, do NOT re-derive from scratch):")
            for f in findings[:10]:
                lines.append(
                    f"- [{f.get('severity', 'info')}] {f.get('category', '?')}: "
                    f"{f.get('message', '')}"
                    + (f" — {f.get('file', '')}" if f.get('file') else "")
                )
            if len(findings) > 10:
                lines.append(f"  … and {len(findings) - 10} more")
        lines.append("")
        lines.append("Your subagent review must reference these findings — confirm, refute, or extend them. "
                     "Do not duplicate the analysis; add value on top of it.")
        deterministic_section = "\n".join(lines) + "\n\n"

    return f"""PR #{pr_number} in {owner}/{repo} — {total_loc} LOC changed.

## Context (pre-gathered)
- Title: {pr_title}
- Author: {pr_author}
- HEAD SHA: {head_sha[:12]}

### Files Changed
{files_str}

### Repository Tree
```
{format_repo_tree(data.get("repo_tree", []))}
```

### Diff Summary
````
{diff_summary}
````

### Graphify Analysis
{graph_str}
{diagram_section}
{deterministic_section}## Your Task: Orchestrate Review

You are a senior engineer. Delegate review tasks to subagents, then synthesize.

### Step 1: Delegate Inline Review
Spawn a subagent with:
- Role: Code reviewer
- Task: Call `skill_view('deep-think')` first, then analyze the PR diff, post 1-3 inline review comments with GitHub suggestion blocks
- Output: JSON list of findings [{{file, line, severity, title, detail}}]
Severity must be one of: critical, warning, suggestion, info, approved.

### Step 2: Write Findings JSON
After the inline review subagent finishes, write its findings to /tmp/findings.json as JSON:
[{{severity, title, detail, file, line}}]

### Step 3: Assemble + Post Review (deterministic)
Run the assembly script — it validates, formats, and posts. Do NOT hand-format the review.

```
python -m riptide.assemble_review \
  --findings /tmp/findings.json \
  --owner {owner} --repo {repo} --pr {pr_number} \
  --model "{DEEPTHINK_MODEL}" --provider "{DEEPTHINK_PROVIDER}"
```

The script appends the model/provider to the sign-off deterministically.

### Rules
- Max 3 inline comments, real issues only
- Do not invent problems or pad the review
- Reference inline comments in the summary
- The Code Analysis and Explanation sections are REQUIRED — never omit them
- If a section has nothing to report, say so explicitly ("No significant findings") rather than omitting it
{diagram_step}
REPO PATH: ~/workspace/{repo}/
"""


def format_repo_tree(repo_tree: list) -> str:
    """Format a repo file list as an indented directory tree (capped at 500 entries)."""
    if not repo_tree:
        return "(No repo tree available)"
    lines = []
    for f in repo_tree[:500]:
        # Show directory structure with 2-space indent per path depth
        depth = f.count("/") if isinstance(f, str) else 0
        lines.append("  " * depth + f.rsplit("/", 1)[-1])
    if len(repo_tree) > 500:
        lines.append(f"  ... ({len(repo_tree) - 500} more files)")
    return "\n".join(lines)


def run():
    """Poll watched repos and spawn deep-think sessions on qualifying PRs."""
    if not _is_cron_available():
        log.error("hermes binary not found — can't spawn sessions")
        sys.exit(1)

    state_store = StateStore()
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
            h = state_store.get_pr_heuristics(pr_key)
            if h["last_sha"] == head_sha:
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
                state_store.set_pr_last_sha(pr_key, head_sha)
                state_store.set_pr_reviewed_at(pr_key, datetime.now(timezone.utc).isoformat())
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
