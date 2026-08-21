#!/usr/bin/env python3
"""
riptide/checkbox_handler.py — Handle checkbox toggle events from GitHub webhooks.

Listens for issue_comment edited events, parses checkbox toggles,
dispatches actions, applies authorization gates, and resets checkboxes.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from riptide.checkbox import (
    CHECKBOX_ACTIONS,
    parse_checkbox_toggles,
    reset_checkboxes,
    extract_comment_edit,
)

log = logging.getLogger("riptide.checkbox_handler")

# ── Authorization gates ──────────────────────────────────────────────────────
# Maps action → who can trigger it.
# "pr_author" = the PR author
# "repo_owner" = the repo owner (e.g., ChonSong)
# "anyone" = any authenticated user

ACTION_AUTHZ: dict[str, str] = {
    "review": "anyone",
    "fix": "pr_author",  # Only PR author can trigger fix (writes to their branch)
    "visual": "anyone",
    "relabel": "anyone",
}


def check_authorization(action: str, commenter: str, pr_author: str, owner: str) -> bool:
    """
    Check if the commenter is authorized to trigger this action.

    Args:
        action: Action identifier (review, fix, visual, relabel).
        commenter: GitHub login of the user who toggled the checkbox.
        pr_author: GitHub login of the PR author.
        owner: Repository owner login.

    Returns:
        True if authorized, False otherwise.
    """
    gate = ACTION_AUTHZ.get(action)
    if gate is None:
        return False  # Unknown action — deny by default
    if gate == "anyone":
        return True
    elif gate == "pr_author":
        return commenter == pr_author or commenter == owner
    elif gate == "repo_owner":
        return commenter == owner
    return False


def handle_checkbox_toggle(
    payload: dict,
    github_client,
    state_store,
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
    pr_author: str,
) -> list[str]:
    """
    Handle a checkbox toggle event from an issue_comment edited webhook.

    Parses which checkboxes were toggled, checks authorization, applies dedup,
    dispatches actions, and resets checkboxes.

    Args:
        payload: Full webhook payload.
        github_client: GitHub API client instance.
        state_store: StateStore instance for dedup.
        installation_id: GitHub App installation ID.
        owner: Repository owner.
        repo: Repository name.
        pr_number: PR number.
        commenter: GitHub login of the user who toggled.
        pr_author: GitHub login of the PR author.

    Returns:
        List of action identifiers that were triggered.
    """
    # Extract comment edit info
    edit_info = extract_comment_edit(payload)
    if edit_info is None:
        return []

    # Skip bot users (our own edits shouldn't trigger actions)
    if edit_info["is_bot"]:
        return []

    old_body = edit_info["old_body"]
    new_body = edit_info["new_body"]
    comment_id = edit_info["comment_id"]

    # Parse which checkboxes were toggled [ ] → [x]
    toggled_labels = parse_checkbox_toggles(old_body, new_body)
    if not toggled_labels:
        return []

    # Map labels to actions
    triggered_actions: list[str] = []
    authorized_actions: list[str] = []
    unauthorized_labels: list[str] = []
    deduped_labels: list[str] = []

    for label in toggled_labels:
        action = CHECKBOX_ACTIONS.get(label)
        if action is None:
            # Unknown checkbox label — skip
            continue

        # Dedup check: skip if same action was triggered in last 30s
        pr_key = f"{owner}/{repo}#{pr_number}"
        last_trigger = state_store.get_last_checkbox_trigger(pr_key, label)
        if last_trigger is not None and (time.time() - last_trigger) < 30:
            deduped_labels.append(label)
            continue

        # Authorization check
        if not check_authorization(action, commenter, pr_author, owner):
            unauthorized_labels.append(label)
            continue

        authorized_actions.append(action)

    # Dispatch authorized actions FIRST (before dedup/reset)
    failed_actions: list[str] = []
    for action in authorized_actions:
        try:
            _dispatch_action(
                action,
                github_client,
                installation_id,
                owner,
                repo,
                pr_number,
                commenter,
                pr_author,
            )
            triggered_actions.append(action)
        except Exception as e:
            log.error(f"Failed to dispatch checkbox action '{action}': {e}")
            failed_actions.append(action)
    authorized_actions = [a for a in authorized_actions if a not in failed_actions]

    # Now write dedup records (only for successfully dispatched actions)
    pr_key = f"{owner}/{repo}#{pr_number}"
    for action in triggered_actions:
        # Find the label for this action
        from riptide.checkbox import ACTION_LABELS

        label = ACTION_LABELS.get(action, action)
        state_store.set_last_checkbox_trigger(pr_key, label, time.time())

    # Reset checkboxes AFTER successful dispatch
    reset_labels = [label for label in toggled_labels if CHECKBOX_ACTIONS.get(label) is not None]
    if reset_labels:
        new_body_reset = reset_checkboxes(new_body, reset_labels)
        if new_body_reset != new_body:
            try:
                github_client.update_pr_comment(
                    installation_id, owner, repo, comment_id, new_body_reset
                )
            except Exception as e:
                log.warning(f"Failed to reset checkboxes on comment {comment_id}: {e}")

    # Log summary
    if unauthorized_labels:
        log.info(
            f"Checkbox unauthorized: {unauthorized_labels} by {commenter} on {owner}/{repo}#{pr_number}"
        )
    if deduped_labels:
        log.info(f"Checkbox deduped: {deduped_labels} on {owner}/{repo}#{pr_number}")

    return triggered_actions


def _dispatch_action(
    action: str,
    github_client,
    installation_id: int,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
    pr_author: str,
):
    """
    Dispatch a checkbox action to the appropriate handler.

    Args:
        action: Action identifier (review, fix, visual, relabel).
        github_client: GitHub API client.
        installation_id: GitHub App installation ID.
        owner: Repository owner.
        repo: Repository name.
        pr_number: PR number.
        commenter: GitHub login of the user who triggered.
        pr_author: GitHub login of the PR author.
    """
    if action == "review":
        from riptide.deepthink import handle_review_command

        result = handle_review_command(
            github_client, installation_id, owner, repo, pr_number, commenter
        )
        if result:
            github_client.post_pr_comment(installation_id, owner, repo, pr_number, result)

    elif action == "fix":
        from riptide.fixer import handle_fix_command

        result = handle_fix_command(
            github_client, installation_id, owner, repo, pr_number, commenter, ""
        )
        if result:
            github_client.post_pr_comment(installation_id, owner, repo, pr_number, result)

    elif action == "visual":
        from riptide.visual import handle_visual_command

        result = handle_visual_command(
            github_client, installation_id, owner, repo, pr_number, commenter
        )
        if result:
            github_client.post_pr_comment(installation_id, owner, repo, pr_number, result)

    elif action == "relabel":
        from riptide.webhook import get_labeler, _reconcile_labels

        labeler = get_labeler()
        if labeler:
            pr_detail = github_client.get_pr_details(installation_id, owner, repo, pr_number)
            files = github_client.get_pr_files(installation_id, owner, repo, pr_number)
            labels = labeler.classify_pr(pr_detail, files, f"{owner}/{repo}")
            labeler.setup_labels_on_repo(installation_id, owner, repo, github_client)
            _reconcile_labels(
                github_client, installation_id, owner, repo, pr_number, labels, labeler
            )
            github_client.add_labels_to_issue(installation_id, owner, repo, pr_number, labels)
            github_client.post_pr_comment(
                installation_id,
                owner,
                repo,
                pr_number,
                f"🏷️ Labels re-applied: {', '.join(labels)}",
            )
