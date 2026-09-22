#!/usr/bin/env python3
"""finding_status.py — a review's findings keep their lifecycle in the thread.

A posted review is frozen. When a later commit fixes a finding, or a later review
stops raising it, the original comment keeps displaying a live 🔴/🟡, so every
reader has to work out for themselves which findings still stand. Two reviews in
this repo still show a warning for a defect that was fixed and verified.

This module renders the missing half of the lifecycle into the review comment:

    raised    — the review posts its 🔴/🟡 table
    addressed — a later commit changed the file the finding names
    resolved  — a later *review* re-examined the code and raised no findings

Statuses are evidence, not verdicts, and are worded that way: "`file` changed
after the review" is checkable, "fixed" is not. A file touched by an unrelated
commit therefore reads as addressed-but-unconfirmed, never as resolved.

Deliberately, the only way to reach `resolved` is a later review that raises no
findings. The Companion's `## Riptide Pass:` is NOT such a review — it is the
deterministic pre-pass and says nothing about an LLM review's findings — so it is
excluded here, as it is excluded from the CI gate.

The payload is also machine-readable (`STATUS_MARKER` + a JSON block), so the
`riptide-review-required` gate can ask "are any findings still open?" instead of
accepting any commit at all as an answer.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("riptide.finding_status")

STATUS_MARKER = "<!-- riptide-finding-status -->"
STATUS_END = "<!-- /riptide-finding-status -->"

RESOLVED = "resolved"
ADDRESSED = "addressed"
OPEN = "open"

STATUS_BADGE = {RESOLVED: "✅", ADDRESSED: "🔄", OPEN: "⬜"}

_HEADING = "### 📋 Finding status"

# The severity table is the machine-readable half of a review; the numbered prose
# above it is human-facing and is reworded between runs, so it is not parsed.
_TABLE_ROW = re.compile(r"^\|\s*(🔴|🟡)\s*\|\s*(.+?)\s*\|\s*(`[^`]*`|—)\s*\|\s*$")

SEVERITY = {"🔴": "critical", "🟡": "warning"}

# Review markers, mirroring deepthink.RIPTIDE_REVIEW_MARKERS. A comment carrying
# one of these is a review; `## Riptide Pass:` and `## ✨ Review Required` are not.
_REVIEW_MARKERS = ("## 🔍 Findings", "## 🎯 Summary", "Riptide Review ·")
_NOT_A_REVIEW = ("Riptide Pass:", "Review Required")

# Comment/commit lists are fetched one page; a PR with more is out of scope here
# and the refresh degrades to "no later commits", which never resolves a finding.
_PAGE = "per_page=100"
_MAX_COMMITS_SCANNED = 10


@dataclass(frozen=True)
class Finding:
    """One row of a review's severity table."""

    severity: str
    title: str
    file: str
    line: Optional[int] = None

    @property
    def ref(self) -> str:
        return f"{self.file}:{self.line}" if self.line else self.file

    def to_dict(self) -> dict:
        return {"severity": self.severity, "title": self.title, "ref": self.ref}


@dataclass(frozen=True)
class Verdict:
    finding: Finding
    status: str
    evidence: str


def _split_ref(cell: str) -> tuple[str, Optional[int]]:
    """``path/to/file.py:12`` -> ("path/to/file.py", 12); "—" -> ("", None)."""
    text = cell.strip().strip("`").strip()
    if not text or text == "—":
        return "", None
    match = re.match(r"^(.*?):(\d+)$", text)
    if match:
        return match.group(1), int(match.group(2))
    return text, None


def parse_findings(body: str) -> list[Finding]:
    """The findings a posted review raises, read from its severity table."""
    findings: list[Finding] = []
    for line in body.splitlines():
        match = _TABLE_ROW.match(line.strip())
        if not match:
            continue
        file, number = _split_ref(match.group(3))
        if not file:
            continue
        findings.append(
            Finding(
                severity=SEVERITY[match.group(1)],
                title=match.group(2).replace("\\|", "|"),
                file=file,
                line=number,
            )
        )
    return findings


def is_review_comment(body: str) -> bool:
    """Whether a comment is a findings-capable review (not a pass/pre-pass)."""
    first_line = body.splitlines()[0] if body.splitlines() else ""
    if any(skip in first_line for skip in _NOT_A_REVIEW):
        return False
    return any(marker in body for marker in _REVIEW_MARKERS)


def assess(
    finding: Finding,
    *,
    later_review_clean: bool,
    touched_files: set[str],
) -> Verdict:
    """What is known about one finding after the review that raised it.

    The order matters: a later review that raised nothing is the strongest
    evidence available (the reviewer re-read the code and had nothing to say),
    then a later commit that changed the file, then nothing at all.
    """
    if later_review_clean:
        return Verdict(finding, RESOLVED, "a later review raised no findings")
    if finding.file in touched_files:
        return Verdict(finding, ADDRESSED, f"`{finding.file}` changed after the review")
    return Verdict(finding, OPEN, f"`{finding.file}` untouched since the review")


def build_payload(
    *,
    comment_id: int,
    reviewed_at: str,
    assessed_head: str,
    assessed_at: str,
    verdicts: list[Verdict],
) -> dict:
    """The status payload: human-readable rows plus the counts the gate reads."""
    counts = {OPEN: 0, ADDRESSED: 0, RESOLVED: 0}
    for verdict in verdicts:
        counts[verdict.status] += 1
    return {
        "review_comment_id": comment_id,
        "reviewed_at": reviewed_at,
        "assessed_head": assessed_head,
        "assessed_at": assessed_at,
        "counts": counts,
        "findings": [
            {**verdict.finding.to_dict(), "status": verdict.status, "evidence": verdict.evidence}
            for verdict in verdicts
        ],
    }


def render_block(payload: dict) -> str:
    """The status block, appended to the review body (idempotently)."""
    counts = payload["counts"]
    open_count = counts[OPEN]
    headline = (
        f"{_HEADING} — all findings addressed"
        if open_count == 0
        else f"{_HEADING} — {open_count} still open"
    )
    lines = [headline, "", "| | Finding | File | Status |", "|---|---|---|---|"]

    for item in payload["findings"]:
        status = item["status"]
        emoji = STATUS_BADGE.get(status, "")
        title = item["title"].replace("|", "\\|")
        lines.append(
            f"| {emoji} | {title} | `{item['ref']}` | {status} — {item['evidence']} |"
        )

    lines += [
        "",
        f"_Assessed at `{payload['assessed_head'][:8]}` on {payload['assessed_at']}. "
        "Statuses record what changed since the review, not that a fix was verified._",
        "",
        STATUS_MARKER,
        "```json",
        json.dumps(payload, indent=2, sort_keys=True),
        "```",
        STATUS_END,
    ]
    return "\n".join(lines)


def parse_status(body: str) -> Optional[dict]:
    """The status payload already carried by a review body, or None."""
    start = body.find(STATUS_MARKER)
    if start == -1:
        return None
    end = body.find(STATUS_END, start)
    chunk = body[start + len(STATUS_MARKER): end if end != -1 else len(body)]
    match = re.search(r"```json\s*(\{.*?\})\s*```", chunk, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def upsert_status_block(body: str, payload: dict) -> str:
    """Replace the status block in a body, or append one. Never stacks blocks."""
    block = render_block(payload)
    start = body.find(STATUS_MARKER)
    if start == -1:
        return body.rstrip("\n") + "\n\n" + block + "\n"
    # The block starts at its own heading, one blank line above the marker.
    head = body.rfind(_HEADING, 0, start)
    start = head if head != -1 else start
    end = body.find(STATUS_END, start)
    end = len(body) if end == -1 else end + len(STATUS_END)
    return body[:start].rstrip("\n") + "\n\n" + block + "\n" + body[end:].lstrip("\n")


def _same_status(existing: Optional[dict], payload: dict) -> bool:
    """Whether a refresh would rewrite the same block (ignoring its timestamp)."""
    if not existing:
        return False
    return (
        existing.get("assessed_head") == payload["assessed_head"]
        and existing.get("counts") == payload["counts"]
        and [f.get("status") for f in existing.get("findings", [])]
        == [f["status"] for f in payload["findings"]]
    )


def _gh_json(args: list[str], runner: Callable) -> object:
    result = runner(["gh", *args], capture_output=True, text=True, timeout=60)
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {result.stderr[:200]}")
    return json.loads(result.stdout or "null")


def refresh_review_status(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    runner: Callable = subprocess.run,
    now: Optional[datetime] = None,
) -> dict:
    """Bring the newest findings review's status block up to date.

    Reads the PR's comments, then for the newest review that raised findings works
    out what changed since (a later review with no findings, and the files later
    commits touched) and rewrites that review's status block in place.

    Cheap when there is nothing to do: one comment fetch, no writes.
    """
    comments = _gh_json(
        ["api", f"repos/{owner}/{repo}/issues/{pr_number}/comments?{_PAGE}"], runner
    ) or []

    review_index = None
    for index, comment in enumerate(comments):
        if is_review_comment(comment.get("body", "")) and parse_findings(comment.get("body", "")):
            review_index = index
    if review_index is None:
        return {"pr": pr_number, "updated": False, "reason": "no findings review"}

    review = comments[review_index]
    findings = parse_findings(review["body"])
    reviewed_at = review.get("created_at", "")

    later_review_clean = any(
        is_review_comment(body) and not parse_findings(body)
        for body in (c.get("body", "") for c in comments[review_index + 1:])
    )

    commits = _gh_json(
        ["api", f"repos/{owner}/{repo}/pulls/{pr_number}/commits?{_PAGE}"], runner
    ) or []
    after = [
        c for c in commits
        if (c.get("commit", {}).get("committer", {}).get("date") or "") > reviewed_at
    ][:_MAX_COMMITS_SCANNED]

    touched: set[str] = set()
    for commit in after:
        sha = commit.get("sha", "")
        if not sha:
            continue
        files = _gh_json(["api", f"repos/{owner}/{repo}/commits/{sha}"], runner) or {}
        touched.update(f.get("filename", "") for f in files.get("files", []))

    head_sha = commits[-1].get("sha", "") if commits else ""
    verdicts = [
        assess(finding, later_review_clean=later_review_clean, touched_files=touched)
        for finding in findings
    ]
    payload = build_payload(
        comment_id=review["id"],
        reviewed_at=reviewed_at,
        assessed_head=head_sha,
        assessed_at=(now or datetime.now(timezone.utc)).isoformat(),
        verdicts=verdicts,
    )

    if _same_status(parse_status(review["body"]), payload):
        return {"pr": pr_number, "updated": False, "reason": "already current",
                "counts": payload["counts"]}

    new_body = upsert_status_block(review["body"], payload)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({"body": new_body}, handle)
        payload_path = handle.name
    try:
        result = runner(
            ["gh", "api", "-X", "PATCH",
             f"repos/{owner}/{repo}/issues/comments/{review['id']}",
             "--input", payload_path],
            capture_output=True, text=True, timeout=60,
        )
    finally:
        Path(payload_path).unlink(missing_ok=True)

    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError(f"status PATCH failed: {result.stderr[:200]}")

    log.info(
        "  #%d finding status: %d open, %d addressed, %d resolved",
        pr_number, payload["counts"][OPEN], payload["counts"][ADDRESSED],
        payload["counts"][RESOLVED],
    )
    return {"pr": pr_number, "updated": True, "counts": payload["counts"]}
