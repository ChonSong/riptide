#!/usr/bin/env python3
"""
interaction_handler.py — Unified @riptide-bot command router.

Extracts all inline command parsing from webhook.py into a single
entry point: handle_command(). Routes @riptide-bot commands to the
correct handler based on comment body content.

Supported commands:
  - @riptide-bot review          → handle_review_command() (deepthink.py)
  - @riptide-bot fix [desc]       → handle_fix_command() (fixer.py)
  - @riptide-bot proofshot        → handle_visual_command() (visual.py)
  - @riptide-bot relabel          → labeler.classify_pr() + add_labels_to_issue()
  - @riptide-bot explain <n>      → Fetch finding detail from latest review comment
  - @riptide-bot status           → Show bot queue depth + last review timestamps
  - @riptide-bot help             → Command reference card

Authorization:
  - review, relabel, explain, status, help: anyone
  - fix, proofshot: PR author or repo owner only

Returns:
  - Response string if this was a command (caller should post as comment).
  - None if this was not a recognized command.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

log = logging.getLogger("riptide.interaction_handler")

# ── Config ───────────────────────────────────────────────────────────────────

OUR_USERNAME = os.environ.get("RIPTIDE_OUR_USERNAME", "ChonSong")
OUR_ORG = os.environ.get("RIPTIDE_OUR_ORG", "ChonSong")

# Command patterns (compiled once)
FIX_RE = re.compile(r"@riptide-bot\s+fix\b(.*)", re.IGNORECASE | re.DOTALL)
VISUAL_RE = re.compile(r"@riptide-bot\s+(?:visual|proofshot)\b", re.IGNORECASE)
REVIEW_RE = re.compile(
    r"@riptide-bot\s+(review|deepthink|full\s*review)\b", re.IGNORECASE
)
RELABEL_RE = re.compile(r"@riptide-bot\s+relabel\b", re.IGNORECASE)
EXPLAIN_RE = re.compile(r"@riptide-bot\s+explain\s+(\d+)\b", re.IGNORECASE)
STATUS_RE = re.compile(r"@riptide-bot\s+status\b", re.IGNORECASE)
HELP_RE = re.compile(r"@riptide-bot\s+help\b", re.IGNORECASE)

# Combined pattern: any @riptide-bot command mention
ANY_COMMAND_RE = re.compile(r"@riptide-bot\s+(\S+)", re.IGNORECASE)

# Help text returned for @riptide-bot help
HELP_TEXT = """## 🦞 Riptide Bot Commands

| Command | Description | Auth |
|---------|-------------|------|
| `@riptide-bot review` | Trigger deep-think review | anyone |
| `@riptide-bot fix [desc]` | Auto-fix findings | author/owner |
| `@riptide-bot proofshot` | Trigger visual regression | author/owner |
| `@riptide-bot relabel` | Re-classify PR labels | anyone |
| `@riptide-bot explain <n>` | Detail finding #n | anyone |
| `@riptide-bot status` | Show bot queue status | anyone |
| `@riptide-bot help` | Show this help card | anyone |

**Aliases:** `review` = `deepthink` = `full review` | `proofshot` = `visual`
"""


def handle_command(
    payload: dict,
    delivery_id: str,
    comment_id: int,
    installation_id: int | None,
    owner: str,
    repo: str,
    pr_number: int,
    body: str,
    commenter: str,
    client: object = None,
) -> Optional[str]:
    """
    Route @riptide-bot commands to the correct handler.

    Args:
        payload: Full webhook payload dict.
        delivery_id: GitHub delivery ID (for logging).
        comment_id: Comment ID (for dedup/state).
        installation_id: GitHub App installation ID.
        owner: Repository owner login.
        repo: Repository name.
        pr_number: Pull request number.
        body: Comment body text.
        commenter: Login of the comment author.

    Returns:
        Response text if this was a command (caller should post as comment).
        None if this was not a recognized command.
    """
    if not body or "@riptide-bot" not in body.lower():
        return None

    # Determine PR author for authorization checks
    pr_author = _get_pr_author(payload, installation_id, owner, repo, pr_number)

    # Route commands in priority order
    # 1. Help (always available, no side effects)
    if HELP_RE.search(body):
        return _handle_help()

    # 2. Status (read-only)
    if STATUS_RE.search(body):
        return _handle_status(installation_id, owner, repo, pr_number)

    # 3. Review (anyone)
    if REVIEW_RE.search(body):
        return _handle_review(installation_id, owner, repo, pr_number, commenter, pr_author, client)

    # 4. Fix (auth: author/owner)
    if FIX_RE.search(body):
        return _handle_fix(
            installation_id, owner, repo, pr_number, commenter, pr_author, body
        )

    # 5. Proofshot/Visual (auth: author/owner)
    if VISUAL_RE.search(body):
        return _handle_proofshot(
            installation_id, owner, repo, pr_number, commenter, pr_author
        )

    # 6. Relabel (anyone)
    if RELABEL_RE.search(body):
        return _handle_relabel(installation_id, owner, repo, pr_number)

    # 7. Explain <n> (anyone)
    explain_match = EXPLAIN_RE.search(body)
    if explain_match:
        finding_num = int(explain_match.group(1))
        return _handle_explain(
            installation_id, owner, repo, pr_number, finding_num
        )

    # Not a recognized command (just a mention)
    return None


def _get_pr_author(
    payload: dict,
    installation_id: int | None,
    owner: str,
    repo: str,
    pr_number: int,
) -> str:
    """Extract PR author from payload or API."""
    # Try payload first (issue_comment events have issue.user for the author)
    issue = payload.get("issue", {})
    # For PRs, issue.user is the PR author
    user = issue.get("user", {})
    if isinstance(user, dict) and user.get("login"):
        return user["login"]
    # Fallback: issue.pull_request.user (rare, but some payloads have it)
    pr = issue.get("pull_request", {})
    if pr and isinstance(pr, dict):
        pr_user = pr.get("user", {})
        if isinstance(pr_user, dict) and pr_user.get("login"):
            return pr_user["login"]

    # Fallback: fetch from API
    try:
        from riptide.webhook import github_client
        client = github_client()
        pr_details = client.get_pr_details(installation_id, owner, repo, pr_number)  # type: ignore[arg-type]
        return pr_details.get("user", {}).get("login", "unknown")
    except Exception as e:
        log.warning(f"Could not fetch PR author: {e}")
        return "unknown"


def _is_authorized(commenter: str, pr_author: str, owner: str) -> bool:
    """Check if commenter is PR author, repo owner, or our bot."""
    return (
        commenter == OUR_USERNAME
        or commenter == pr_author
        or commenter == owner
    )


def _handle_help() -> str:
    """Return help text."""
    return HELP_TEXT


def _handle_status(
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
) -> str:
    """Show bot queue status."""
    try:
        from riptide.state import StateStore

        state = StateStore()
        pr_key = f"{owner}/{repo}#{pr_number}"

        # Get job status
        job = state.get_job_status(pr_number)
        heuristics = state.get_pr_heuristics(pr_key)

        lines = ["## 🤖 Riptide Status"]

        # Job status
        if job:
            status_emoji = {
                "pending": "⏳",
                "complete": "✅",
                "failed": "❌",
            }.get(job.get("status", ""), "❓")
            lines.append(
                f"**Latest job:** {status_emoji} {job.get('status', 'unknown')} "
                f"(tier: {job.get('tier', '?')})"
            )
        else:
            lines.append("**Latest job:** none")

        # Last reviewed
        reviewed_at = heuristics.get("reviewed_at")
        if reviewed_at:
            lines.append(f"**Last reviewed:** {reviewed_at}")
        else:
            lines.append("**Last reviewed:** never")

        # Pending jobs count (approximate via has_pending_job)
        lines.append(
            f"**PR key:** `{pr_key}`"
        )

        return "\n".join(lines)
    except Exception as e:
        log.error(f"Status command failed: {e}")
        return f"⚠️ Could not fetch status: {e}"


def _handle_review(
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
    pr_author: str,
    client: object = None,
) -> str:
    """Route to deepthink.handle_review_command()."""
    from riptide.deepthink import handle_review_command
    from riptide.webhook import github_client

    try:
        if client is None:
            client = github_client()
        result = handle_review_command(
            client, installation_id, owner, repo, pr_number, commenter
        )
        if result is None:
            return "⚠️ Review command returned no output."
        return result
    except Exception as e:
        log.error(f"Review command failed: {e}")
        return f"⚠️ Review command failed: {e}"


def _handle_fix(
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
    pr_author: str,
    body: str,
) -> str:
    """Route to fixer.handle_fix_command() with authorization."""
    if not _is_authorized(commenter, pr_author, owner):
        return (
            f"🚫 **Not authorized.** Only the PR author (@{pr_author}), the repo "
            f"owner (@{owner}), or @{OUR_USERNAME} can trigger `@riptide-bot fix` "
            f"on this PR."
        )

    from riptide.fixer import handle_fix_command
    from riptide.webhook import github_client

    try:
        match = FIX_RE.search(body)
        description = match.group(1).strip() if match else ""
        client = github_client()
        result = handle_fix_command(
            client, installation_id, owner, repo, pr_number, commenter, description
        )
        if result is None:
            return "⚠️ Fix command returned no output."
        return result
    except Exception as e:
        log.error(f"Fix command failed: {e}")
        return f"⚠️ Fix command failed: {e}"


def _handle_proofshot(
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
    pr_author: str,
) -> str:
    """Route to visual.handle_visual_command() with authorization."""
    if not _is_authorized(commenter, pr_author, owner):
        return (
            f"🚫 **Not authorized.** Only the PR author (@{pr_author}), the repo "
            f"owner (@{owner}), or @{OUR_USERNAME} can trigger "
            f"`@riptide-bot proofshot` on this PR."
        )

    from riptide.visual import handle_visual_command
    from riptide.webhook import github_client

    try:
        client = github_client()
        result = handle_visual_command(
            client, installation_id, owner, repo, pr_number, commenter
        )
        if result is None:
            return "⚠️ Proofshot command returned no output."
        return result
    except Exception as e:
        log.error(f"Proofshot command failed: {e}")
        return f"⚠️ Proofshot command failed: {e}"


def _handle_relabel(
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
) -> str:
    """Re-classify and apply labels to PR."""
    from riptide.webhook import github_client, get_labeler, _reconcile_labels

    try:
        client = github_client()
        labeler = get_labeler()
        if labeler is None:
            return "⚠️ Labeler not available (RIPTIDE_LABELER_ENABLED != '1')."

        pr_detail = client.get_pr_details(installation_id, owner, repo, pr_number)
        files = client.get_pr_files(installation_id, owner, repo, pr_number)
        labels = labeler.classify_pr(pr_detail, files, f"{owner}/{repo}")
        labeler.setup_labels_on_repo(installation_id, owner, repo, client)
        _reconcile_labels(
            client, installation_id, owner, repo, pr_number, labels, labeler
        )
        client.add_labels_to_issue(installation_id, owner, repo, pr_number, labels)
        return f"🏷️ Labels re-applied: {', '.join(labels)}"
    except Exception as e:
        log.error(f"Relabel command failed: {e}")
        return f"⚠️ Relabel command failed: {e}"


def _handle_explain(
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    finding_num: int,
) -> str:
    """Fetch detail for finding #n from latest review comment."""
    from riptide.webhook import github_client

    try:
        client = github_client()
        # Fetch comments on the PR
        comments = client.get_issue_comments(
            installation_id, owner, repo, pr_number
        )

        # Find the latest @riptide-bot review comment
        bot_comments = [
            c for c in comments
            if "@riptide-bot" in (c.get("body", "") or "")
            and "🔍" in (c.get("body", "") or "")
        ]

        if not bot_comments:
            return "⚠️ No @riptide-bot review comment found. Run `@riptide-bot review` first."

        # Sort by created_at descending (latest first)
        bot_comments.sort(
            key=lambda c: c.get("created_at", ""), reverse=True
        )
        latest = bot_comments[0]
        body_text = latest.get("body", "")

        # Parse findings (look for numbered sections like "1. " or "## Finding 1")
        findings = _parse_findings(body_text)

        if not findings:
            return "⚠️ Could not parse findings from the latest review comment."

        if finding_num < 1 or finding_num > len(findings):
            return (
                f"⚠️ Finding #{finding_num} not found. "
                f"Latest review has {len(findings)} finding(s)."
            )

        finding = findings[finding_num - 1]
        return (
            f"## 🔍 Finding #{finding_num}\n\n"
            f"{finding}\n\n"
            f"---\n"
            f"*From review comment {latest.get('id', '?')}*"
        )
    except Exception as e:
        log.error(f"Explain command failed: {e}")
        return f"⚠️ Explain command failed: {e}"


def _parse_findings(body: str) -> list[str]:
    """Parse findings from a review comment body.

    Supports formats:
    - Numbered: "1. Finding text" or "1) Finding text"
    - Header: "## Finding 1" or "### 1. Title"
    - Bullet: "- Finding text" or "* Finding text"
    """
    findings = []

    # Try numbered format: "1. text" or "1) text" on separate lines
    numbered = re.findall(
        r"(?:^|\n)\s*(?:\d+)[.\)]\s+(.+?)(?=(?:\n\s*(?:\d+)[.\)]|\Z))",
        body, re.DOTALL
    )
    if numbered:
        findings = [f.strip() for f in numbered if f.strip()]

    if not findings:
        # Try header format: "## Finding 1" or "### 1. Title"
        headers = re.findall(
            r"(?:^|\n)\s*#{2,3}\s+(?:\d+[.\)]\s*)?(.+?)(?=(?:\n\s*#{2,3}|\Z))",
            body, re.DOTALL
        )
        if headers:
            findings = [f.strip() for f in headers if f.strip()]

    if not findings:
        # Try bullet format
        bullets = re.findall(
            r"(?:^|\n)\s*[-*]\s+(.+?)(?=(?:\n\s*[-*]|\Z))",
            body, re.DOTALL
        )
        if bullets:
            findings = [f.strip() for f in bullets if f.strip()]

    return findings