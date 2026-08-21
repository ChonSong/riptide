#!/usr/bin/env python3
"""
riptide/checkbox.py — Interactive checkbox button system for Riptide Companion.

Parses GitHub webhook payloads to detect checkbox toggles in PR comments,
dispatches actions, and resets checkboxes so they can be clicked again.

Checkbox taxonomy:
    🔍 Trigger review  → deep-think review
    🛠 Fix issues       → fixer bot
    📸 ProofShot       → visual verification
    🏷️ Relabel          → re-classify & apply labels

Each checkbox label is stable text — never changes, so diff parsing is deterministic.
"""

from __future__ import annotations

import re
from typing import Optional

# ── Checkbox taxonomy ─────────────────────────────────────────────────────────
# Maps checkbox label → action identifier (used for dispatch + dedup).

CHECKBOX_ACTIONS: dict[str, str] = {
    "🔍 Trigger review": "review",
    "🛠 Fix issues": "fix",
    "📸 ProofShot": "visual",
    "🏷️ Relabel": "relabel",
}

# Reverse lookup: action → label
ACTION_LABELS: dict[str, str] = {v: k for k, v in CHECKBOX_ACTIONS.items()}

# ── Regex ─────────────────────────────────────────────────────────────────────

# Matches checkbox lines: "- [ ] label" or "- [x] label" or "- [X] label"
# Group 1: checkbox state (' ', 'x', or 'X')
# Group 2: label text (everything after "] ")
CHECKBOX_RE = re.compile(r"^- \[([ xX])\] (.+)$", re.MULTILINE)

# Footer separator + checkbox block
# Matches the entire checkbox block at the end of a comment
CHECKBOX_BLOCK_RE = re.compile(
    r"(?P<sep>^---$\s*)?(?P<block>(?:^- \[([ xX])\] .+$\s?)+)",
    re.MULTILINE,
)


# ── Parsing ───────────────────────────────────────────────────────────────────


def parse_checkbox_state(body: str) -> dict[str, bool]:
    """
    Parse all checkboxes from a comment body.

    Returns dict of {label: checked} for all checkboxes found.
    """
    state: dict[str, bool] = {}
    for match in CHECKBOX_RE.finditer(body):
        checked_char = match.group(1)
        label = match.group(2).strip()
        state[label] = checked_char in ("x", "X")
    return state


def parse_checkbox_toggles(old_body: str | None, new_body: str) -> list[str]:
    """
    Return labels of checkboxes that changed from [ ] → [x] (unchecked to checked).

    This is the core detection function. Only fires on user-initiated checks,
    not unchecks or unrelated edits.

    Args:
        old_body: Previous comment body (from changes.body.from), or None if not available.
        new_body: Current comment body.

    Returns:
        List of checkbox label strings that were freshly checked.
    """
    old_state = parse_checkbox_state(old_body) if old_body else {}
    new_state = parse_checkbox_state(new_body)

    toggled: list[str] = []
    for label, checked in new_state.items():
        if checked and not old_state.get(label, False):
            toggled.append(label)
    return toggled


def parse_checkbox_unchecks(old_body: str | None, new_body: str) -> list[str]:
    """
    Return labels of checkboxes that changed from [x] → [ ] (checked to unchecked).

    Useful for detecting user-initiated cancellations (not used for dispatch,
    but useful for logging/auditing).
    """
    old_state = parse_checkbox_state(old_body) if old_body else {}
    new_state = parse_checkbox_state(new_body)

    toggled: list[str] = []
    for label, checked in new_state.items():
        if not checked and old_state.get(label, False):
            toggled.append(label)
    return toggled


# ── Reset ─────────────────────────────────────────────────────────────────────


def reset_checkboxes(body: str, labels: list[str]) -> str:
    """
    Reset specified checkboxes to unchecked state.

    Uses line-anchored regex to avoid false positives (e.g., checkbox text
    appearing in code blocks or diff quotes).
    Labels not found in the body are silently ignored.

    Args:
        body: Comment body text.
        labels: List of checkbox labels to reset.

    Returns:
        Body text with specified checkboxes unchecked.
    """
    for label in labels:
        # Use line-anchored regex to match only checkbox lines, not arbitrary text
        pattern = rf"^- \[([xX])\] {re.escape(label)}$"
        body = re.sub(pattern, f"- [ ] {label}", body, flags=re.MULTILINE)
    return body


def reset_all_checkboxes(body: str) -> str:
    """
    Reset ALL checkboxes in the body to unchecked state.

    Used when re-rendering a comment (e.g., Tier-2 enrichment) to ensure
    all buttons start in the default unchecked state.
    """
    return CHECKBOX_RE.sub(lambda m: f"- [ ] {m.group(2)}", body)


# ── Footer generation ─────────────────────────────────────────────────────────


def build_checkbox_footer(
    actions: list[str] | None = None,
    checked: list[str] | None = None,
) -> str:
    """
    Build the checkbox footer block for a comment.

    Args:
        actions: List of action identifiers to include. Defaults to all.
        checked: List of action identifiers that should start checked (rare).

    Returns:
        Markdown string with checkbox block.
    """
    if actions is None:
        actions = list(CHECKBOX_ACTIONS.values())

    checked_set = set(checked or [])
    lines: list[str] = []

    for action in actions:
        label = ACTION_LABELS.get(action, action)
        state = "x" if action in checked_set else " "
        lines.append(f"- [{state}] {label}")

    return "\n".join(lines)


def build_comment_with_footer(body: str, actions: list[str] | None = None) -> str:
    """
    Append a checkbox footer to an existing comment body.

    If the body already has a checkbox block, it is replaced.
    Otherwise, the footer is appended after a separator.

    Args:
        body: Existing comment body.
        actions: List of action identifiers to include.

    Returns:
        Body with checkbox footer.
    """
    # Remove existing checkbox block if present
    body = strip_checkbox_footer(body)

    # Append footer
    footer = build_checkbox_footer(actions)
    return f"{body}\n\n---\n{footer}"


def strip_checkbox_footer(body: str) -> str:
    """
    Remove the checkbox footer block from a comment body.

    Returns the body with the separator and checkbox block removed.
    Uses finditer() to handle multiple checkbox blocks (e.g., from stale
    enrichment cycles) — strips from the first match to end of body.
    """
    # Strip ALL checkbox blocks, not just the first
    matches = list(CHECKBOX_BLOCK_RE.finditer(body))
    if matches:
        # Remove from the start of the first match to end of body
        start = matches[0].start("sep") if matches[0].group("sep") else matches[0].start("block")
        body = body[:start].rstrip()
    return body


# ── Webhook payload helpers ───────────────────────────────────────────────────


def extract_comment_edit(payload: dict) -> Optional[dict]:
    """
    Extract relevant fields from an issue_comment edited webhook payload.

    Returns None if the payload is not a comment edit or lacks required fields.

    Returned dict has:
        - comment_id: int
        - old_body: str (from changes.body.from)
        - new_body: str (from comment.body)
        - commenter: str (user.login)
        - is_bot: bool (user.type == "Bot")
        - pr_number: int
        - owner: str
        - repo: str
        - installation_id: int
    """
    action = payload.get("action", "")
    if action != "edited":
        return None

    comment = payload.get("comment", {})
    issue = payload.get("issue", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})

    # Must be a PR comment
    if "pull_request" not in issue:
        return None

    # Must have body changes
    changes = payload.get("changes", {})
    old_body = changes.get("body", {}).get("from")
    new_body = comment.get("body", "")

    if old_body is None:
        # No body change (e.g., only reaction added) — not a checkbox toggle
        return None

    user = comment.get("user", {})
    repo_full = repo.get("full_name", "")

    return {
        "comment_id": comment.get("id"),
        "old_body": old_body,
        "new_body": new_body,
        "commenter": user.get("login", "unknown"),
        "is_bot": user.get("type") == "Bot",
        "pr_number": issue.get("number"),
        "owner": repo_full.split("/")[0] if repo_full else "",
        "repo": repo.get("name", ""),
        "installation_id": installation.get("id"),
    }


def extract_pr_body_edit(payload: dict) -> Optional[dict]:
    """
    Extract relevant fields from a pull_request edited webhook payload.

    Returns None if the payload is not a PR body edit or lacks required fields.

    Returned dict has:
        - old_body: str (from changes.body.from)
        - new_body: str (from pull_request.body)
        - pr_number: int
        - owner: str
        - repo: str
        - installation_id: int
    """
    action = payload.get("action", "")
    if action != "edited":
        return None

    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})

    changes = payload.get("changes", {})
    old_body = changes.get("body", {}).get("from")
    new_body = pr.get("body", "")

    if old_body is None:
        return None

    repo_full = repo.get("full_name", "")

    return {
        "old_body": old_body,
        "new_body": new_body,
        "pr_number": pr.get("number"),
        "owner": repo_full.split("/")[0] if repo_full else "",
        "repo": repo.get("name", ""),
        "installation_id": installation.get("id"),
    }
