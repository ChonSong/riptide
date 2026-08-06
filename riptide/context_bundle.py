"""
context_bundle.py — Deterministic context-bundle pipeline for Riptide (Vision Pillar 1).

Pre-gathers ALL deterministic signals for a PR into one structured dict:
- DiffAnalyzer findings (security, complexity, error_handling, structure)
- Per-file concept classification (auth, payments, api, ui, tests, docs, config, infra, core)
- Aggregate stats (total_loc, files_count, concepts, touches_core, is_draft, has_repro_steps)
- test_status placeholder (Checks API integration is WS-3 scope)

Pure Python, no LLM. Deterministic output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from riptide.diff_analyzer import DiffAnalyzer, DiffReport


# ── Core files (touches_core detection) ─────────────────────────────────────

CORE_FILES = {
    "server.py",
    "webhook.py",
    "orchestrator.py",
    "deepthink.py",
    "companion.py",
    "diff_analyzer.py",
    "state.py",
}


# ── Concept classification ──────────────────────────────────────────────────

# Ordered by priority — first match wins.
CONCEPT_RULES: list[tuple[re.Pattern[str], str]] = [
    # Tests (high priority — test files should be classified as tests even if they contain auth/api keywords)
    # Matches: test_foo.py, foo_test.py, foo.spec.js, tests/foo, spec/bar, specs/baz, __tests__/qux
    (re.compile(r"(?:^|/|\\)(?:test_|_test\.|_spec\.|spec(?:s)?/|tests?/|__tests__/)", re.I), "tests"),
    # Auth
    (re.compile(r"(?:^|/|\\)(?:auth|login|logout|signup|register|password|token|session|oauth|jwt|permission|rbac|acl)", re.I), "auth"),
    # Payments
    (re.compile(r"(?:^|/|\\)(?:payment|billing|invoice|subscription|stripe|checkout|receipt|refund|plan|pricing)", re.I), "payments"),
    # API (exclude webhook — it's a core file handled below)
    (re.compile(r"(?:^|/|\\)(?:api|endpoint|route|controller|resolver|graphql|rest|middleware)", re.I), "api"),
    # UI
    (re.compile(r"\.(?:css|scss|less|html|jsx|tsx|vue|svelte|astro|svg)$", re.I), "ui"),
    (re.compile(r"(?:^|/|\\)(?:ui|component|layout|page|screen|widget|button|modal|navbar|sidebar|footer|header|theme|style)", re.I), "ui"),
    # Config (before docs so Dockerfile matches config not docs)
    (re.compile(r"\.(?:yml|yaml|toml|ini|cfg|env|lock)$", re.I), "config"),
    (re.compile(r"(?:^|/|\\)(?:config|\.github|docker|compose|settings|\.env)", re.I), "config"),
    # Docs
    (re.compile(r"\.(?:md|mdx|rst|txt)$", re.I), "docs"),
    (re.compile(r"(?:^|/|\\)(?:docs|readme|changelog|guide|tutorial|doc/|documentation)", re.I), "docs"),
    # Infra
    (re.compile(r"(?:^|/|\\)(?:infra|terraform|ansible|k8s|kubernetes|deploy|ci/|cd/|pipeline|dockerfile)", re.I), "infra"),
    # Core (deterministic module detection — these are riptide internals)
    (re.compile(r"(?:^|/|\\)(?:server|webhook|orchestrator|deepthink|companion|diff_analyzer|state)\.py$", re.I), "core"),
]

DEFAULT_CONCEPT = "core"


# ── Data structures ─────────────────────────────────────────────────────────


@dataclass
class DiffConcept:
    """A deterministic concept mapping for a single changed file."""

    filename: str
    concept: str
    additions: int
    deletions: int
    status: str          # "added", "modified", "removed"
    has_patch: bool


@dataclass
class ContextBundle:
    """Structured bundle of deterministic signals for a PR."""

    findings: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    verdict: str = "pass"
    summary: str = ""
    concepts: list[DiffConcept] = field(default_factory=list)
    aggregate: dict = field(default_factory=dict)
    test_status: dict = field(default_factory=lambda: {"available": False, "status": None})


# ── Concept classifier ──────────────────────────────────────────────────────


def classify_concept(filename: str) -> str:
    """Classify a changed file into a concept based on its filename/path.

    Uses ordered regex rules — first match wins. Falls back to "core" default.
    """
    for pattern, concept in CONCEPT_RULES:
        if pattern.search(filename):
            return concept
    return DEFAULT_CONCEPT


# ── Reproduction steps detection ────────────────────────────────────────────

REPRO_STEPS_RE = re.compile(
    r"(?:steps\s+to\s+reproduce|reproduction\s+steps|how\s+to\s+reproduce|repro\s+steps)",
    re.IGNORECASE,
)


def _has_repro_steps(body: str | None) -> bool:
    """Detect whether PR body includes reproduction steps."""
    if not body or not isinstance(body, str):
        return False
    return bool(REPRO_STEPS_RE.search(body))


# ── Main builder ────────────────────────────────────────────────────────────


def build_context_bundle(
    files: list[dict],
    graph_context: dict | None,
    pr_details: dict | None = None,
) -> dict:
    """Build a deterministic context bundle for a PR.

    Args:
        files: GitHub API file objects (filename, additions, deletions, status, patch).
        graph_context: Output of companion._get_graph_context() (or None).
        pr_details: Optional PR metadata (title, body, author, draft).

    Returns:
        Structured dict with findings, stats, verdict, concepts, aggregate, test_status.
    """
    pr_details = pr_details or {}

    # 1. Run DiffAnalyzer
    report: DiffAnalyzer = DiffAnalyzer()
    diff_report: DiffReport = report.analyze(files)

    findings = [
        {
            "category": f.category,
            "severity": f.severity,
            "message": f.message,
            "file": f.file,
            "line_hint": f.line_hint,
        }
        for f in diff_report.findings
    ]

    # 2. Per-file concept classification
    concepts: list[DiffConcept] = []
    for f in files:
        filename = f.get("filename", "")
        patch = f.get("patch") or ""
        concept = classify_concept(filename)
        concepts.append(DiffConcept(
            filename=filename,
            concept=concept,
            additions=f.get("additions", 0),
            deletions=f.get("deletions", 0),
            status=f.get("status", "modified"),
            has_patch=bool(patch.strip()),
        ))

    # 3. Aggregate stats
    total_loc = sum(f.get("additions", 0) + f.get("deletions", 0) for f in files)
    files_count = len(files)
    concepts_deduped = sorted(set(c.concept for c in concepts))
    touches_core = any(c.concept == "core" for c in concepts)

    title = pr_details.get("title", "")
    body = pr_details.get("body", "")
    is_draft = pr_details.get("draft", False)
    has_repro_steps = _has_repro_steps(body)

    aggregate = {
        "total_loc": total_loc,
        "files_count": files_count,
        "concepts": concepts_deduped,
        "touches_core": touches_core,
        "is_draft": is_draft,
        "has_repro_steps": has_repro_steps,
        "title": title,
        "author": pr_details.get("author", ""),
    }

    # 4. Include graph_context if available
    graph_raw = None
    if graph_context:
        graph_raw = graph_context.get("raw")

    # 5. Build bundle dict
    bundle = {
        "findings": findings,
        "stats": diff_report.stats,
        "verdict": diff_report.verdict,
        "summary": diff_report.summary,
        "concepts": [
            {
                "filename": c.filename,
                "concept": c.concept,
                "additions": c.additions,
                "deletions": c.deletions,
                "status": c.status,
                "has_patch": c.has_patch,
            }
            for c in concepts
        ],
        "aggregate": aggregate,
        "test_status": {"available": False, "status": None},
        "graph_context": graph_raw,
    }

    return bundle


# ── Summary helper ──────────────────────────────────────────────────────────


def concept_summary(bundle: dict) -> str:
    """Produce a high-level one-liner summary from a context bundle.

    Feeds the future Tier-1 comment.

    Example:
        "touches auth + payments, adds 2 test files, 412 LOC total"
    """
    agg = bundle.get("aggregate", {})
    concepts = agg.get("concepts", [])
    total_loc = agg.get("total_loc", 0)

    parts: list[str] = []

    # Concepts touched
    if concepts:
        parts.append("touches " + " + ".join(concepts))

    # Test files added
    test_added = sum(
        1 for c in bundle.get("concepts", [])
        if c.get("concept") == "tests" and c.get("status") == "added"
    )
    if test_added:
        parts.append(f"adds {test_added} test file{'s' if test_added > 1 else ''}")

    # Total LOC
    parts.append(f"{total_loc} LOC total")

    return ", ".join(parts)