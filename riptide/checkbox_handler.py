#!/usr/bin/env python3
# riptide/checkbox_handler.py — Toggle handler + action dispatch for checkbox system.
#
# Handles the webhook event when a user toggles a checkbox in a Riptide
# Companion TL;DR comment. Performs authorization, deduplication, action
# dispatch, and checkbox reset.

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from fastapi import Response

from .checkbox import (
    CHECKBOX_ACTIONS,
    parse_checkbox_toggles,
    reset_checkboxes,
    extract_comment_edit,
)
from .state import StateStore

log = logging.getLogger("riptide.checkbox_handler")

# The GitHub user/org that owns the bot — always authorized
BOT_OWNER = os.environ.get("RIPTIDE_OWNED_ORG", "ChonSong")


def handle_checkbox_toggle(
    payload: dict,
    delivery_id: str,
    comment_id: int,
) -> Response:
    """
    Main webhook handler for checkbox toggle events.

    Flow:
    1. Extract comment edit info from payload
    2. Skip own comments (bot user)
    3. Authorization gate (PR author, repo owner, or ChonSong)
    4. Dedup check (prevent double-trigger)
    5. Parse toggled checkboxes
    6. Dispatch actions
    7. Reset checkboxes after dispatch

    Args:
        payload: The webhook payload dict.
        delivery_id: The GitHub delivery ID for logging.
        comment_id: The comment ID from the webhook.

    Returns:
        FastAPI Response with status 200.
    """
    # 1. Extract comment edit info
    edit_info = extract_comment_edit(payload)
    if edit_info is None:
        return Response(status_code=200)

    body = edit_info["body"]
    old_body = edit_info["old_body"]
    pr_number = edit_info["pr_number"]
    owner = edit_info["owner"]
    repo = edit_info["repo"]
    commenter = edit_info["author"]

    # 2. Skip own comments
    if _is_bot_comment(payload):
        log.info(f"[{delivery_id}] Skipping bot's own comment edit")
        return Response(status_code=200)

    # 3. Authorization gate
    if not _is_authorized(commenter, payload):
        log.info(
            f"[{delivery_id}] Unauthorized checkbox toggle by {commenter} "
            f"on {owner}/{repo}#{pr_number}"
        )
        return Response(status_code=200)

    # 4. Dedup check
    state = _get_state_store()
    trigger_key = f"{owner}/{repo}#{pr_number}:{comment_id}"
    last_trigger = state.get_last_checkbox_trigger(trigger_key)
    now = time.time()
    if last_trigger and (now - last_trigger) < 5.0:
        log.info(
            f"[{delivery_id}] Dedup: checkbox trigger on "
            f"{owner}/{repo}#{pr_number} within 5s window"
        )
        return Response(status_code=200)

    # 5. Parse toggled checkboxes
    toggled_labels = parse_checkbox_toggles(old_body, body)
    if not toggled_labels:
        log.info(f"[{delivery_id}] No checkbox toggles detected")
        return Response(status_code=200)

    log.info(
        f"[{delivery_id}] Checkbox toggled by {commenter} on "
        f"{owner}/{repo}#{pr_number}: {toggled_labels}"
    )

    # 6. Dispatch actions
    for label in toggled_labels:
        action = CHECKBOX_ACTIONS.get(label)
        if action:
            _dispatch_action(
                action=action,
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                commenter=commenter,
                delivery_id=delivery_id,
                installation_id=payload.get("installation", {}).get("id"),
            )

    # Record trigger for dedup
    state.set_last_checkbox_trigger(trigger_key, now)

    # 7. Reset checkboxes (PATCH comment with unchecked boxes)
    _reset_checkboxes_in_comment(
        owner=owner,
        repo=repo,
        comment_id=comment_id,
        labels=toggled_labels,
        installation_id=payload.get("installation", {}).get("id"),
    )

    return Response(status_code=200)


def _is_bot_comment(payload: dict) -> bool:
    """
    Check if the comment was posted by the bot itself.

    Returns True if the comment's author is the bot user or was performed
    via the GitHub App.
    """
    comment = payload.get("comment", {})
    via_app = comment.get("performed_via_github_app") or {}
    app_id = via_app.get("id")
    if app_id and str(app_id) == str(os.environ.get("GITHUB_APP_ID", "4262983")):
        return True

    user = comment.get("user", {})
    if user.get("type") == "Bot":
        bot_login = user.get("login", "").lower()
        app_slug = os.environ.get("GITHUB_APP_SLUG", "octopus-selfhost")
        if bot_login == f"{app_slug}[bot]":
            return True

    return False


def _is_authorized(commenter: str, payload: dict) -> bool:
    """
    Check if the commenter is authorized to trigger checkbox actions.

    Authorized users:
    - The PR author
    - The repository owner
    - ChonSong (bot owner)
    """
    if commenter == BOT_OWNER:
        return True

    # Check if commenter is the PR author
    issue = payload.get("issue", {})
    pr_author = issue.get("user", {}).get("login", "")
    if commenter == pr_author:
        return True

    # Check if commenter is the repo owner
    repo = payload.get("repository", {})
    owner_login = repo.get("owner", {}).get("login", "")
    if commenter == owner_login:
        return True

    return False


def _dispatch_action(
    action: str,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
    delivery_id: str,
    installation_id: Optional[int],
) -> None:
    """
    Dispatch a checkbox action to the appropriate handler.

    Actions:
    - review → spawn deep-think review
    - fix → spawn fixer
    - visual → spawn proofshot
    - relabel → re-run labeler
    """
    log.info(
        f"[{delivery_id}] Dispatching action '{action}' for "
        f"{owner}/{repo}#{pr_number} by {commenter}"
    )

    try:
        if action == "review":
            _spawn_deepthink(owner, repo, pr_number, commenter, installation_id)
        elif action == "fix":
            _spawn_fix(owner, repo, pr_number, commenter, installation_id)
        elif action == "visual":
            _spawn_proofshot(owner, repo, pr_number, commenter, installation_id)
        elif action == "relabel":
            _run_relabel(owner, repo, pr_number, installation_id)
        else:
            log.warning(f"[{delivery_id}] Unknown action: {action}")
    except Exception as e:
        log.error(f"[{delivery_id}] Action dispatch failed for '{action}': {e}")


def _spawn_deepthink(
    owner: str, repo: str, pr_number: int, commenter: str, installation_id: Optional[int]
) -> None:
    """Spawn a deep-think review session."""
    from .deepthink import handle_review_command

    if not installation_id:
        return

    try:
        from .webhook import github_client

        client = github_client()
        result = handle_review_command(
            client, installation_id, owner, repo, pr_number, commenter
        )
        if result:
            client.post_pr_comment(
                installation_id, owner, repo, pr_number, result
            )
    except Exception as e:
        log.error(f"Failed to spawn deep-think for {owner}/{repo}#{pr_number}: {e}")


def _spawn_fix(
    owner: str, repo: str, pr_number: int, commenter: str, installation_id: Optional[int]
) -> None:
    """Spawn an autonomous fix session."""
    from .fixer import handle_fix_command

    try:
        from .webhook import github_client

        client = github_client()
        result = handle_fix_command(
            client, installation_id, owner, repo, pr_number, commenter, ""
        )
        if result:
            client.post_pr_comment(
                installation_id, owner, repo, pr_number, result
            )
    except Exception as e:
        log.error(f"Failed to spawn fix for {owner}/{repo}#{pr_number}: {e}")


def _spawn_proofshot(
    owner: str, repo: str, pr_number: int, commenter: str, installation_id: Optional[int]
) -> None:
    """Spawn a proofshot visual verification."""
    from .visual import handle_visual_command

    try:
        from .webhook import github_client

        client = github_client()
        result = handle_visual_command(
            client, installation_id, owner, repo, pr_number, commenter
        )
        if result:
            client.post_pr_comment(
                installation_id, owner, repo, pr_number, result
            )
    except Exception as e:
        log.error(f"Failed to spawn proofshot for {owner}/{repo}#{pr_number}: {e}")


def _run_relabel(
    owner: str, repo: str, pr_number: int, installation_id: Optional[int]
) -> None:
    """Re-run the labeler on the PR."""
    from .labeler import Labeler

    try:
        from .webhook import github_client

        client = github_client()
        labeler = Labeler()
        pr_detail = client.get_pr_details(installation_id, owner, repo, pr_number)
        files = client.get_pr_files(installation_id, owner, repo, pr_number)
        labels = labeler.classify_pr(pr_detail, files, f"{owner}/{repo}")
        labeler.setup_labels_on_repo(installation_id, owner, repo, client)
        client.add_labels_to_issue(installation_id, owner, repo, pr_number, labels)
        client.post_pr_comment(
            installation_id, owner, repo, pr_number,
            f"🏷️ Labels re-applied: {', '.join(labels)}"
        )
    except Exception as e:
        log.error(f"Failed to relabel {owner}/{repo}#{pr_number}: {e}")


def _reset_checkboxes_in_comment(
    owner: str,
    repo: str,
    comment_id: int,
    labels: list[str],
    installation_id: Optional[int],
) -> None:
    """
    Reset checkboxes in the comment by PATCHing with unchecked state.

    Fetches the current comment body, resets the specified checkboxes,
    and updates the comment via the GitHub API.
    """
    try:
        from .webhook import github_client

        client = github_client()
        # Get current comment body
        comment = client.get_comment(installation_id, owner, repo, comment_id)
        current_body = comment.get("body", "")

        # Reset the toggled checkboxes
        new_body = reset_checkboxes(current_body, labels)

        if new_body != current_body:
            client.update_pr_comment(installation_id, owner, repo, comment_id, new_body)
            log.info(f"Reset checkboxes in comment {comment_id}")
    except Exception as e:
        log.warning(f"Failed to reset checkboxes in comment {comment_id}: {e}")


# ── State Store Helpers ──────────────────────────────────────────────────────

_state_store: Optional[StateStore] = None


def _get_state_store() -> StateStore:
    """Get or create the StateStore instance."""
    global _state_store
    if _state_store is None:
        _state_store = StateStore()
    return _state_store