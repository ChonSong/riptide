#!/usr/bin/env python3
"""
assemble_review.py — Post-process LLM findings into an ADHD-friendly review comment.

Called from within the Hermes cron session (the LLM writes JSON, then runs
this script to assemble and post the review). Synchronous from the LLM's
perspective — no async gap.

Usage:
    python -m riptide.assemble_review \
        --findings /tmp/findings.json \
        --owner ChonSong \
        --repo riptide \
        --pr 42 \
        --diagram-url "https://excalidraw.com/#json=..." \
        --model "custom:LongCat-2.0" \
        --provider "custom"
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Constants ───────────────────────────────────────────────────────────────

# Maximum visible findings before collapsing into <details>
MAX_VISIBLE_FINDINGS = 5

# Default time estimates by severity (when no override provided)
SEVERITY_TIME_ESTIMATES = {
    "critical": "~5min",
    "warning": "~2min",
    "suggestion": "~1min",
    "info": "~1min",
}

# Valid severities for validation
VALID_SEVERITIES = ("critical", "warning", "suggestion", "info", "approved")


# ── Assembly ────────────────────────────────────────────────────────────────

def assemble_review_body(
    findings: list[dict],
    owner: str,
    repo: str,
    pr_number: int,
    diagram_url: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    pr_created_at: Optional[str] = None,
    triggered_at: Optional[str] = None,
    time_estimates: Optional[dict[str, str]] = None,
) -> str:
    """
    Assemble an ADHD-friendly review comment from structured findings.

    Rules:
        1. Lead with verdict (count + first critical/warning).
        2. Number findings (1. 2. 3.).
        3. Cap at 5 visible; remainder in <details>.
        4. Add time estimates after each finding.
        5. End with single next action + total time.
        6. No preamble/headers — start with answer.
        7. Success message for clean PRs.
        8. Matter-of-fact errors.

    Args:
        findings: [{severity, title, detail, file, line, actions}]
            actions is an optional list of strings for multi-step fixes.
        owner: Repo owner
        repo: Repo name
        pr_number: PR number
        diagram_url: Optional pre-generated diagram URL
        model: Model used for review (appended to sign-off)
        provider: Provider used for review (appended to sign-off)
        pr_created_at: ISO timestamp when PR was created (for timing fallback)
        triggered_at: ISO timestamp when review was triggered (for timing metric)
        time_estimates: Optional override mapping finding title → time string.

    Returns:
        Markdown review body ready for `gh pr comment`
    """
    if time_estimates is None:
        time_estimates = {}

    # Clean PR: success message
    if not findings:
        return _build_success_footer(model, provider, triggered_at, pr_created_at)

    # Separate critical/warnings from suggestions/info
    criticals = [f for f in findings if f.get("severity") == "critical"]
    warnings = [f for f in findings if f.get("severity") == "warning"]

    # Build parts
    parts = []

    # 1. Verdict line (first)
    parts.append(_build_verdict(criticals, warnings))

    # 2. Numbered findings (cap at 5 visible)
    numbered = _build_numbered_findings(findings, time_estimates)
    parts.extend(numbered)

    # 3. Diagram link (if any)
    if diagram_url:
        parts.append(f"\n[Diagram]({diagram_url})")

    # 4. Next action footer
    next_action = _build_next_action(findings, time_estimates)
    parts.append(f"\n{next_action}")

    # 5. Sign-off with timing
    signoff = _build_signoff(model, provider, triggered_at, pr_created_at)
    parts.append(signoff)

    body = "\n".join(parts)

    # Enforce GitHub comment length limit (65536 chars)
    if len(body) > 65500:
        body = body[:65400] + "\n\n... (truncated)"

    return body


def _build_success_footer(
    model: Optional[str],
    provider: Optional[str],
    triggered_at: Optional[str],
    pr_created_at: Optional[str],
) -> str:
    """Build a clean-PR success message."""
    lines = ["✅ No critical or warning findings. Ready to merge.", "", "Next: Merge when ready."]

    elapsed_str = _compute_elapsed(triggered_at, pr_created_at)
    if elapsed_str:
        lines.append(f"\n<sub>Riptide Review · ⏱️ Review posted in {elapsed_str}</sub>")
    else:
        signoff = _build_signoff(model, provider, triggered_at, pr_created_at)
        lines.append(signoff)

    return "\n".join(lines)


def _build_verdict(criticals: list[dict], warnings: list[dict]) -> str:
    """Build the leading verdict line."""
    c_count = len(criticals)
    w_count = len(warnings)

    # Compose count description
    parts = []
    if c_count:
        parts.append(f"{c_count} critical")
    if w_count:
        parts.append(f"{w_count} warning(s)")

    if not parts:
        # Only suggestions/info
        return "No critical issues or warnings."

    verdict = ", ".join(parts) + "."

    # Add "Fix X first" directive if there are criticals or warnings
    first = (criticals or warnings)[0]
    file_ref = _format_file_ref(first)
    reason = first.get("title", "issue")
    if file_ref:
        verdict += f" Fix `{file_ref}` first — {reason.lower()}."
    else:
        verdict += f" Fix: {reason}."

    return verdict


def _build_numbered_findings(
    findings: list[dict],
    time_estimates: dict[str, str],
) -> list[str]:
    """Build numbered finding lines, capping visible at MAX_VISIBLE_FINDINGS."""
    visible = findings[:MAX_VISIBLE_FINDINGS]
    remainder = findings[MAX_VISIBLE_FINDINGS:]

    lines = []

    for i, f in enumerate(visible, 1):
        lines.append(_format_finding(i, f, time_estimates))

    if remainder:
        lines.append("")
        lines.append(f"<details><summary>Additional findings ({len(remainder)})</summary>")
        lines.append("")
        for i, f in enumerate(remainder, MAX_VISIBLE_FINDINGS + 1):
            lines.append(_format_finding(i, f, time_estimates))
        lines.append("")
        lines.append("</details>")

    return lines


def _format_finding(index: int, finding: dict, time_estimates: dict[str, str]) -> str:
    """Format a single finding as a numbered line."""
    title = finding.get("title", "Unknown issue")
    file_ref = _format_file_ref(finding)
    detail = finding.get("detail", "")
    severity = finding.get("severity", "info")
    actions = finding.get("actions", [])

    # Time estimate
    time_est = _get_time_estimate(title, severity, time_estimates)

    # Build the line
    if file_ref:
        line = f"{index}. **{title}** in `{file_ref}`"
    else:
        line = f"{index}. **{title}**"

    if detail:
        line += f" — {detail}"

    if time_est:
        line += f". {time_est}."

    # Multi-step actions (numbered sub-list)
    if actions:
        for j, action in enumerate(actions, 1):
            line += f"\n   {j}. {action}"

    return line


def _get_time_estimate(title: str, severity: str, overrides: dict[str, str]) -> str:
    """Get time estimate for a finding."""
    if title in overrides:
        return overrides[title]
    return SEVERITY_TIME_ESTIMATES.get(severity, "~2min")


def _compute_total_time(findings: list[dict], time_estimates: dict[str, str]) -> str:
    """Compute total estimated time for all critical/warning findings."""
    total_minutes = 0
    for f in findings:
        if f.get("severity") not in ("critical", "warning", "suggestion", "info"):
            continue
        title = f.get("title", "")
        est = time_estimates.get(title, SEVERITY_TIME_ESTIMATES.get(f.get("severity"), "~2min"))
        # Parse "~Xmin" format
        try:
            num = int(est.replace("~", "").replace("min", "").strip())
            total_minutes += num
        except (ValueError, AttributeError):
            total_minutes += 2  # default

    if total_minutes < 60:
        return f"~{total_minutes}min"
    hours = total_minutes / 60
    if hours == int(hours):
        return f"~{int(hours)}h"
    return f"~{hours:.1f}h"


def _build_next_action(findings: list[dict], time_estimates: dict[str, str]) -> str:
    """Build the single next action footer."""
    if not findings:
        return "Next: Merge when ready."

    first = findings[0]
    file_ref = _format_file_ref(first)
    title = first.get("title", "issue")
    severity = first.get("severity", "warning")
    time_est = _get_time_estimate(title, severity, time_estimates)
    total_time = _compute_total_time(findings, time_estimates)

    if file_ref:
        return f"Next: Fix {title} in `{file_ref}` ({time_est}, total {total_time})."
    return f"Next: Fix {title} ({time_est}, total {total_time})."


def _format_file_ref(finding: dict) -> str:
    """Format file:line reference string."""
    file_path = finding.get("file", "")
    line = finding.get("line")
    if file_path and line:
        return f"{file_path}:{line}"
    if file_path:
        return file_path
    return ""


def _build_signoff(
    model: Optional[str],
    provider: Optional[str],
    triggered_at: Optional[str],
    pr_created_at: Optional[str],
) -> str:
    """Build the sign-off line with timing."""
    elapsed_str = _compute_elapsed(triggered_at, pr_created_at)

    signoff_parts = []
    if model:
        signoff_parts.append(f"model: `{model}`")
    if provider:
        signoff_parts.append(f"provider: `{provider}`")
    if elapsed_str:
        signoff_parts.append(f"⏱️ Review posted in {elapsed_str}")

    if signoff_parts:
        return f"\n<sub>Riptide Review · {' · '.join(signoff_parts)}</sub>"
    return "\n<sub>Riptide Review</sub>"


def _compute_elapsed(
    triggered_at: Optional[str],
    pr_created_at: Optional[str],
) -> Optional[str]:
    """Compute elapsed time string from triggered_at or pr_created_at."""
    if triggered_at:
        try:
            triggered = datetime.fromisoformat(triggered_at.replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - triggered).total_seconds()
            return _format_elapsed(elapsed)
        except (ValueError, TypeError):
            return None
    elif pr_created_at:
        try:
            created = datetime.fromisoformat(pr_created_at.replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - created).total_seconds()
            return f"{_format_elapsed(elapsed)} (since PR opened)"
        except (ValueError, TypeError):
            return None
    return None


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds to human-readable string."""
    if seconds < 1:
        return f"{int(seconds * 1000)}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


# ── Posting ────────────────────────────────────────────────────────────────

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
        if f.get("severity") not in VALID_SEVERITIES:
            errors.append(f"Finding {i}: invalid severity '{f.get('severity')}' (must be one of {VALID_SEVERITIES})")
    return errors


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Assemble and post a Riptide review from structured findings")
    parser.add_argument("--findings", required=True, help="Path to findings JSON file")
    parser.add_argument("--owner", required=True, help="Repo owner")
    parser.add_argument("--repo", required=True, help="Repo name")
    parser.add_argument("--pr", required=True, type=int, help="PR number")
    parser.add_argument("--diagram-url", default=None, help="Pre-generated diagram URL")
    parser.add_argument("--model", default=None, help="Model used for the review (appended to sign-off)")
    parser.add_argument("--provider", default=None, help="Provider used for the review (appended to sign-off)")
    parser.add_argument("--pr-created-at", default=None, help="PR created_at ISO timestamp (for timing metric)")
    parser.add_argument("--triggered-at", default=None, help="ISO timestamp when review was triggered (for timing metric)")
    parser.add_argument("--dry-run", action="store_true", help="Print review instead of posting")
    args = parser.parse_args()

    # Load findings
    findings_path = Path(args.findings)
    if not findings_path.exists():
        print(f"ERROR: findings file not found: {args.findings}. Fix: verify --findings path exists.", file=sys.stderr)
        sys.exit(1)

    try:
        findings = json.loads(findings_path.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in findings file: {e}. Fix: validate JSON syntax.", file=sys.stderr)
        sys.exit(1)

    # Validate
    errors = validate_findings(findings)
    if errors:
        print("ERROR: findings validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print("Fix: ensure all findings have 'severity' and 'title' fields.", file=sys.stderr)
        sys.exit(1)

    # Assemble
    body = assemble_review_body(
        findings=findings,
        owner=args.owner,
        repo=args.repo,
        pr_number=args.pr,
        diagram_url=args.diagram_url,
        model=args.model,
        provider=args.provider,
        pr_created_at=args.pr_created_at,
        triggered_at=args.triggered_at,
    )

    if args.dry_run:
        print(body)
        return

    # Post
    success = post_review(args.owner, args.repo, args.pr, body)
    if success:
        print(f"Review posted to {args.owner}/{args.repo}#{args.pr}")
    else:
        print(f"ERROR: failed to post review to {args.owner}/{args.repo}#{args.pr}. Fix: check gh auth and PR number.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()