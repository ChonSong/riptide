#!/usr/bin/env python3
"""diagram_analyst.py — Generate annotated Excalidraw diagrams from review findings.

Wraps the pipeline DiagramBuilder to produce a visual summary of PR review
findings, uploads it to excalidraw.com, and returns structured metadata
(narrative, confidence, gaps, annotations) for the review comment.

Standalone worker — does not modify any existing pipeline code.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy import to allow mocking in tests
try:
    from riptide.pipeline.diagram_builder import build_review_diagram
except ImportError:
    build_review_diagram = None  # type: ignore[assignment]


def _build_pr_data(
    owner: str,
    repo: str,
    pr_number: int,
    pr_title: str,
    pr_author: str,
    total_loc: int,
) -> dict:
    """Build the pr_data dict expected by DiagramBuilder."""
    return {
        "title": pr_title or f"PR #{pr_number}",
        "number": pr_number,
        "repo": f"{owner}/{repo}",
        "author": {"login": pr_author},
        "loc": total_loc,
        "status": "open",
    }


def _build_context_bundle(findings: list[dict]) -> dict:
    """Build a minimal context_bundle from findings for DiagramBuilder."""
    files = set()
    for f in findings:
        fname = f.get("file", "")
        if fname:
            files.add(fname)

    concepts = []
    for fname in sorted(files):
        concepts.append({
            "filename": fname,
            "additions": 0,
            "deletions": 0,
            "status": "modified",
            "concept": "core",
        })

    # Determine verdict from findings severity
    severities = {f.get("severity", "info") for f in findings}
    if "critical" in severities:
        verdict = "block"
    elif "warning" in severities:
        verdict = "review"
    else:
        verdict = "pass"

    return {
        "aggregate": {
            "total_loc": sum(f.get("line", 0) for f in findings),
            "concepts": sorted(files),  # list of strings for join()
            "files_count": len(files),
        },
        "verdict": verdict,
        "concepts": concepts,  # list of dicts for file display
    }


def _build_narrative(
    findings: list[dict],
    owner: str,
    repo: str,
    pr_number: int,
    pr_title: str,
    pr_author: str,
    total_loc: int,
) -> dict:
    """Build a narrative summary from findings."""
    severity_counts: dict[str, int] = {}
    for f in findings:
        sev = f.get("severity", "info")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    critical = severity_counts.get("critical", 0)
    warning = severity_counts.get("warning", 0)
    suggestion = severity_counts.get("suggestion", 0)
    info = severity_counts.get("info", 0)

    summary_parts = []
    if critical:
        summary_parts.append(f"{critical} critical")
    if warning:
        summary_parts.append(f"{warning} warning")
    if suggestion:
        summary_parts.append(f"{suggestion} suggestion")
    if info:
        summary_parts.append(f"{info} info")

    summary = ", ".join(summary_parts) if summary_parts else "no issues"

    files_affected = sorted({
        f.get("file", "") for f in findings if f.get("file")
    })

    return {
        "summary": f"PR #{pr_number} by {pr_author}: {summary} in {len(files_affected)} file(s)",
        "title": pr_title,
        "author": pr_author,
        "repo": f"{owner}/{repo}",
        "total_loc": total_loc,
        "findings_count": len(findings),
        "severity_breakdown": severity_counts,
        "files_affected": files_affected,
    }


def _build_annotations(findings: list[dict]) -> list[dict]:
    """Map each finding to a diagram annotation."""
    annotations = []
    for i, f in enumerate(findings):
        annotations.append({
            "index": i,
            "severity": f.get("severity", "info"),
            "title": f.get("title", ""),
            "file": f.get("file", ""),
            "line": f.get("line", 0),
            "detail": f.get("detail", ""),
            "element_id": f"finding_{i}",
        })
    return annotations


def _compute_confidence(findings: list[dict]) -> float:
    """Compute a confidence score (0-1) based on finding quality."""
    if not findings:
        return 0.5

    score = 0.5
    for f in findings:
        # Boost for findings with file references
        if f.get("file"):
            score += 0.05
        # Boost for findings with line numbers
        if f.get("line"):
            score += 0.03
        # Boost for findings with detail
        if f.get("detail"):
            score += 0.02

    # Cap at 1.0
    return min(1.0, score)


def _identify_gaps(findings: list[dict]) -> list[str]:
    """Identify what information is missing from the findings."""
    gaps = []

    if not findings:
        gaps.append("No findings available — review may be incomplete")
        return gaps

    has_file = any(f.get("file") for f in findings)
    has_line = any(f.get("line") for f in findings)
    has_detail = any(f.get("detail") for f in findings)

    if not has_file:
        gaps.append("No file references in findings — cannot locate issues")
    if not has_line:
        gaps.append("No line numbers in findings — precise location unknown")
    if not has_detail:
        gaps.append("No detailed descriptions — issue context unclear")

    # Check for missing severity
    missing_sev = [f for f in findings if not f.get("severity")]
    if missing_sev:
        gaps.append(f"{len(missing_sev)} finding(s) missing severity level")

    return gaps


def _upload_diagram(file_path: str) -> Optional[str]:
    """Upload .excalidraw file and return shareable link.

    Tries multiple strategies:
    1. scripts/upload_excalidraw.py in the repo
    2. Hermes creative/excalidraw skill
    3. Direct API call
    """
    # Strategy 1: repo-local upload script
    repo_script = Path(__file__).resolve().parent.parent / "scripts" / "upload_excalidraw.py"
    if repo_script.exists():
        try:
            import subprocess
            result = subprocess.run(
                ["python3", str(repo_script), file_path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception as e:
            logger.warning("Repo upload script failed: %s", e)

    # Strategy 2: Hermes skill
    hermes_script = (
        Path.home() / ".hermes" / "hermes-agent" / "skills"
        / "creative" / "excalidraw" / "scripts" / "upload.py"
    )
    if hermes_script.exists():
        try:
            import subprocess
            result = subprocess.run(
                ["python3", str(hermes_script), file_path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception as e:
            logger.warning("Hermes upload script failed: %s", e)

    # Strategy 3: direct API
    try:
        import urllib.request
        data = Path(file_path).read_bytes()
        req = urllib.request.Request(
            "https://api.excalidraw.com/v2/uploads",
            data=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            upload_id = result.get("id", "")
            if upload_id:
                return f"https://excalidraw.com/#json={upload_id}"
    except Exception as e:
        logger.warning("Direct API upload failed: %s", e)

    return None


def generate_diagram(
    findings: list[dict],
    owner: str,
    repo: str,
    pr_number: int,
    pr_title: str = "",
    pr_author: str = "",
    total_loc: int = 0,
    output_path: Optional[str] = None,
) -> Optional[dict]:
    """Generate an annotated Excalidraw diagram from review findings.

    Args:
        findings: list of {severity, title, file, line, detail}
        owner: repo owner
        repo: repo name
        pr_number: PR number
        pr_title: PR title
        pr_author: PR author username
        total_loc: total lines changed
        output_path: optional path for .excalidraw file (uses temp if None)

    Returns:
        dict with: diagram_url, narrative, confidence, gaps, annotations
        None if generation failed.
    """
    if not findings:
        logger.warning("No findings provided for diagram generation")
        return None

    if build_review_diagram is None:
        logger.error("Cannot import DiagramBuilder — riptide.pipeline.diagram_builder not available")
        return None

    # Build inputs for DiagramBuilder
    pr_data = _build_pr_data(owner, repo, pr_number, pr_title, pr_author, total_loc)
    context_bundle = _build_context_bundle(findings)

    # Determine output path
    if output_path is None:
        output_path = str(Path(tempfile.mkdtemp()) / "review.excalidraw")

    # Generate diagram
    try:
        build_review_diagram(
            pr_data=pr_data,
            context_bundle=context_bundle,
            findings=findings,
            output_path=output_path,
        )
    except Exception as e:
        logger.error("DiagramBuilder failed: %s", e)
        return None

    # Upload diagram
    diagram_url = _upload_diagram(output_path)
    if not diagram_url:
        logger.warning("Diagram upload failed — returning local path only")
        diagram_url = f"file://{output_path}"

    # Build narrative
    narrative = _build_narrative(
        findings, owner, repo, pr_number, pr_title, pr_author, total_loc
    )

    # Build annotations
    annotations = _build_annotations(findings)

    # Compute confidence
    confidence = _compute_confidence(findings)

    # Identify gaps
    gaps = _identify_gaps(findings)

    return {
        "diagram_url": diagram_url,
        "narrative": narrative,
        "confidence": confidence,
        "gaps": gaps,
        "annotations": annotations,
    }


def main():
    """CLI entry point for the diagram analyst."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate annotated Excalidraw from review findings"
    )
    parser.add_argument("--findings", required=True, help="Path to findings JSON file")
    parser.add_argument("--owner", required=True, help="Repo owner")
    parser.add_argument("--repo", required=True, help="Repo name")
    parser.add_argument("--pr", type=int, required=True, help="PR number")
    parser.add_argument("--title", default="", help="PR title")
    parser.add_argument("--author", default="", help="PR author")
    parser.add_argument("--loc", type=int, default=0, help="Total lines changed")
    parser.add_argument("--output", help="Output path for diagram insights JSON")

    args = parser.parse_args()

    findings_path = Path(args.findings)
    if not findings_path.exists():
        print(f"ERROR: Findings file not found: {findings_path}", file=__import__("sys").stderr)
        return 1

    findings = json.loads(findings_path.read_text())
    if not isinstance(findings, list):
        print("ERROR: Findings must be a JSON array", file=__import__("sys").stderr)
        return 1

    result = generate_diagram(
        findings=findings,
        owner=args.owner,
        repo=args.repo,
        pr_number=args.pr,
        pr_title=args.title,
        pr_author=args.author,
        total_loc=args.loc,
    )

    if result is None:
        print("ERROR: Diagram generation failed", file=__import__("sys").stderr)
        return 1

    output_json = json.dumps(result, indent=2)

    if args.output:
        Path(args.output).write_text(output_json)
        print(f"Diagram insights written to {args.output}")
    else:
        print(output_json)

    return 0


if __name__ == "__main__":
    exit(main() or 0)