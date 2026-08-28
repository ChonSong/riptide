#!/usr/bin/env python3
"""
review_memory.py — Historical review context for Riptide.

Provides functions to store review outcomes and retrieve
common-finding patterns for injection into the Deepthink prompt.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from riptide.state import StateStore

log = logging.getLogger("riptide.review_memory")


def get_memory_context(owner: str, repo: str) -> str:
    """Get historical review context for injection into Deepthink prompt.

    Returns:
        String with common findings, or empty string if no history.
    """
    store = StateStore()
    return store.get_memory_context(owner, repo)


def store_review_outcome(
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    findings_count: int,
    critical_count: int,
    warning_count: int,
    verdict: str,
    metadata: Optional[dict] = None,
) -> None:
    """Store a review outcome in the database.

    Inserts a row into review_memory and updates the review_profiles
    aggregate for the repo.

    Args:
        owner: Repository owner.
        repo: Repository name.
        pr_number: PR number.
        head_sha: Commit SHA that was reviewed.
        findings_count: Total number of findings in the review.
        critical_count: Number of critical findings.
        warning_count: Number of warning findings.
        verdict: Review verdict (e.g., "pass", "fail", "warn").
        metadata: Optional dict of extra data to store.
    """
    store = StateStore()
    meta_str = json.dumps(metadata) if metadata is not None else None
    store.store_review_outcome(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        head_sha=head_sha,
        findings_count=findings_count,
        critical_count=critical_count,
        warning_count=warning_count,
        verdict=verdict,
        metadata=meta_str,
    )
    log.info(
        f"Stored review outcome for {owner}/{repo}#{pr_number}: "
        f"{findings_count} findings, verdict={verdict}"
    )


def get_review_profile(repo: str) -> Optional[dict]:
    """Get the review profile for a repo.

    Returns:
        Dict with total_reviews, common_findings, last_review_at, updated_at,
        or None if no history exists.
    """
    store = StateStore()
    return store.get_review_profile(repo)