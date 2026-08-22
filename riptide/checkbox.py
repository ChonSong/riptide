#!/usr/bin/env python3
# riptide/checkbox.py — Checkbox parsing, reset, footer generation for Riptide Companion.
#
# Provides utilities to parse checkbox state from comment bodies, detect toggles
# between edits, reset checkbacks to unchecked, and build the checkbox footer
# appended to Tier-1 TL;DR comments.

from __future__ import annotations

import re
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

CHECKBOX_ACTIONS: dict[str, str] = {
    "🔍 Trigger review": "review",
    "🛠 Fix issues": "fix",
    "📸 ProofShot": "visual",
    "🏷️ Relabel": "relabel",
}

ACTION_LABELS: dict[str, str] = {v: k for k, v in CHECKBOX_ACTIONS.items()}

CHECKBOX_FOOTER_PREFIX = "---"

DEFAULT_ACTIONS = ["review", "fix", "visual", "relabel"]


# ── Regex Patterns ───────────────────────────────────────────────────────────

# Matches a single checkbox line: "- [ ]" or "- [x]" or "- [X]"
CHECKBOX_RE = re.compile(r"^- \[([ xX])\] (.+)$", re.MULTILINE)

# Matches a checkbox block starting with "---" separator
CHECKBOX_BLOCK_RE = re.compile(
    r"^---\s*\n((?:- \[[ xX]\].*\n?)+)",
    re.MULTILINE,
)


# ── Parsing Functions ─────────────────────────────────────────────────────────


def parse_checkbox_state(body: str) -> dict[str, bool]:
    """
    Parse a comment body and return a mapping of label → checked state.

    Only recognizes labels defined in CHECKBOX_ACTIONS. Unknown checkbox
    labels are silently ignored.

    Args:
        body: The full comment body text.

    Returns:
        Dict mapping label strings to boolean checked state.
    """
    state: dict[str, bool] = {}
    for match in CHECKBOX_RE.finditer(body):
        checked_char = match.group(1).strip().lower()
        label = match.group(2).strip()
        if label in CHECKBOX_ACTIONS:
            state[label] = checked_char == "x"
    return state


def _parse_all_checkboxes(body: str) -> dict[str, bool]:
    """
    Parse ALL checkboxes (not just known actions) for toggle detection.

    Returns:
        Dict mapping full label strings to boolean checked state.
    """
    state: dict[str, bool] = {}
    for match in CHECKBOX_RE.finditer(body):
        checked_char = match.group(1).strip().lower()
        label = match.group(2).strip()
        state[label] = checked_char == "x"
    return state


def parse_checkbox_toggles(old_body: str, new_body: str) -> list[str]:
    """
    Compare two comment bodies and return labels that were checked ([ ] → [x]).

    Only reports transitions from unchecked to checked. Unchecks ([x] → [ ])
    are ignored for action triggering but reported by parse_checkbox_unchecks.

    Args:
        old_body: The previous comment body.
        new_body: The new comment body after edit.

    Returns:
        List of action labels that were toggled on.
    """
    old_state = _parse_all_checkboxes(old_body)
    new_state = _parse_all_checkboxes(new_body)

    toggled: list[str] = []
    for label, new_checked in new_state.items():
        old_checked = old_state.get(label, False)
        if new_checked and not old_checked and label in CHECKBOX_ACTIONS:
            toggled.append(label)
    return toggled


def parse_checkbox_unchecks(old_body: str, new_body: str) -> list[str]:
    """
    Compare two comment bodies and return labels that were unchecked ([x] → [ ]).

    Args:
        old_body: The previous comment body.
        new_body: The new comment body after edit.

    Returns:
        List of action labels that were toggled off.
    """
    old_state = _parse_all_checkboxes(old_body)
    new_state = _parse_all_checkboxes(new_body)

    unchecked: list[str] = []
    for label, new_checked in new_state.items():
        old_checked = old_state.get(label, False)
        if not new_checked and old_checked and label in CHECKBOX_ACTIONS:
            unchecked.append(label)
    return unchecked


# ── Reset Functions ───────────────────────────────────────────────────────────


def reset_checkboxes(body: str, labels: list[str]) -> str:
    """
    Reset specific checkbox labels to unchecked state ([x] → [ ]) in the body.

    Uses line-anchored regex replacement to ensure only exact labels are matched.

    Args:
        body: The full comment body text.
        labels: List of label strings to reset (e.g., "🔍 Trigger review").

    Returns:
        The body with specified checkboxes reset to [ ].
    """
    for label in labels:
        escaped = re.escape(label)
        # Replace [x] or [X] with [ ] for this specific label
        pattern = rf"^- \[([xX])\] {escaped}$"
        body = re.sub(pattern, f"- [ ] {label}", body, flags=re.MULTILINE)
    return body


def reset_all_checkboxes(body: str) -> str:
    """
    Reset ALL known checkbox action labels to unchecked state.

    Convenience wrapper around reset_checkboxes that resets all CHECKBOX_ACTIONS.

    Args:
        body: The full comment body text.

    Returns:
        The body with all known checkboxes reset to [ ].
    """
    return reset_checkboxes(body, list(CHECKBOX_ACTIONS.keys()))


# ── Footer Generation ────────────────────────────────────────────────────────


def build_checkbox_footer(
    actions: list[str],
    checked: Optional[list[str]] = None,
) -> str:
    """
    Build the checkbox footer block.

    Args:
        actions: List of action identifiers (review, fix, visual, relabel).
        checked: Optional list of action identifiers that should render as checked.

    Returns:
        The footer block string starting with "---\n" followed by checkbox lines.
    """
    checked_set = set(checked or [])
    lines = [CHECKBOX_FOOTER_PREFIX]
    for action in actions:
        label = ACTION_LABELS.get(action, action)
        mark = "x" if action in checked_set else " "
        lines.append(f"- [{mark}] {label}")
    return "\n".join(lines) + "\n"


def build_comment_with_footer(body: str, actions: list[str]) -> str:
    """
    Build a comment body with the checkbox footer appended.

    If the body already contains a checkbox footer (detected by CHECKBOX_BLOCK_RE),
    it is replaced. Otherwise, the footer is appended to the end.

    Args:
        body: The base comment body.
        actions: List of action identifiers for the footer.

    Returns:
        The body with a fresh checkbox footer.
    """
    footer = build_checkbox_footer(actions)

    # If body already has a checkbox block, replace it
    if CHECKBOX_BLOCK_RE.search(body):
        # Replace from the "---" before checkboxes to end
        body = CHECKBOX_BLOCK_RE.sub(footer.rstrip(), body)
        return body

    # No existing footer — append to end
    body = body.rstrip("\n")
    return body + "\n\n" + footer


def strip_checkbox_footer(body: str) -> str:
    """
    Remove the checkbox footer from a comment body.

    Removes everything from the first checkbox block match to the end of the body.

    Args:
        body: The full comment body.

    Returns:
        The body with checkbox footer removed.
    """
    match = CHECKBOX_BLOCK_RE.search(body)
    if match:
        return body[: match.start()].rstrip("\n")
    return body


# ── Webhook Payload Extraction ───────────────────────────────────────────────


def extract_comment_edit(payload: dict) -> Optional[dict]:
    """
    Extract comment edit info from an issue_comment edited webhook payload.

    Returns a normalized dict with:
        - body: new comment body
        - old_body: previous comment body
        - comment_id: the comment's ID
        - pr_number: the PR number
        - owner, repo: repository info
        - author: commenter login
        - is_pr: whether the issue is a PR

    Returns None if the payload is not a valid comment edit on a PR.
    """
    action = payload.get("action", "")
    if action != "edited":
        return None

    comment = payload.get("comment", {})
    issue = payload.get("issue", {})

    # Must be a PR comment
    if not issue.get("pull_request"):
        return None

    body = comment.get("body", "")
    old_body = (
        payload.get("changes", {}).get("body", {}).get("from", "")
    )

    if not body and not old_body:
        return None

    repo = payload.get("repository", {})
    owner, repo_name = _split_full_name(repo.get("full_name", ""))

    return {
        "body": body,
        "old_body": old_body,
        "comment_id": comment.get("id"),
        "pr_number": issue.get("number"),
        "owner": owner,
        "repo": repo_name,
        "author": comment.get("user", {}).get("login", ""),
        "is_pr": True,
    }


def extract_pr_body_edit(payload: dict) -> Optional[dict]:
    """
    Extract PR body edit info from a pull_request edited webhook payload.

    Returns a normalized dict similar to extract_comment_edit but for PR body edits.
    Returns None if the payload is not a valid PR body edit.
    """
    action = payload.get("action", "")
    if action != "edited":
        return None

    pr = payload.get("pull_request", {})
    if not pr:
        return None

    body = pr.get("body", "") or ""
    old_body = payload.get("changes", {}).get("body", {}).get("from", "") or ""

    if not body and not old_body:
        return None

    repo = payload.get("repository", {})
    owner, repo_name = _split_full_name(repo.get("full_name", ""))

    return {
        "body": body,
        "old_body": old_body,
        "comment_id": None,  # Not a comment edit
        "pr_number": pr.get("number"),
        "owner": owner,
        "repo": repo_name,
        "author": pr.get("user", {}).get("login", ""),
        "is_pr": True,
    }


def _split_full_name(full_name: str) -> tuple[str, str]:
    """Split 'owner/repo' into (owner, repo) tuple."""
    parts = full_name.split("/", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", ""