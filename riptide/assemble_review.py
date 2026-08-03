#!/usr/bin/env python3
"""
assemble_review.py — Post-process LLM findings into a validated review comment.

Called from within the Hermes cron session (the LLM writes JSON, then runs
this script to assemble and post the review). Synchronous from the LLM's
perspective — no async gap.

Usage:
    python -m riptide.assemble_review \
        --findings /tmp/findings.json \
        --owner ChonSong \
        --repo riptide \
        --pr 42 \
        --diagram-url "https://excalidraw.com/#json=..."
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ── Assembly ────────────────────────────────────────────────────────────────

def assemble_review_body(
    findings: list[dict],
    owner: str,
    repo: str,
    pr_number: int,
    diagram_url: Optional[str] = None,
) -> str:
    """
    Assemble a review comment from structured findings.

    Args:
        findings: [{severity, title, detail, file, line}]
        owner: Repo owner
        repo: Repo name
        pr_number: PR number
        diagram_url: Optional pre-generated diagram URL

    Returns:
        Markdown review body ready for `gh pr comment`
    """
    parts = []

    # Summary (first finding becomes the summary line)
    if findings:
        critical_or_warnings = [f for f in findings if f.get("severity") in ("critical", "warning")]
        if critical_or_warnings:
            parts.append(f"## 🎯 Summary\n\n{len(critical_or_warnings)} issue(s) found — see details below.")
        else:
            parts.append("## 🎯 Summary\n\nClean PR — no critical issues or warnings.")
    else:
        parts.append("## 🎯 Summary\n\nClean PR — no issues found.")

    # Findings table
    parts.append("\n## 🔍 Findings\n")
    if findings:
        parts.append("| Severity | File | Line | Issue |")
        parts.append("|----------|------|------|-------|")
        for f in findings:
            severity = f.get("severity", "info")
            severity_icon = {"critical": "🔴", "warning": "🟡", "suggestion": "🟣", "info": "🔵"}.get(severity, "⚪")
            file_ref = f"`{f.get('file', '')}`" if f.get("file") else "—"
            line = f.get("line", "")
            title = f.get("title", "")
            parts.append(f"| {severity_icon} {severity} | {file_ref} | {line or '—'} | {title} |")
        # Detail sections
        parts.append("")
        for i, f in enumerate(findings, 1):
            detail = f.get("detail", "")
            if detail:
                parts.append(f"**{i}. {f.get('title', '')}**\n{detail}\n")
    else:
        parts.append("No critical/warning findings.")

    # Diagram
    parts.append("\n## 🔗 Diagram\n")
    if diagram_url:
        parts.append(f"[Visual Review Diagram]({diagram_url})")
    else:
        parts.append("(No diagram generated)")

    # Next steps
    parts.append("\n## 📌 Next Steps\n")
    if findings:
        for f in findings[:3]:
            title = f.get("title", "")
            if title:
                parts.append(f"- Address: {title}")
    else:
        parts.append("- Ready to merge ✓")

    # Sign-off
    parts.append("\n---\n<sub>Riptide Review via Hermes</sub>")

    body = "\n".join(parts)

    # Enforce GitHub comment length limit (65536 chars)
    if len(body) > 65500:
        body = body[:65400] + "\n\n... (truncated)"

    return body


def post_review(owner: str, repo: str, pr_number: int, body: str) -> bool:
    """
    Post review comment to PR via `gh pr comment`.

    Args:
        owner: Repo owner
        repo: Repo name
        pr_number: PR number
        body: Review body (markdown)

    Returns:
        True if posted successfully
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "comment", str(pr_number), "--repo", f"{owner}/{repo}", "--body", body],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def validate_findings(findings: list[dict]) -> list[str]:
    """
    Validate findings structure.

    Returns list of validation errors (empty = valid).
    """
    errors = []
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            errors.append(f"Finding {i}: must be a dict")
            continue
        if "severity" not in f:
            errors.append(f"Finding {i}: missing 'severity'")
        if "title" not in f:
            errors.append(f"Finding {i}: missing 'title'")
        valid_severities = ("critical", "warning", "suggestion", "info", "approved")
        if f.get("severity") not in valid_severities:
            errors.append(f"Finding {i}: invalid severity '{f.get('severity')}' (must be one of {valid_severities})")
    return errors


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Assemble and post a Riptide review from structured findings")
    parser.add_argument("--findings", required=True, help="Path to findings JSON file")
    parser.add_argument("--owner", required=True, help="Repo owner")
    parser.add_argument("--repo", required=True, help="Repo name")
    parser.add_argument("--pr", required=True, type=int, help="PR number")
    parser.add_argument("--diagram-url", default=None, help="Pre-generated diagram URL")
    parser.add_argument("--dry-run", action="store_true", help="Print review instead of posting")
    args = parser.parse_args()

    # Load findings
    findings_path = Path(args.findings)
    if not findings_path.exists():
        print(f"ERROR: findings file not found: {args.findings}", file=sys.stderr)
        sys.exit(1)

    try:
        findings = json.loads(findings_path.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in findings file: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate
    errors = validate_findings(findings)
    if errors:
        print(f"ERROR: findings validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    # Assemble
    body = assemble_review_body(
        findings=findings,
        owner=args.owner,
        repo=args.repo,
        pr_number=args.pr,
        diagram_url=args.diagram_url,
    )

    if args.dry_run:
        print(body)
        return

    # Post
    success = post_review(args.owner, args.repo, args.pr, body)
    if success:
        print(f"✓ Review posted to {args.owner}/{args.repo}#{args.pr}")
    else:
        print(f"ERROR: failed to post review to {args.owner}/{args.repo}#{args.pr}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
