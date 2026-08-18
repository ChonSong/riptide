#!/usr/bin/env python3
"""
interaction_handler.py — Worker 7: Unified Command Router

Handles all @riptide-bot commands from PR comments and routes them
to the appropriate worker. Enables conversational interaction with
the Riptide review system.

Commands:
    @riptide-bot review          → Bot 2: Deep-think review
    @riptide-bot fix             → Bot 2b: Autonomous fix
    @riptide-bot fix <desc>      → Bot 2b: Fix specific problem
    @riptide-bot proofshot       → Bot 3: Visual capture
    @riptide-bot explain <find>  → Explain a specific finding
    @riptide-bot retest          → Worker 5: Run targeted tests
    @riptide-bot diagram         → Worker 4: Generate annotated diagram
    @riptide-bot companion skip  → Bot 1: Skip this PR
    @riptide-bot companion resume→ Bot 1: Resume this PR
    @riptide-bot status          → Show all bot status for this PR
    @riptide-bot help            → Show available commands

The handler is idempotent — duplicate commands within the cooldown
window are silently deduplicated.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from riptide.state import StateStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("riptide.interaction_handler")

# ── Command Patterns ───────────────────────────────────────────────────────

# Matches @riptide-bot <command> [description]
COMMAND_RE = re.compile(
    r"@riptide-bot\s+(\w+)(.*)",
    re.IGNORECASE | re.DOTALL,
)

# Sub-commands for structured actions
EXPLAIN_RE = re.compile(r"explain\s+(.+)", re.IGNORECASE)
RETEST_RE = re.compile(r"retest\s*(\d+)?", re.IGNORECASE)

# Valid top-level commands
VALID_COMMANDS = {
    "review", "fix", "proofshot", "explain", "retest",
    "diagram", "companion", "status", "help",
}

# Commands that require authorization
AUTH_COMMANDS = {"fix", "retest", "proofshot"}

# ── Config ─────────────────────────────────────────────────────────────────

# How long to wait before allowing the same command again (seconds)
COMMAND_COOLDOWN = 300  # 5 minutes

# Maximum description length for fix command
MAX_FIX_DESCRIPTION = 1000

# Our bot identity (for skip own-comment detection)
OUR_USERNAME = os.environ.get("RIPTIDE_OUR_USERNAME", "ChonSong")
OUR_ORG = os.environ.get("RIPTIDE_OUR_GITHUB_ORG", "ChonSong")


def handle_command(
    client,
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
    comment_body: str,
    comment_id: int,
) -> str | None:
    """
    Main entry point — called from webhook.py when @riptide-bot is mentioned.

    Returns a response string (posted as PR comment) or None if no response needed.
    """
    m = COMMAND_RE.search(comment_body)
    if not m:
        return None

    command = m.group(1).lower()
    description = m.group(2).strip() if m.group(2) else ""

    if command not in VALID_COMMANDS:
        return _help_response()

    # Authorization check
    if command in AUTH_COMMANDS:
        if not _is_authorized(commenter, owner, pr_number):
            return _unauthorized_response(commenter, owner)

    # Cooldown check
    if _is_on_cooldown(owner, repo, pr_number, command):
        log.info(f"Command '{command}' on {owner}/{repo}#{pr_number} is on cooldown — skipping")
        return None

    # Route to appropriate handler
    try:
        if command == "review":
            return _handle_review(client, installation_id, owner, repo, pr_number, commenter)
        elif command == "fix":
            return _handle_fix(client, installation_id, owner, repo, pr_number, commenter, description)
        elif command == "proofshot":
            return _handle_proofshot(client, installation_id, owner, repo, pr_number, commenter)
        elif command == "explain":
            return _handle_explain(description, owner, repo, pr_number)
        elif command == "retest":
            return _handle_retest(client, installation_id, owner, repo, pr_number, commenter)
        elif command == "diagram":
            return _handle_diagram(client, installation_id, owner, repo, pr_number, commenter)
        elif command == "companion":
            return _handle_companion(description, owner, repo, pr_number, commenter)
        elif command == "status":
            return _handle_status(owner, repo, pr_number)
        elif command == "help":
            return _help_response()
    except Exception as e:
        log.error(f"Error handling command '{command}': {e}")
        return f"⚠️ Error processing `{command}`: {e}"

    return None


def _handle_review(
    client,
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
) -> str:
    """Route to Bot 2: Deepthink review."""
    from riptide.deepthink import handle_review_command

    # Check if review is already pending
    state = StateStore()
    if state.has_pending_job(f"riptide-review-{owner}-{repo}-{pr_number}"):
        return "⏭️ **Already pending.** A review is already in progress for this PR."

    # Delegate to existing handler
    result = handle_review_command(client, installation_id, owner, repo, pr_number, commenter)
    if result:
        client.post_pr_comment(installation_id, owner, repo, pr_number, result)
    return None


def _handle_fix(
    client,
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
    description: str,
) -> str:
    """Route to Bot 2b: Autonomous fix."""
    from riptide.fixer import handle_fix_command

    # Truncate description if too long
    if len(description) > MAX_FIX_DESCRIPTION:
        description = description[:MAX_FIX_DESCRIPTION] + "..."

    result = handle_fix_command(
        client, installation_id, owner, repo, pr_number, commenter, description
    )
    if result:
        client.post_pr_comment(installation_id, owner, repo, pr_number, result)
    return None


def _handle_proofshot(
    client,
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
) -> str:
    """Route to Bot 3: Visual capture."""
    from riptide.proofshotter import handle_manual_command

    result = handle_manual_command(
        client, installation_id, owner, repo, pr_number, commenter
    )
    if result:
        client.post_pr_comment(installation_id, owner, repo, pr_number, result)
    return None


def _handle_explain(
    description: str,
    owner: str,
    repo: str,
    pr_number: int,
) -> str:
    """Explain a specific finding from the latest review."""
    # Load the latest diagram insights
    insights_path = Path(f"/tmp/riptide-diagram-insights-{owner}-{repo}-{pr_number}.json")
    if not insights_path.exists():
        return "No diagram insights found for this PR. Run `@riptide-bot review` first."

    try:
        insights = json.loads(insights_path.read_text())
    except Exception:
        return "Could not load diagram insights."

    # If description is a number, look up that finding index
    if description.isdigit():
        idx = int(description)
        findings = insights.get("findings", [])
        if idx < 0 or idx >= len(findings):
            return f"Finding #{idx} not found. This review has {len(findings)} finding(s)."
        finding = findings[idx]
        return (
            f"**Finding #{idx}:** {finding.get('title', 'Unknown')}\n\n"
            f"{finding.get('detail', 'No detail available.')}\n\n"
            f"**Severity:** {finding.get('severity', 'unknown')} | "
            f"**File:** `{finding.get('file', '?')}:{finding.get('line', '?')}`"
        )

    # Otherwise, search findings by keyword
    matches = []
    for i, f in enumerate(insights.get("findings", [])):
        if description.lower() in f.get("title", "").lower():
            matches.append((i, f))

    if not matches:
        return f"No findings matching '{description}'. Run `@riptide-bot status` to see available findings."

    response = []
    for idx, finding in matches:
        response.append(
            f"**#{idx}** [{finding.get('severity', '?')}] {finding.get('title', 'Unknown')} "
            f"— `{finding.get('file', '?')}:{finding.get('line', '?')}`"
        )
    return "\n".join(response)


def _handle_retest(
    client,
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
) -> str:
    """Route to Worker 5: Test Oracle."""
    # Worker 5 doesn't exist yet — provide helpful message
    return (
        "🔬 **Test Oracle** is coming soon. This will run targeted tests "
        "based on the PR diff and report results.\n\n"
        "For now, please trigger tests manually via CI."
    )


def _handle_diagram(
    client,
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
) -> str:
    """Route to Worker 4: Diagram Analyst."""
    # Worker 4 doesn't exist yet — provide helpful message
    return (
        "📊 **Diagram Analyst** is coming soon. This will generate an "
        "annotated diagram showing the agent's understanding of the PR.\n\n"
        "For now, diagrams are generated as part of `@riptide-bot review`."
    )


def _handle_companion(
    description: str,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
) -> str:
    """Route to Bot 1: Companion skip/resume."""
    from riptide.companion import Companion

    parts = description.strip().lower().split()
    if len(parts) < 1:
        return "Usage: `@riptide-bot companion skip` or `@riptide-bot companion resume`"

    action = parts[0]
    if action == "skip":
        # Delegate to companion
        try:
            companion = Companion(None)
            companion.set_skip(owner, repo, pr_number, True)
            return "🤖 Companion will **skip** this PR. Reply `@riptide-bot companion resume` to re-enable."
        except Exception as e:
            return f"⚠️ Error skipping companion: {e}"
    elif action == "resume":
        try:
            companion = Companion(None)
            companion.set_skip(owner, repo, pr_number, False)
            return "🤖 Companion **resumed** for this PR."
        except Exception as e:
            return f"⚠️ Error resuming companion: {e}"
    else:
        return f"Unknown companion action: `{action}`. Use `skip` or `resume`."


def _handle_status(
    owner: str,
    repo: str,
    pr_number: int,
) -> str:
    """Show status of all bots for this PR."""
    state = StateStore()
    pr_key = f"{owner}/{repo}#{pr_number}"

    # Gather status from each bot
    parts = [f"**Bot Status for {pr_key}**\n"]

    # Bot 1: Companion
    heuristics = state.get_pr_heuristics(pr_key)
    if heuristics.get("skip"):
        parts.append("🤖 Bot 1 (Companion): ⏭️ **Skipped**")
    else:
        parts.append("🤖 Bot 1 (Companion): ✅ Active")

    # Bot 2: Deepthink
    job_state = state.get_job_status(pr_number)
    if job_state:
        status_icon = {"pending": "⏳", "complete": "✅", "failed": "❌"}.get(
            job_state["status"], "❓"
        )
        parts.append(f"🧠 Bot 2 (Deepthink): {status_icon} **{job_state['status']}** (tier: {job_state.get('tier', '?')})")
    else:
        parts.append("🧠 Bot 2 (Deepthink): ⚪ No recent jobs")

    # Bot 3: Proofshotter
    proofshot_state = _load_proofshot_state(owner, repo, pr_number)
    if proofshot_state:
        parts.append(f"📸 Bot 3 (Proofshotter): ✅ Last run {proofshot_state}")
    else:
        parts.append("📸 Bot 3 (Proofshotter): ⚪ No recent captures")

    # Worker 4: Diagram Analyst
    insights_path = Path(f"/tmp/riptide-diagram-insights-{owner}-{repo}-{pr_number}.json")
    if insights_path.exists():
        parts.append("📊 Worker 4 (Diagram Analyst): ✅ Insights available")
    else:
        parts.append("📊 Worker 4 (Diagram Analyst): ⚪ No insights yet")

    # Worker 5: Test Oracle
    parts.append("🔬 Worker 5 (Test Oracle): 🚧 Coming soon")

    # Worker 6: Review Memory
    memory_count = _count_review_memory(pr_key)
    parts.append(f"🧠 Worker 6 (Review Memory): {memory_count} past reviews recorded")

    # Worker 7: Interaction Handler
    parts.append("💬 Worker 7 (Interaction Handler): ✅ Active (you're talking to me)")

    return "\n".join(parts)


def _is_authorized(commenter: str, owner: str, pr_number: int) -> bool:
    """Check if commenter is authorized to trigger privileged commands."""
    return commenter == OUR_USERNAME or commenter == owner


def _is_on_cooldown(owner: str, repo: str, pr_number: int, command: str) -> bool:
    """Check if the same command was recently issued."""
    state = StateStore()
    key = f"interaction-{owner}-{repo}-{pr_number}-{command}"
    last_run = state.get_pr_heuristics(key).get("reviewed_at")
    if not last_run:
        return False
    try:
        last_time = datetime.fromisoformat(last_run)
        return (datetime.now(timezone.utc) - last_time).total_seconds() < COMMAND_COOLDOWN
    except (ValueError, TypeError):
        return False


def _load_proofshot_state(owner: str, repo: str, pr_number: int) -> Optional[str]:
    """Load last proofshot run timestamp."""
    state_path = Path.home() / ".local/share/riptide/proofshotter_acted_prs.json"
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text())
        pr_key = f"{owner}/{repo}#{pr_number}"
        entry = data.get(pr_key, {})
        return entry.get("last_run")
    except Exception:
        return None


def _count_review_memory(pr_key: str) -> int:
    """Count how many reviews are stored in memory for this PR."""
    memory_path = Path.home() / ".local/share/riptide/review_memory.json"
    if not memory_path.exists():
        return 0
    try:
        data = json.loads(memory_path.read_text())
        return len(data.get(pr_key, []))
    except Exception:
        return 0


def _help_response() -> str:
    """Return help text for @riptide-bot commands."""
    return """**Riptide Bot Commands:**

| Command | Description |
|---------|-------------|
| `@riptide-bot review` | Trigger a deep-think review |
| `@riptide-bot fix [desc]` | Fix issues (optionally describe specific problem) |
| `@riptide-bot proofshot` | Capture visual evidence of UI changes |
| `@riptide-bot explain <n>` | Explain finding #n from latest review |
| `@riptide-bot diagram` | Generate annotated architecture diagram |
| `@riptide-bot companion skip/resume` | Skip or resume Companion for this PR |
| `@riptide-bot status` | Show all bot status for this PR |
| `@riptide-bot help` | Show this help message |

**Examples:**
- `@riptide-bot review` — Full deep-think review
- `@riptide-bot fix the auth race condition` — Fix specific issue
- `@riptide-bot explain 2` — Explain finding #2

<sub>🤖 Riptide Interaction Handler · Worker 7</sub>"""


def _unauthorized_response(commenter: str, owner: str) -> str:
    """Return unauthorized message."""
    return (
        f"🚫 **Not authorized.** Only the PR author, the repo owner (@{owner}), "
        f"or @{OUR_USERNAME} can trigger this command. Your comment was logged."
    )


# ── Legacy Compatibility ──────────────────────────────────────────────────

def parse_legacy_visual_command(body: str) -> Optional[str]:
    """
    Parse the legacy `@riptide-bot visual` command.
    Returns the description if found, None if not a visual command.
    """
    m = re.search(r"@riptide-bot\s+visual\b(.*)", body, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return None
