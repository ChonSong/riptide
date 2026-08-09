#!/usr/bin/env python3
"""
visual.py — @riptide-bot visual: trigger visual regression tests via GitHub Actions.

Handles on-demand `@riptide-bot visual` commands via
handle_visual_command(), called directly from webhook.py when a user
comments the command on a PR.

Fires a workflow_dispatch event to a GitHub Actions workflow that runs
visual regression tests and posts results back as a check run or PR comment.
"""
from __future__ import annotations

import logging
import os
import re
import time

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("riptide.visual")

# ── Config ───────────────────────────────────────────────────────────────────

# Matches `@riptide-bot visual` command.
VISUAL_RE = re.compile(r"@riptide-bot\s+visual\b", re.IGNORECASE)

OUR_USERNAME = os.environ.get("RIPTIDE_OUR_USERNAME", "ChonSong")

# Target repo/workflow for visual regression tests
VISUAL_REPO = os.environ.get("RIPTIDE_VISUAL_REPO", "ChonSong/hermes-webui-tests")
VISUAL_WORKFLOW = os.environ.get("RIPTIDE_VISUAL_WORKFLOW", "visual.yml")


def handle_visual_command(
    client,
    installation_id: int | None,
    owner: str,
    repo: str,
    pr_number: int,
    commenter: str,
) -> str | None:
    """Handle @riptide-bot visual command — trigger visual regression tests.

    Called from webhook.py when a user comments @riptide-bot visual on a PR.
    Fetches PR details via the GitHub API client, checks authorization,
    fires a workflow_dispatch event to GitHub Actions, and returns a
    user-facing confirmation message (or error message).
    """
    try:
        pr_details = client.get_pr_details(installation_id, owner, repo, pr_number)
    except Exception as e:
        log.warning("Failed to fetch PR details for visual: %s", e)
        return (
            f"⚠️ Could not fetch PR #{pr_number} details ({e}). "
            f"Make sure the PR exists and the app is installed."
        )

    title = pr_details.get("title", f"PR #{pr_number}")
    author = pr_details.get("user", {}).get("login", "unknown")
    head_sha = pr_details.get("head", {}).get("sha", "")
    head_ref = pr_details.get("head", {}).get("ref", "")

    # Authorization gate: the COMMENTER must be the PR author, the repo owner,
    # or OUR_USERNAME.
    authorized = (
        commenter == OUR_USERNAME
        or commenter == author
        or commenter == owner
    )
    if not authorized:
        log.warning(
            "Unauthorized visual attempt by %s on %s/%s#%d (author=%s, owner=%s)",
            commenter, owner, repo, pr_number, author, owner,
        )
        return (
            f"🚫 **Not authorized.** Only the PR author (@{author}), the repo "
            f"owner (@{owner}), or @{OUR_USERNAME} can trigger `@riptide-bot visual` "
            f"on this PR. Your comment was logged."
        )

    # Fire workflow_dispatch to GitHub Actions
    if not installation_id:
        return f"⚠️ No installation ID available for #{pr_number}. Cannot dispatch workflow."

    try:
        _fire_visual_workflow(installation_id, client, owner, repo, pr_number, head_ref)
    except Exception as e:
        log.error("Failed to trigger visual workflow: %s", e)
        return f"⚠️ Failed to trigger visual regression for #{pr_number}: {e}"

    log.info(
        "Visual regression triggered for %s/%s#%d by %s",
        owner, repo, pr_number, commenter,
    )

    return (
        f"🎨 **Visual verification triggered for #{pr_number}!**\\n\\n"
        f"A GitHub Actions workflow has been dispatched to run visual regression tests. "
        f"Results will post here shortly.\\n\\n"
        f"**PR:** {title}\\n"
        f"**Author:** @{author}\\n"
        f"**Branch:** `{head_ref}`\\n"
        f"**Commit:** `{head_sha[:12]}`"
    )


def _fire_visual_workflow(
    installation_id: int,
    client,
    owner: str,
    repo: str,
    pr_number: int,
    head_ref: str,
):
    """Dispatch the visual regression GitHub Actions workflow."""
    url = (
        f"{client.base_url}/repos/{VISUAL_REPO}/actions/workflows/{VISUAL_WORKFLOW}/dispatches"
    )

    headers = client._headers(installation_id, {
        "Content-Type": "application/json",
        "Accept": "application/vnd.github.v3+json",
    })

    body = {
        "ref": "main",
        "inputs": {
            "target_repo": f"{owner}/{repo}",
            "target_branch": head_ref,
            "target_pr": str(pr_number),
        },
    }

    log.info(
        "Firing visual workflow: %s for %s/%s#%d (branch: %s)",
        VISUAL_REPO, owner, repo, pr_number, head_ref,
    )

    resp = requests.post(url, headers=headers, json=body, timeout=15)
    resp.raise_for_status()

    log.info("Visual workflow dispatched successfully for %s/%s#%d", owner, repo, pr_number)