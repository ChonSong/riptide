"""
depth.py — PR review depth classification (shared by all entry paths).

WS-3 Stage 0: `classify_review_depth()` runs at the gate for EVERY entry
path (webhook → Companion, cron → Deepthink, command → review/fix). One
rule set, one answer to "how much review does this PR need?".

  TRIVIAL      → Tier 1 only (no LLM enrichment)
  INLINE_ONLY  → minimal review
  STANDARD     → full review
  ARCH         → full review + brooks-lint (high graphify impact)
"""

from __future__ import annotations

from enum import Enum


class ReviewDepth(Enum):
    """Determines how much LLM analysis a PR needs."""
    TRIVIAL = "trivial"         # <10 LOC, no logic changes → auto-approve
    INLINE_ONLY = "inline_only" # Single file, <50 LOC → minimal review
    STANDARD = "standard"       # Normal PR → full review
    ARCH = "arch"              # Multi-file, >200 LOC, high graphify impact → +brooks-lint


def classify_review_depth(data: dict) -> ReviewDepth:
    """
    Rule-based classification of PR depth from pre-gathered data.

    Args:
        data: dict with files_changed (list of {filename, additions,
              deletions}) and optional god_nodes (list of {name, edges}).

    Returns:
        ReviewDepth enum value
    """
    total_loc = sum(
        f.get("additions", 0) + f.get("deletions", 0) for f in data.get("files_changed", [])
    )
    files_changed = data.get("files_changed", [])
    god_nodes = data.get("god_nodes", [])

    # TRIVIAL: tiny change, no logic files
    logic_extensions = ('.py', '.js', '.ts', '.go', '.rs', '.java', '.c', '.cpp', '.h')
    has_logic = any(
        any(f.get("filename", "").endswith(ext) for ext in logic_extensions)
        for f in files_changed
    )
    if total_loc < 10 and not has_logic:
        return ReviewDepth.TRIVIAL

    # INLINE_ONLY: single file, small change
    if len(files_changed) == 1 and total_loc < 50:
        return ReviewDepth.INLINE_ONLY

    # ARCH: multi-file OR large OR touches high-impact god nodes
    if len(files_changed) > 5 or total_loc > 200:
        if any(g.get("edges", 0) > 20 for g in god_nodes):
            return ReviewDepth.ARCH

    return ReviewDepth.STANDARD


def select_skills(depth: ReviewDepth) -> list[str]:
    """
    Select which skills to load based on review depth.

    Args:
        depth: ReviewDepth classification

    Returns:
        List of skill names to pass as --skill flags
    """
    if depth == ReviewDepth.TRIVIAL:
        return []
    elif depth == ReviewDepth.INLINE_ONLY:
        return ["deep-think", "github-pr-lifecycle"]
    elif depth == ReviewDepth.STANDARD:
        return ["deep-think", "github-pr-lifecycle", "excalidraw"]
    elif depth == ReviewDepth.ARCH:
        return ["deep-think", "github-pr-lifecycle", "excalidraw", "brooks-lint"]
    return ["deep-think"]
