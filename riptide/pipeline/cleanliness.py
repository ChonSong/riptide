#!/usr/bin/env python3
"""cleanliness.py — Evaluates PR cleanliness signals from Probe output.

Checks:
1. Merge conflicts
2. Related PRs (same files)
3. Test coverage (source-only changes)
4. PR description quality
5. Commit hygiene (Conventional Commits)
6. PR staleness
7. CI pre-check (existing failures)

Produces structured findings for the Judge to incorporate into review.
"""

from __future__ import annotations

from typing import Any


class Cleanliness:
    """Evaluates cleanliness signals and produces findings.

    Each finding has:
    - category: str (conflict, related, test_coverage, description, commit, staleness, ci)
    - severity: str (critical, warning, info)
    - message: str (human-readable)
    - suggestion: str (actionable fix)
    """

    def __init__(self, probe_output: dict):
        self.probe = probe_output
        self.cleanliness = probe_output.get("cleanliness", {})

    def evaluate(self) -> dict:
        """Evaluate all cleanliness signals and return findings."""
        findings = []

        # 1. Merge conflicts
        merge_finding = self._check_merge_conflicts()
        if merge_finding:
            findings.append(merge_finding)

        # 2. Related PRs
        related_findings = self._check_related_prs()
        findings.extend(related_findings)

        # 3. Test coverage
        test_finding = self._check_test_coverage()
        if test_finding:
            findings.append(test_finding)

        # 4. PR description
        desc_finding = self._check_description()
        if desc_finding:
            findings.append(desc_finding)

        # 5. Commit hygiene
        commit_finding = self._check_commit_hygiene()
        if commit_finding:
            findings.append(commit_finding)

        # 6. PR staleness
        stale_finding = self._check_staleness()
        if stale_finding:
            findings.append(stale_finding)

        # 7. CI pre-check
        ci_finding = self._check_ci_precheck()
        if ci_finding:
            findings.append(ci_finding)

        return {
            "findings": findings,
            "score": self._calculate_score(findings),
            "summary": self._summarize(findings),
        }

    def _check_merge_conflicts(self) -> dict | None:
        """Check if PR has merge conflicts."""
        mergeable = self.cleanliness.get("mergeable", {})
        status = mergeable.get("status", "unknown")
        mergeable_val = mergeable.get("mergeable", "unknown")

        if status == "CONFLICTING" or mergeable_val == "CONFLICTING":
            return {
                "category": "conflict",
                "severity": "critical",
                "message": "PR has merge conflicts — cannot be merged until resolved",
                "suggestion": "Rebase on the base branch: `git pull --rebase origin main`",
            }
        if status == "UNKNOWN" or mergeable_val == "UNKNOWN":
            return {
                "category": "conflict",
                "severity": "info",
                "message": "Merge status unknown — GitHub is still computing",
                "suggestion": "Wait a moment and re-review",
            }
        return None

    def _check_related_prs(self) -> list[dict]:
        """Check for related open PRs touching the same files."""
        related = self.cleanliness.get("related_prs", [])
        if not related:
            return []

        findings = []
        for pr in related[:3]:  # Cap at 3 to avoid noise
            overlap = ", ".join(pr.get("overlap_files", [])[:5])
            findings.append({
                "category": "related",
                "severity": "info",
                "message": (
                    f"Related PR #{pr.get('number')} by @{pr.get('author')} "
                    f"touches same files: {overlap}"
                ),
                "suggestion": (
                    f"Coordinate with @{pr.get('author')} on PR #{pr.get('number')} "
                    f"to avoid conflicts"
                ),
            })

        if len(related) > 3:
            findings.append({
                "category": "related",
                "severity": "info",
                "message": f"...and {len(related) - 3} more related PRs",
                "suggestion": "Check all open PRs for overlap",
            })

        return findings

    def _check_test_coverage(self) -> dict | None:
        """Check if source files have corresponding test changes."""
        coverage = self.cleanliness.get("test_coverage", {})
        source_files = coverage.get("source_files", [])
        test_files = coverage.get("test_files", [])
        untested = coverage.get("untested_source", [])

        if not source_files:
            return None

        if not test_files and untested:
            return {
                "category": "test_coverage",
                "severity": "warning",
                "message": (
                    f"No test files changed — {len(untested)} source file(s) "
                    f"lack test coverage: {', '.join(untested[:3])}"
                ),
                "suggestion": "Add tests for the changed source files",
            }

        if untested:
            return {
                "category": "test_coverage",
                "severity": "warning",
                "message": (
                    f"{len(untested)} source file(s) lack test changes: "
                    f"{', '.join(untested[:3])}"
                ),
                "suggestion": "Consider adding tests for these files",
            }

        return None

    def _check_description(self) -> dict | None:
        """Check PR description quality."""
        desc = self.cleanliness.get("description_quality", {})
        quality = desc.get("quality", "missing")

        if quality == "missing":
            return {
                "category": "description",
                "severity": "warning",
                "message": "PR has no description — context is missing for reviewers",
                "suggestion": (
                    "Add a description explaining the change, linking issues, "
                    "and noting any breaking changes"
                ),
            }

        if quality == "minimal":
            return {
                "category": "description",
                "severity": "info",
                "message": "PR description is minimal — consider adding more context",
                "suggestion": (
                    "Link related issues, explain the motivation, "
                    "and document any trade-offs"
                ),
            }

        if not desc.get("has_issue_link"):
            return {
                "category": "description",
                "severity": "info",
                "message": "No issue references in description",
                "suggestion": "Link related issues with #<number> or GH-<number>",
            }

        return None

    def _check_commit_hygiene(self) -> dict | None:
        """Check commit messages against Conventional Commits."""
        hygiene = self.cleanliness.get("commit_hygiene", {})
        total = hygiene.get("total", 0)
        conventional = hygiene.get("conventional_count", 0)

        if total == 0:
            return None

        if conventional < total:
            non_conventional = total - conventional
            return {
                "category": "commit",
                "severity": "info",
                "message": (
                    f"{non_conventional}/{total} commits don't follow "
                    f"Conventional Commits (feat:, fix:, etc.)"
                ),
                "suggestion": (
                    "Consider squashing and using Conventional Commits: "
                    "feat:, fix:, docs:, style:, refactor:, perf:, test:, build:, ci:, chore:"
                ),
            }

        return None

    def _check_staleness(self) -> dict | None:
        """Check PR staleness."""
        staleness = self.cleanliness.get("staleness", {})
        age_days = staleness.get("age_days")

        if age_days is None:
            return None

        if age_days > 30:
            return {
                "category": "staleness",
                "severity": "warning",
                "message": f"PR is {age_days} days old — may be stale",
                "suggestion": "Rebase on the latest base branch and resolve any conflicts",
            }

        if age_days > 14:
            return {
                "category": "staleness",
                "severity": "info",
                "message": f"PR is {age_days} days old",
                "suggestion": "Consider rebasing to avoid divergence",
            }

        return None

    def _check_ci_precheck(self) -> dict | None:
        """Check existing CI status before reviewing."""
        ci = self.cleanliness.get("ci_precheck", {})
        status = ci.get("status", "unknown")
        failing = ci.get("failing", [])

        if status == "failing":
            names = ", ".join(c.get("name", "?") for c in failing[:3])
            return {
                "category": "ci",
                "severity": "warning",
                "message": f"CI already failing: {names}",
                "suggestion": "Fix CI failures before requesting review",
            }

        return None

    def _calculate_score(self, findings: list[dict]) -> int:
        """Calculate cleanliness score (0-100, higher = cleaner)."""
        score = 100
        for finding in findings:
            severity = finding.get("severity", "info")
            if severity == "critical":
                score -= 30
            elif severity == "warning":
                score -= 15
            elif severity == "info":
                score -= 5
        return max(0, score)

    def _summarize(self, findings: list[dict]) -> str:
        """Generate a one-line summary of cleanliness."""
        if not findings:
            return "PR is clean"

        critical = sum(1 for f in findings if f.get("severity") == "critical")
        warnings = sum(1 for f in findings if f.get("severity") == "warning")
        info = sum(1 for f in findings if f.get("severity") == "info")

        parts = []
        if critical:
            parts.append(f"{critical} critical")
        if warnings:
            parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
        if info:
            parts.append(f"{info} info")

        return f"Cleanliness: {', '.join(parts)}"
