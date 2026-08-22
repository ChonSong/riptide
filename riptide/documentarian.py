#!/usr/bin/env python3
"""
documentarian.py — Post-merge graphify + changelog for Riptide.

Called by webhook.py after a PR is merged into the default branch.
Triggers `graphify update .` to refresh the knowledge graph and appends
a changelog entry to CHANGELOG.md under [Unreleased].
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("riptide.documentarian")

# Default paths
DEFAULT_CHANGELOG_PATH = Path(os.environ.get("RIPTIDE_CHANGELOG", "CHANGELOG.md"))
DEFAULT_DB_PATH = os.environ.get(
    "RIPTIDE_DOCUMENTARIAN_DB",
    str(Path.home() / ".local/share/riptide/documentarian.db"),
)

# Thread-local storage for SQLite connections
_local = threading.local()


def _get_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Get a per-thread SQLite connection, creating the schema if needed."""
    if not hasattr(_local, "conn") or _local.conn is None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _local.conn = sqlite3.connect(db_path, timeout=30)
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
        _init_documentarian_schema(_local.conn)
    return _local.conn


def _init_documentarian_schema(conn: sqlite3.Connection) -> None:
    """Create the review_profiles table if it doesn't exist."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS review_profiles (
            repo_full_name TEXT PRIMARY KEY,
            last_merge_at TEXT NOT NULL,
            merge_count INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.commit()


def on_merge(
    owner: str,
    repo: str,
    merged_pr_number: int,
    pr_title: str,
    pr_body: str = "",
) -> None:
    """Called when a PR is merged. Triggers graphify update + changelog generation.

    This is the main entry point called from webhook.py after auto-deploy.
    All operations are best-effort — failures are logged but not raised
    to avoid disrupting the webhook response.

    Args:
        owner: Repository owner (e.g., "ChonSong").
        repo: Repository name (e.g., "riptide").
        merged_pr_number: The PR number that was merged.
        pr_title: Title of the merged PR.
        pr_body: Body/description of the merged PR (optional).
    """
    repo_full = f"{owner}/{repo}"
    log.info(f"[documentarian] PR #{merged_pr_number} merged into {repo_full} — starting post-merge tasks")

    # 1. Update the knowledge graph
    commit_sha = ""  # Could be passed from webhook payload
    graphify_ok = update_graphify(commit_sha)
    if not graphify_ok:
        log.warning(f"[documentarian] graphify update failed (non-fatal) for {repo_full}")

    # 2. Generate changelog entry
    try:
        generate_changelog_entry(owner, repo, merged_pr_number, pr_title, pr_body)
    except Exception as e:
        log.error(f"[documentarian] changelog generation failed (non-fatal): {e}")

    # 3. Update review profile
    try:
        update_review_profile(owner, repo)
    except Exception as e:
        log.error(f"[documentarian] review profile update failed (non-fatal): {e}")

    log.info(f"[documentarian] post-merge tasks complete for {repo_full}#{merged_pr_number}")


def update_graphify(commit_sha: str) -> bool:
    """Run `graphify update .` to update the knowledge graph.

    Uses the current working directory as the project root.
    Returns True on success, False on failure (graceful degradation).

    Args:
        commit_sha: The commit SHA that was merged (used for logging context).

    Returns:
        True if graphify succeeded, False otherwise.
    """
    try:
        result = subprocess.run(
            ["graphify", "update", "."],
            capture_output=True,
            text=True,
            timeout=120,  # 2-minute timeout
            cwd=os.getcwd(),
        )
        if result.returncode == 0:
            log.info(f"[documentarian] graphify update succeeded (sha={commit_sha or 'unknown'})")
            return True
        else:
            log.warning(
                f"[documentarian] graphify update failed (rc={result.returncode}): "
                f"{result.stderr.strip()[:500]}"
            )
            return False
    except FileNotFoundError:
        log.warning("[documentarian] graphify command not found in PATH — skipping")
        return False
    except subprocess.TimeoutExpired:
        log.warning("[documentarian] graphify update timed out after 120s")
        return False
    except Exception as e:
        log.error(f"[documentarian] graphify update error: {e}")
        return False


def generate_changelog_entry(
    owner: str,
    repo: str,
    pr_number: int,
    pr_title: str,
    pr_body: str = "",
    findings: Optional[list[str]] = None,
) -> None:
    """Append a changelog entry to CHANGELOG.md under [Unreleased].

    Creates the file if it doesn't exist. Adds an "### Added" or "### Changed"
    section under [Unreleased" if not already present, then appends a bullet
    line with the PR reference.

    Args:
        owner: Repository owner.
        repo: Repository name.
        pr_number: PR number.
        pr_title: PR title (used as the changelog bullet text).
        pr_body: Optional PR body for context.
        findings: Optional list of finding descriptions to include.
    """
    changelog_path = DEFAULT_CHANGELOG_PATH

    # Read existing content or create fresh
    if changelog_path.exists():
        content = changelog_path.read_text(encoding="utf-8")
    else:
        content = "# Changelog\n\n## [Unreleased]\n"

    # Determine section to add under
    section = _classify_pr_title(pr_title)

    # Build the entry line
    entry = f"- {pr_title} ([#{pr_number}](https://github.com/{owner}/{repo}/pull/{pr_number}))"
    if findings:
        for finding in findings[:3]:  # Cap at 3 findings in changelog
            entry += f"\n  - {finding}"

    # Insert into the appropriate section under [Unreleased]
    new_content = _insert_changelog_entry(content, section, entry)

    # Write back
    changelog_path.write_text(new_content, encoding="utf-8")
    log.info(f"[documentarian] changelog updated: {changelog_path} (PR #{pr_number})")


def _classify_pr_title(pr_title: str) -> str:
    """Classify a PR title into a changelog section.

    Uses conventional commit prefixes:
    - feat(...) → "Added"
    - fix(...) → "Fixed"
    - refactor/perf → "Changed"
    - docs → "Changed" (docs updates)
    - chore → "Changed"
    - Default → "Added"
    """
    title_lower = pr_title.strip().lower()
    if title_lower.startswith("fix") or title_lower.startswith("hotfix"):
        return "Fixed"
    elif title_lower.startswith("refactor") or title_lower.startswith("perf"):
        return "Changed"
    elif title_lower.startswith("docs"):
        return "Changed"
    elif title_lower.startswith("chore"):
        return "Changed"
    elif title_lower.startswith("feat"):
        return "Added"
    else:
        return "Added"


def _insert_changelog_entry(content: str, section: str, entry: str) -> str:
    """Insert a changelog entry under the specified section within [Unreleased].

    Handles:
    - Missing [Unreleased] section (creates it)
    - Missing section header within [Unreleased] (creates it)
    - Existing section (appends entry)
    """
    lines = content.split("\n")

    # Find [Unreleased] section start
    unreleased_idx = None
    next_section_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("## [Unreleased]"):
            unreleased_idx = i
        elif unreleased_idx is not None and line.strip().startswith("## ") and i > unreleased_idx:
            next_section_idx = i
            break

    if unreleased_idx is None:
        # No [Unreleased] section — add at top after title
        lines.insert(0, "## [Unreleased]")
        lines.insert(1, "")
        lines.insert(2, f"### {section}")
        lines.insert(3, entry)
        lines.insert(4, "")
        return "\n".join(lines)

    # Find the section header within [Unreleased]
    section_idx = None
    end_idx = next_section_idx if next_section_idx is not None else len(lines)

    for i in range(unreleased_idx + 1, end_idx):
        line = lines[i].strip()
        if line == f"### {section}":
            section_idx = i
            break

    if section_idx is None:
        # Section doesn't exist — insert before the next major section or end
        insert_at = end_idx
        lines.insert(insert_at, "")
        lines.insert(insert_at, f"### {section}")
        lines.insert(insert_at + 1, entry)
        return "\n".join(lines)

    # Section exists — append after the last bullet in that section
    # Find the end of the section (next ### or ## or end)
    section_end = end_idx
    for i in range(section_idx + 1, end_idx):
        if lines[i].strip().startswith("### ") or lines[i].strip().startswith("## "):
            section_end = i
            break

    # Insert before the section end
    lines.insert(section_end, entry)
    return "\n".join(lines)


def update_review_profile(owner: str, repo: str, db_path: str = DEFAULT_DB_PATH) -> None:
    """Update review_profiles table with latest merge timestamp.

    Increments the merge_count and updates last_merge_at for the repo.
    Creates the row if it doesn't exist yet.

    Args:
        owner: Repository owner.
        repo: Repository name.
        db_path: Optional override for the SQLite database path.
    """
    repo_full = f"{owner}/{repo}"
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_db(db_path)

    # UPSERT: insert or update
    conn.execute(
        """INSERT INTO review_profiles (repo_full_name, last_merge_at, merge_count, updated_at)
           VALUES (?, ?, 1, ?)
           ON CONFLICT(repo_full_name) DO UPDATE SET
               last_merge_at = excluded.last_merge_at,
               merge_count = merge_count + 1,
               updated_at = excluded.updated_at""",
        (repo_full, now, now),
    )
    conn.commit()
    log.info(f"[documentarian] review profile updated: {repo_full} at {now}")


def get_review_profile(owner: str, repo: str, db_path: str = DEFAULT_DB_PATH) -> Optional[dict]:
    """Get the review profile for a repo.

    Args:
        owner: Repository owner.
        repo: Repository name.
        db_path: Optional override for the SQLite database path.

    Returns:
        Dict with repo_full_name, last_merge_at, merge_count, updated_at,
        or None if no profile exists.
    """
    repo_full = f"{owner}/{repo}"
    conn = _get_db(db_path)
    row = conn.execute(
        "SELECT repo_full_name, last_merge_at, merge_count, updated_at FROM review_profiles WHERE repo_full_name = ?",
        (repo_full,),
    ).fetchone()

    if row is None:
        return None
    return {
        "repo_full_name": row[0],
        "last_merge_at": row[1],
        "merge_count": row[2],
        "updated_at": row[3],
    }