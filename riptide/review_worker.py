#!/usr/bin/env python3
"""
review_worker.py — Background worker for processing review jobs from the queue.

Handles:
  - review: full PR review (diff → retrieve → LLM → inline comments + check run)
  - incremental_index: after PR merge, update the numpy vector store with changed files only

No threading complexity — runs single-threaded in a daemon worker loop.
"""
from __future__ import annotations

import os, sys, json, logging, traceback, tempfile
from pathlib import Path
from queue import Queue
from threading import Thread
from datetime import datetime, timezone

# ── Paths ────────────────────────────────────────────────────────────────────

# Riptide package dir
RIPTIDE_DIR = Path(__file__).parent.parent

# Riptide scripts dir (legacy scripts, imported as modules)
PR_REVIEW_SCRIPTS = Path(os.environ.get(
    "PR_REVIEW_SCRIPTS",
    "/home/sc/.hermes/skills/pr-review/scripts"
))

# Riptide data dirs
DATA_DIR = Path(os.environ.get("RIPTIDE_DATA_DIR", "/tmp/riptide"))
INDEX_DIR = DATA_DIR / "index"
INDEX_DIR.mkdir(parents=True, exist_ok=True)
METADATA_DB = DATA_DIR / "metadata.db"

# Ollama
OLLAMA_BASE  = os.environ.get("OLLAMA_BASE_URL",     "http://localhost:43311")
OLLAMA_EMBED = os.environ.get("OLLAMA_EMBED_MODEL",  "nomic-embed-text")
REVIEW_MODEL = os.environ.get("OLLAMA_REVIEW_MODEL", "qwen2.5-coder:7b")
RETRIEVE_TOP_K = int(os.environ.get("RETRIEVE_TOP_K", "8"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("riptide.worker")


# ── Job enqueue helpers ───────────────────────────────────────────────────────

def enqueue_review(queue: Queue, job: dict):
    """Add a review job. Blocks if queue is full (serialises reviews)."""
    queue.put(job, block=True)
    log.info(f"Enqueued review job: {job['repo_full']}#{job['pr_number']}")


def enqueue_index(queue: Queue, job: dict):
    queue.put(job, block=True)
    log.info(f"Enqueued index job: {job['repo_full']}#{job['pr_number']}")


# ── Main worker loop ─────────────────────────────────────────────────────────

def process_jobs(queue: Queue):
    """
    Single-threaded daemon loop. Blocks on queue.get(), processes one job at a time.
    Serialisation is intentional — prevents GitHub API rate-limiting on concurrent reviews.
    """
    # Import here so FastAPI starts cleanly even if scripts dir is missing
    from .github_app import GitHubAppClient

    app_id = int(os.environ.get("GITHUB_APP_ID", "4262983"))
    private_key = os.environ.get("GITHUB_PRIVATE_KEY_PATH", "")

    if not private_key:
        log.error("GITHUB_PRIVATE_KEY_PATH not set — worker cannot authenticate")
        return

    client = GitHubAppClient(app_id, private_key)

    # Pre-import review scripts
    _import_review_scripts()

    log.info("Worker loop started")
    while True:
        job = queue.get()  # blocks
        job_type = job.get("type", "unknown")
        delivery_id = job.get("delivery_id", "?")
        try:
            if job_type == "review":
                _run_review(client, job)
            elif job_type == "incremental_index":
                _run_incremental_index(client, job)
            else:
                log.warning(f"[{delivery_id}] Unknown job type: {job_type}")
        except Exception as e:
            log.error(f"[{delivery_id}] Worker error on {job_type}: {e}\n{traceback.format_exc()}")


# ── Review pipeline ──────────────────────────────────────────────────────────

def _run_review(client: GitHubAppClient, job: dict):
    """Full review: fetch diff → retrieve context → LLM → post results."""
    installation_id = job["installation_id"]
    owner = job["owner"]
    repo = job["repo"]
    repo_full = job["repo_full"]
    pr_number = job["pr_number"]
    pr_title = job.get("pr_title", f"PR #{pr_number}")
    pr_author = job.get("pr_author", "unknown")
    head_sha = job.get("head_sha", "")
    delivery_id = job.get("delivery_id", "?")

    log.info(f"[{delivery_id}] Starting review: {repo_full}#{pr_number}")

    # ── 1. Fetch PR details ────────────────────────────────────────────────
    try:
        pr_details = client.get_pr_details(installation_id, owner, repo, pr_number)
        base_sha = pr_details.get("base", {}).get("sha", "")
        head_sha = pr_details.get("head", {}).get("sha", head_sha)
        diff_url = pr_details.get("diff_url", "")
        if not head_sha:
            head_sha = job.get("head_sha", "")
    except Exception as e:
        log.error(f"[{delivery_id}] Could not fetch PR details: {e}")
        return

    # ── 2. Fetch changed files ──────────────────────────────────────────────
    try:
        changed_files = client.get_pr_files(installation_id, owner, repo, pr_number)
    except Exception as e:
        log.error(f"[{delivery_id}] Could not fetch PR files: {e}")
        return

    if not changed_files:
        log.info(f"[{delivery_id}] No changed files, skipping review")
        return

    # ── 3. Build diff for review ────────────────────────────────────────────
    # Use the raw patch from GitHub API
    diff_lines = []
    for f in changed_files:
        diff_lines.append(f"=== {f['filename']} ({f['status']}) ===")
        patch = f.get("patch", "")
        if patch:
            diff_lines.append(patch)
        else:
            # Binary or new file with no patch
            diff_lines.append("(no diff available)")
    diff_content = "\n".join(diff_lines)

    # ── 4. Retrieve context from numpy store ────────────────────────────────
    db_path = INDEX_DIR / f"{owner}___{repo}.db"
    context_results = []
    if db_path.exists():
        context_results = _retrieve_context(
            diff_content[:2000],  # use first 2k chars as query
            str(db_path),
            top_k=RETRIEVE_TOP_K,
        )
        log.info(f"[{delivery_id}] Retrieved {len(context_results)} context results")
    else:
        log.info(f"[{delivery_id}] No index found at {db_path}, skipping context retrieval")

    # ── 5. Run LLM review ───────────────────────────────────────────────────
    review_result = _llm_review(diff_content, repo_full, pr_number, context_results)

    # ── 6. Create check run (in_progress → completed) ──────────────────────
    try:
        check = client.create_check_run(
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            name="riptide/code-review",
            head_sha=head_sha,
            status="in_progress",
        )
        check_run_id = check["id"]
        log.info(f"[{delivery_id}] Created check run {check_run_id}")
    except Exception as e:
        log.warning(f"[{delivery_id}] Could not create check run: {e}")
        check_run_id = None

    # ── 7. Parse findings and post comments ───────────────────────────────
    findings = review_result.get("findings", [])
    summary = review_result.get("summary", "Review complete")

    if findings:
        # Post inline comments
        inline_count = 0
        for finding in findings[:10]:  # cap at 10 inline comments
            location = finding.get("location", "")
            if ":" in location:
                # Try to extract path:line
                parts = location.rsplit(":", 1)
                path_hint, line_hint = parts[0], parts[1]
                try:
                    line_num = int(line_hint)
                    # Find the file in changed_files that matches the path hint
                    for f in changed_files:
                        if path_hint in f["filename"]:
                            body = _format_finding_body(finding)
                            try:
                                client.post_inline_comment(
                                    installation_id=installation_id,
                                    owner=owner,
                                    repo=repo,
                                    pr_number=pr_number,
                                    body=body,
                                    commit_id=head_sha,
                                    path=f["filename"],
                                    line=line_num,
                                )
                                inline_count += 1
                                log.info(f"[{delivery_id}] Posted inline comment on {f['filename']}:{line_num}")
                            except Exception as e2:
                                log.warning(f"[{delivery_id}] Inline comment failed: {e2}")
                            break
                except ValueError:
                    pass  # couldn't parse line number

        # Post a summary PR comment
        summary_body = _format_summary_comment(findings, repo_full, pr_number, pr_author, inline_count)
        try:
            client.post_pr_comment(installation_id, owner, repo, pr_number, summary_body)
            log.info(f"[{delivery_id}] Posted summary comment")
        except Exception as e:
            log.warning(f"[{delivery_id}] Summary comment failed: {e}")
    else:
        # No findings — post a clean LGTM comment
        lgtm_body = (
            f"## ✅ Riptide Review\n\n"
            f"**{repo_full}#{pr_number}** — looks good to me!\n\n"
            f"No issues detected in the changes.\n"
        )
        try:
            client.post_pr_comment(installation_id, owner, repo, pr_number, lgtm_body)
        except Exception as e:
            log.warning(f"[{delivery_id}] LGTM comment failed: {e}")

    # ── 8. Update check run ────────────────────────────────────────────────
    if check_run_id:
        conclusion = "failure" if findings else "success"
        output = {
            "title": summary,
            "summary": summary[:65535],
            "text": review_result.get("raw", "")[:65535],
        }
        if findings:
            output["annotations"] = [
                {
                    "path": finding.get("location", "unknown").split(":")[0] if ":" in finding.get("location", "unknown") else "unknown",
                    "start_line": int(finding.get("location", "").rsplit(":", 1)[-1]) if ":" in finding.get("location", "") else 1,
                    "end_line": int(finding.get("location", "").rsplit(":", 1)[-1]) if ":" in finding.get("location", "") else 1,
                    "annotation_level": "warning",
                    "message": f"[{finding.get('severity', 'Medium')}] {finding.get('finding', '')}",
                }
                for finding in findings[:10]
            ]
        try:
            client.update_check_run(installation_id, owner, repo, check_run_id, conclusion, output)
            log.info(f"[{delivery_id}] Check run updated: {conclusion}")
        except Exception as e:
            log.warning(f"[{delivery_id}] Check run update failed: {e}")

    log.info(f"[{delivery_id}] Review complete: {len(findings)} findings")


# ── Incremental index ───────────────────────────────────────────────────────

def _run_incremental_index(client: GitHubAppClient, job: dict):
    """
    After a PR is merged, update the numpy vector store with only the changed files.
    Uses GitHub API to fetch file contents and re-embed.
    """
    installation_id = job["installation_id"]
    owner = job["owner"]
    repo = job["repo"]
    repo_full = job["repo_full"]
    changed_files = job.get("changed_files", [])
    delivery_id = job.get("delivery_id", "?")

    log.info(f"[{delivery_id}] Incremental index: {len(changed_files)} files in {repo_full}")

    db_path = INDEX_DIR / f"{owner}___{repo}.db"
    indexed = 0
    for file_info in changed_files:
        fname = file_info.get("filename", "")
        status = file_info.get("status", "")
        if status == "removed":
            # Remove from store if we had it
            try:
                _remove_from_store(str(db_path), fname)
                log.info(f"[{delivery_id}] Removed {fname} from index")
            except Exception:
                pass
            continue
        try:
            # Fetch file content via GitHub API
            from .embed import embed_texts  # local embedder
            import requests as _req
            resp = _req.get(
                f"https://api.github.com/repos/{owner}/{repo}/contents/{fname}",
                headers={"Accept": "application/vnd.github.raw+json"},
                timeout=15,
            )
            if resp.status_code == 200:
                content = resp.text[:50000]  # cap at 50k chars
                vecs = embed_texts([content])  # returns list of vectors
                if vecs:
                    _upsert_to_store(str(db_path), fname, content, vecs[0])
                    indexed += 1
        except Exception as e:
            log.warning(f"[{delivery_id}] Could not index {fname}: {e}")

    log.info(f"[{delivery_id}] Incremental index done: {indexed}/{len(changed_files)} files updated")


# ── Helpers (import + delegate to existing scripts) ─────────────────────────

def _import_review_scripts():
    """Import review.py, store.py as modules from the pr-review scripts dir."""
    import importlib.util
    scripts = {}

    for name in ("review", "store"):
        path = PR_REVIEW_SCRIPTS / f"{name}.py"
        if path.exists():
            spec = importlib.util.spec_from_file_location(f"pr_review_{name}", str(path))
            mod = importlib.util.module_from_spec(spec)
            sys.modules[f"pr_review_{name}"] = mod
            spec.loader.exec_module(mod)
            scripts[name] = mod
            logging.info(f"Loaded pr_review_{name} from {path}")
        else:
            logging.warning(f"Review script not found: {path}")

    return scripts


# Cached module references
_review_scripts = None

def _get_review_scripts():
    global _review_scripts
    if _review_scripts is None:
        _review_scripts = _import_review_scripts()
    return _review_scripts


def _retrieve_context(query: str, db_path: str, top_k: int) -> list:
    """Top-K vector search using the numpy store."""
    try:
        scripts = _get_review_scripts()
        store_mod = scripts.get("store")
        review_mod = scripts.get("review")
        if not store_mod or not review_mod:
            return []
        vec = review_mod.embed_query(query)
        if all(v == 0 for v in vec):
            return []
        return store_mod.search(db_path, vec, top_k=top_k)
    except Exception as e:
        log.warning(f"Context retrieval failed: {e}")
        return []


def _llm_review(diff_content: str, repo: str, pr_num: int, context_results: list) -> dict:
    """Run LLM review using existing review.py."""
    try:
        scripts = _get_review_scripts()
        review_mod = scripts.get("review")
        if not review_mod:
            return {"raw": "review module not available", "findings": [], "summary": "error"}

        prompt = review_mod.build_prompt(diff_content, repo, pr_num, context_results)
        raw = review_mod.llm_review(prompt)
        result = review_mod.parse_review(raw)
        return result
    except Exception as e:
        log.error(f"LLM review failed: {e}")
        return {"raw": str(e), "findings": [], "summary": "review error"}


# ── Embedding (local, no network call to GitHub API for this) ────────────────

def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts using local Ollama."""
    import requests
    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/embed",
            json={"model": OLLAMA_EMBED, "input": texts},
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("embeddings", [])
    except Exception as e:
        log.warning(f"Embed failed: {e}")
    return []


# ── Store helpers (SQLite numpy store) ───────────────────────────────────────

def _upsert_to_store(db_path: str, filename: str, content: str, vector: list[float]):
    """Add or update a file entry in the numpy vector store."""
    import sqlite3, json
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR REPLACE INTO code_chunks (filename, chunk_text, vector, updated_at)
        VALUES (?, ?, ?, ?)
    """, (filename, content[:10000], json.dumps(vector), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _remove_from_store(db_path: str, filename: str):
    """Remove a file from the numpy store."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM code_chunks WHERE filename = ?", (filename,))
    conn.commit()
    conn.close()


# ── Formatting helpers ───────────────────────────────────────────────────────

def _format_finding_body(finding: dict) -> str:
    """Format a single finding as an inline comment body."""
    severity = finding.get("severity", "Medium")
    emoji = {"Low": "🔵", "Medium": "🟡", "High": "🟠", "Critical": "🔴"}.get(severity, "🟡")
    lines = [
        f"{emoji} **{severity}** — {finding.get('finding', '')}",
    ]
    if finding.get("suggestion"):
        lines.append(f"\n**Suggestion**: {finding['suggestion']}")
    return "\n".join(lines)


def _format_summary_comment(findings: list, repo: str, pr_num: int, author: str, inline_count: int) -> str:
    """Format the PR-level summary comment."""
    high = sum(1 for f in findings if f.get("severity") in ("High", "Critical"))
    medium = sum(1 for f in findings if f.get("severity") == "Medium")
    low = sum(1 for f in findings if f.get("severity") == "Low")
    total = len(findings)

    lines = [
        f"## 🔍 Riptide Code Review",
        f"**{repo}#{pr_num}** — reviewed by Riptide",
        "",
        f"**{total} finding{'s' if total != 1 else ''}** "
        f"({inline_count} inline comment{'s' if inline_count != 1 else ''} posted): ",
        f"- 🔴 Critical/High: **{high}**" if high else "- No critical/high issues ✅",
        f"- 🟡 Medium: **{medium}**" if medium else "",
        f"- 🔵 Low: **{low}**" if low else "",
        "",
    ]

    for i, f in enumerate(findings[:5], 1):
        emoji = {"Low": "🔵", "Medium": "🟡", "High": "🟠", "Critical": "🔴"}.get(f.get("severity", "Medium"), "🟡")
        lines.append(f"{emoji} **{i}.** {f.get('finding', '')}")
        loc = f.get("location", "")
        if loc:
            lines.append(f"   📍 {loc}")
        sugg = f.get("suggestion", "")
        if sugg:
            lines.append(f"   💡 {sugg}")
        lines.append("")

    if total > 5:
        lines.append(f"_…and {total - 5} more findings (see inline comments or check run details)_")

    lines.extend([
        "",
        "---",
        "_🤖 Generated by Riptide · PR review via local Ollama (qwen2.5-coder:7b)_",
    ])

    return "\n".join(lines)
