#!/usr/bin/env python3
"""
diagram_analyst.py — Worker 4: Diagram Analyst

Consumes Bot 2's findings and generates an annotated Excalidraw diagram
that communicates agent understanding of the PR's impact and risks.

Architecture:
    Bot 2 findings.json → Diagram Analyst → diagram_insights.json
                                                        ↓
                                              assemble_review.py (embeds in review)

The Diagram Analyst is deterministic where possible:
    - Rendering: programmatic Excalidraw JSON generation
    - Upload: reuses existing upload_excalidraw()
    - Layout: deterministic positioning based on findings structure

LLM is used only for:
    - Interpreting what to visualize (which files matter most)
    - Generating human-readable narrative
    - Determining connections between findings

Input: findings.json (from Bot 2)
Output: diagram_insights.json (consumed by assemble_review.py)
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from riptide.grafiphy.excalidraw_renderer import render_review, upload_excalidraw

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("riptide.diagram_analyst")

# ── Config ─────────────────────────────────────────────────────────────────

# Maximum number of findings to visualize (prevent diagram bloat)
MAX_VISUALIZED_FINDINGS = 10

# Canvas dimensions (matching excalidraw_renderer)
CANVAS_W = 900
MARGIN = 40
CONTENT_W = CANVAS_W - 2 * MARGIN

# Severity color mapping (fill, stroke, text)
SEVERITY_COLORS = {
    "critical": ("#ffc9c9", "#ef4444", "#b91c1c"),
    "warning": ("#ffd8a8", "#f59e0b", "#9a5030"),
    "suggestion": ("#d0bfff", "#8b5cf6", "#5b21b6"),
    "info": ("#a5d8ff", "#4a9eed", "#1e3a5f"),
    "approved": ("#b2f2bb", "#22c55e", "#15803d"),
}


def spawn_diagram_analyst(
    findings_path: str,
    pr_metadata: dict,
    output_path: Optional[str] = None,
) -> Optional[dict]:
    """
    Main entry point — generate an annotated diagram from Bot 2 findings.

    Args:
        findings_path: Path to findings.json from Bot 2
        pr_metadata: {owner, repo, number, title, author, total_loc}
        output_path: Where to write diagram_insights.json (optional)

    Returns:
        dict with diagram_url, annotations, narrative (or None on failure)
    """
    start = time.monotonic()

    # Load findings
    findings_file = Path(findings_path)
    if not findings_file.exists():
        log.error(f"Findings file not found: {findings_path}")
        return None

    try:
        findings = json.loads(findings_file.read_text())
    except Exception as e:
        log.error(f"Failed to parse findings: {e}")
        return None

    # Determine what to visualize
    plan = _plan_visualization(findings, pr_metadata)

    # Generate diagram
    diagram_path = _generate_diagram(findings, pr_metadata, plan)
    if not diagram_path:
        return None

    # Upload
    diagram_url = upload_excalidraw(diagram_path)
    if not diagram_url:
        log.warning("Diagram upload failed — returning None")
        return None

    # Build output
    elapsed = time.monotonic() - start
    result = {
        "version": 1,
        "pr_number": pr_metadata.get("number"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "diagram_url": diagram_url,
        "annotations": plan.get("annotations", []),
        "narrative": plan.get("narrative", ""),
        "confidence": plan.get("confidence", 0.5),
        "gaps": plan.get("gaps", []),
        "generation_time_s": round(elapsed, 2),
    }

    # Write output
    if output_path:
        Path(output_path).write_text(json.dumps(result, indent=2))
    else:
        default_output = (
            Path("/tmp")
            / f"riptide-diagram-insights-{pr_metadata.get('owner')}-"
            f"{pr_metadata.get('repo')}-{pr_metadata.get('number')}.json"
        )
        default_output.write_text(json.dumps(result, indent=2))

    log.info(
        f"Diagram generated for {pr_metadata.get('owner')}/{pr_metadata.get('repo')}"
        f"#{pr_metadata.get('number')} in {elapsed:.2f}s"
    )
    return result


def _plan_visualization(findings: dict, pr_metadata: dict) -> dict:
    """
    Determine what the diagram should emphasize based on findings.

    This is the deterministic planning step — it analyzes the structure
    of findings to decide layout and emphasis without LLM.
    """
    findings_list = findings.get("findings", [])

    # Cap findings to prevent bloat
    if len(findings_list) > MAX_VISUALIZED_FINDINGS:
        findings_list = findings_list[:MAX_VISUALIZED_FINDINGS]
        log.info(f"Capped findings from {len(findings.get('findings', []))} to {MAX_VISUALIZED_FINDINGS}")

    # Identify priority files (files with most/priority findings)
    file_counts = {}
    for f in findings_list:
        fname = f.get("file", "?")
        severity = f.get("severity", "info")
        weight = {"critical": 4, "warning": 3, "suggestion": 2, "info": 1}.get(severity, 1)
        file_counts[fname] = file_counts.get(fname, 0) + weight

    priority_files = sorted(file_counts.keys(), key=lambda k: file_counts[k], reverse=True)[:5]

    # Build annotations list
    annotations = []
    for i, f in enumerate(findings_list):
        severity = f.get("severity", "info")
        annotations.append({
            "element_id": f"callout_{i}",
            "type": "finding",
            "finding_idx": i,
            "file": f.get("file", "?"),
            "line": f.get("line", 0),
            "severity": severity,
            "message": f.get("message", f.get("title", "Unknown")),
        })

    # Build narrative from findings structure
    risk = findings.get("risk_assessment", "medium")
    blast_radius = findings.get("blast_radius", {})
    narrative = _build_narrative(pr_metadata, findings_list, risk, blast_radius)

    # Determine confidence based on data quality
    confidence = _calculate_confidence(findings)

    # Identify gaps (what we don't know)
    gaps = _identify_gaps(findings, pr_metadata)

    return {
        "priority_files": priority_files,
        "annotations": annotations,
        "narrative": narrative,
        "confidence": confidence,
        "gaps": gaps,
        "total_findings": len(findings_list),
    }


def _build_narrative(
    pr_metadata: dict,
    findings: list,
    risk: str,
    blast_radius: dict,
) -> str:
    """Build a human-readable narrative from findings structure."""
    pr_number = pr_metadata.get("number", "?")
    author = pr_metadata.get("author", "unknown")
    loc = pr_metadata.get("total_loc", 0)
    direct = blast_radius.get("direct_files", 0)
    indirect = blast_radius.get("indirect_files", 0)

    parts = [f"PR #{pr_number} by @{author} changes {loc} LOC across {direct} file(s)"]

    if indirect:
        parts.append(f"with {indirect} indirectly affected")

    critical = [f for f in findings if f.get("severity") == "critical"]
    warnings = [f for f in findings if f.get("severity") == "warning"]
    suggestions = [f for f in findings if f.get("severity") == "suggestion"]

    if critical:
        parts.append(f". 🔴 {len(critical)} critical issue(s)")
    if warnings:
        parts.append(f". 🟡 {len(warnings)} warning(s)")
    if suggestions:
        parts.append(f". 🟣 {len(suggestions)} suggestion(s)")

    parts.append(f". Risk level: **{risk}**.")

    return "".join(parts)


def _calculate_confidence(findings: dict) -> float:
    """Calculate agent confidence based on data quality."""
    score = 0.5  # baseline

    # More findings = more confident we understand the code
    num_findings = len(findings.get("findings", []))
    if num_findings > 0:
        score += 0.1
    if num_findings > 3:
        score += 0.1

    # Has blast radius data
    if findings.get("blast_radius"):
        score += 0.1

    # Has risk assessment
    if findings.get("risk_assessment"):
        score += 0.1

    # Has deterministic analysis
    if findings.get("deterministic"):
        score += 0.1

    return min(score, 1.0)


def _identify_gaps(findings: dict, pr_metadata: dict) -> list:
    """Identify knowledge gaps based on what we don't have."""
    gaps = []

    if not findings.get("blast_radius"):
        gaps.append("Blast radius not calculated")

    if not findings.get("risk_assessment"):
        gaps.append("Risk level not assessed")

    # Check for missing test coverage info
    has_test_info = any(
        f.get("category") == "testing" for f in findings.get("findings", [])
    )
    if not has_test_info:
        gaps.append("Test coverage impact unknown")

    # Check for missing security analysis
    has_security = any(
        f.get("category") == "security" for f in findings.get("findings", [])
    )
    if not has_security:
        gaps.append("Security implications not fully assessed")

    return gaps


def _generate_diagram(
    findings: dict,
    pr_metadata: dict,
    plan: dict,
) -> Optional[str]:
    """
    Generate the Excalidraw diagram with annotations.

    This is deterministic — it uses the visualization plan to position
    elements programmatically. No LLM needed for rendering.
    """
    findings_list = findings.get("findings", [])[:MAX_VISUALIZED_FINDINGS]

    # Build file_tree for PR SCOPE section
    file_tree = _build_file_tree(findings_list, pr_metadata)

    # Build repo_tree for CODEBASE DIRECTORY TREE section
    repo_tree = _build_repo_tree(pr_metadata)

    # Build graph_data for GRAPHIFY ANALYSIS section
    graph_data = {
        "god_nodes": [
            {"name": f.get("file", "?"), "edges": 0, "why": f.get("message", "")}
            for f in findings_list[:8]
        ],
        "communities": [],
    }

    # Build code_chunks for CODE CHUNKS / WHY section
    code_chunks = []
    for i, f in enumerate(findings_list[:3]):
        code_chunks.append({
            "code": f.get("detail", f.get("message", ""))[:200],
            "why": f.get("category", "unknown"),
            "file": f.get("file", "?"),
        })

    # Generate diagram
    with tempfile.TemporaryDirectory(prefix="riptide-diagram-") as tmp_dir:
        diagram_path = Path(tmp_dir) / "diagram.excalidraw"

        try:
            render_review(
                pr_data={
                    "number": pr_metadata.get("number", 0),
                    "title": pr_metadata.get("title", ""),
                    "repo": f"{pr_metadata.get('owner', '?')}/{pr_metadata.get('repo', '?')}",
                    "author": pr_metadata.get("author", ""),
                    "loc": pr_metadata.get("total_loc", 0),
                    "status": "OPEN",
                },
                graph_data=graph_data,
                file_tree=file_tree,
                repo_tree=repo_tree,
                findings=findings_list,
                human_narrative=plan.get("narrative", ""),
                code_chunks=code_chunks,
                output_path=str(diagram_path),
            )
            return str(diagram_path)
        except Exception as e:
            log.error(f"Diagram generation failed: {e}")
            return None


def _build_file_tree(findings: list, pr_metadata: dict) -> str:
    """Build a file tree string from findings for the PR SCOPE section."""
    lines = []
    seen_files = set()

    for f in findings:
        fname = f.get("file", "?")
        if fname in seen_files:
            continue
        seen_files.add(fname)

        severity = f.get("severity", "info")
        icon = {"critical": "🔴", "warning": "🟡", "suggestion": "🟣", "info": "🔵"}.get(severity, "⚪")
        lines.append(f"{icon} {fname}")

    return "\n".join(lines)


def _build_repo_tree(pr_metadata: dict) -> str:
    """Build a repo tree string for the CODEBASE DIRECTORY TREE section."""
    # Try to get from graphify-out
    workspace = Path.home() / "workspace" / pr_metadata.get("repo", "")
    graphify_dir = workspace / "graphify-out"

    if graphify_dir.exists():
        graph_json = graphify_dir / "graph.json"
        if graph_json.exists():
            try:
                data = json.loads(graph_json.read_text())
                nodes = data.get("nodes", [])
                if nodes:
                    return "\n".join(n.get("name", "?") for n in nodes[:50])
            except Exception:
                pass

    # Fallback: list key directories
    return "\n".join([
        "riptide/",
        "├── webhook.py",
        "├── companion.py",
        "├── deepthink.py",
        "├── fixer.py",
        "├── proofshotter.py",
        "├── interaction_handler.py",
        "├── diagram_analyst.py",
        "├── assemble_review.py",
        "├── state.py",
        "└── tests/",
    ])


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    """CLI entry point for testing."""
    import argparse

    parser = argparse.ArgumentParser(description="Riptide Diagram Analyst")
    parser.add_argument("--findings", required=True, help="Path to findings.json")
    parser.add_argument("--owner", required=True, help="Repo owner")
    parser.add_argument("--repo", required=True, help="Repo name")
    parser.add_argument("--pr", required=True, type=int, help="PR number")
    parser.add_argument("--title", default="", help="PR title")
    parser.add_argument("--author", default="", help="PR author")
    parser.add_argument("--loc", default=0, type=int, help="Lines changed")
    parser.add_argument("--output", default=None, help="Output path for insights JSON")
    args = parser.parse_args()

    pr_metadata = {
        "owner": args.owner,
        "repo": args.repo,
        "number": args.pr,
        "title": args.title,
        "author": args.author,
        "total_loc": args.loc,
    }

    result = spawn_diagram_analyst(args.findings, pr_metadata, args.output)
    if result:
        print(json.dumps(result, indent=2))
    else:
        print("ERROR: Diagram generation failed")
        exit(1)


if __name__ == "__main__":
    main()
