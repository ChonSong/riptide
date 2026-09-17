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


# Extensions that make a change "logic". One definition, two users: the
# classifier below (any logic file defeats TRIVIAL) and anything that has to
# describe a diff to a human — the Companion's pass comment prints these kinds.
LOGIC_EXTENSIONS = ('.py', '.js', '.ts', '.go', '.rs', '.java', '.c', '.cpp', '.h')


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
    has_logic = any(
        any(f.get("filename", "").endswith(ext) for ext in LOGIC_EXTENSIONS)
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


# The human-readable half of each rule above: what the class means, why the PR
# landed in it. Kept next to the thresholds so the two cannot drift — the
# Companion's pass comment prints this, and a reader who sees "trivial" without
# a reason has learned nothing.
_DEPTH_REASONS = {
    ReviewDepth.TRIVIAL.value:
        "under 10 changed LOC and no logic files",
    ReviewDepth.INLINE_ONLY.value:
        "a single file with under 50 changed LOC",
    ReviewDepth.STANDARD.value:
        "not small enough to skip, and no high-impact god nodes in the blast radius",
    ReviewDepth.ARCH.value:
        "more than 5 files or 200+ changed LOC together with high graphify impact",
}


def describe_depth(depth) -> str:
    """
    One-sentence reason for a depth class, for humans reading a comment.

    Args:
        depth: a ReviewDepth, or its string value (`companion._depth` is a str).

    Returns:
        The reason sentence (no trailing period), or an honest statement that
        the class is unknown — never a guess.
    """
    key = depth.value if isinstance(depth, ReviewDepth) else str(depth or "").strip().lower()
    reason = _DEPTH_REASONS.get(key)
    if reason is None:
        return f"no depth rule matched `{depth}`"
    return reason


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
