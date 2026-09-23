#!/usr/bin/env python3
"""The finding lifecycle: raised -> addressed -> resolved, visible in the thread.

The rules under test are the honesty ones:

* a status is only claimed from evidence that was actually gathered — a later
  review that raised nothing, or a commit that changed the file the finding names;
* a file touching the finding is `addressed`, never `resolved` — touching a file is
  not fixing a finding;
* the Companion's `## Riptide Pass:` is not a review, so it can never resolve an
  LLM review's findings (the mistake that would have marked #220's four standing
  findings resolved on the strength of a deterministic pre-pass);
* refreshing is idempotent: one block per comment, no writes when nothing changed.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from riptide.finding_status import (
    ADDRESSED,
    OPEN,
    RESOLVED,
    Finding,
    assess,
    build_payload,
    is_review_comment,
    parse_findings,
    parse_status,
    refresh_review_status,
    render_block,
    upsert_status_block,
)

REVIEWED_AT = "2026-09-22T00:20:44Z"
REVIEWED_SHA = "a" * 40
LATER_SHA = "b" * 40
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)

# The real shape of a posted review (trimmed from PR #220's finding review).
REVIEW_BODY = """## Review: 2 warning(s). Fix `riptide/proofshotter.py:632` first — a `localhost:8788` default survives.
1. **A `localhost:8788` fallback survives in the poller** in `riptide/proofshotter.py:632` — The poller still defaults. ~2min.
2. **A third fallback survives in the t3 dispatch path** in `riptide/orchestrator.py:281` — Same class. ~1min.

| | Finding | File |
|---|---|---|
| 🟡 | A `localhost:8788` fallback survives in the poller | `riptide/proofshotter.py:632` |
| 🟡 | A third fallback survives in the t3 dispatch path | `riptide/orchestrator.py:281` |

Next: Fix A `localhost:8788` fallback survives in the poller in `riptide/proofshotter.py:632` (~2min, total ~3min).

<sub>Riptide Review · model: `deepseek-v4-flash` · provider: `deepseek`</sub>
"""

# The Companion's deterministic pass — NOT a review.
PASS_BODY = """## Riptide Pass: ✅ No findings
**Riptide Review Complete — No findings**
Deterministic analysis found no issues with this PR.
**Depth:** standard | **Verdict:** pass
"""

CLEAN_REVIEW_BODY = """## Review: no findings.
Nothing to fix.

<sub>Riptide Review · model: `deepseek-v4-flash` · provider: `deepseek`</sub>
"""

PRE_PASS_BODY = """## ✨ Review Required
@ChonSong: ⚠️ Function `x` has nesting depth 5 (threshold: 4).

| | Finding | File |
|---|---|---|
| 🟡 | Deep nesting | `riptide/companion.py:10` |
"""


class _Result:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


class FakeGH:
    """Answers `gh api` calls from canned JSON and records the PATCH body."""

    def __init__(self, comments=(), commits=(), commit_files=None, fail_patch=False):
        self.comments = list(comments)
        self.commits = list(commits)
        self.commit_files = commit_files or {}
        self.fail_patch = fail_patch
        self.calls: list[list[str]] = []
        self.patched_body: str | None = None

    @staticmethod
    def comment(body, comment_id=1, created_at=REVIEWED_AT):
        return {"id": comment_id, "body": body, "created_at": created_at}

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        path = next((a for a in cmd if a.startswith("repos/")), "")
        if "-X" in cmd and "PATCH" in cmd:
            if self.fail_patch:
                return _Result(returncode=1, stderr="422 Unprocessable Entity")
            payload = json.loads(Path(cmd[cmd.index("--input") + 1]).read_text())
            self.patched_body = payload["body"]
            return _Result(stdout="{}")
        if "/issues/comments/" in path:
            return _Result(stdout="{}")
        if "/issues/" in path and path.endswith("comments?per_page=100"):
            return _Result(stdout=json.dumps(self.comments))
        if "/pulls/" in path and path.endswith("commits?per_page=100"):
            return _Result(stdout=json.dumps(self.commits))
        if "/commits/" in path:
            sha = path.rsplit("/", 1)[-1]
            return _Result(stdout=json.dumps({"files": self.commit_files.get(sha, [])}))
        raise AssertionError(f"unexpected gh call: {cmd}")

    @property
    def patches(self):
        return [c for c in self.calls if "-X" in c and "PATCH" in c]


def _commit(sha, date):
    return {"sha": sha, "commit": {"committer": {"date": date}}}


def _files(*names):
    return [{"filename": n} for n in names]


class TestParsing:
    def test_severity_table_is_the_source_of_findings(self):
        findings = parse_findings(REVIEW_BODY)

        assert [(f.severity, f.file, f.line) for f in findings] == [
            ("warning", "riptide/proofshotter.py", 632),
            ("warning", "riptide/orchestrator.py", 281),
        ]
        assert findings[0].ref == "riptide/proofshotter.py:632"

    def test_escaped_pipe_in_a_title_does_not_split_the_row(self):
        body = "| | Finding | File |\n|---|---|---|\n| 🟡 | a \\| b | `x.py:1` |\n"

        assert parse_findings(body)[0].title == "a | b"

    def test_the_pass_comment_is_not_a_review(self):
        assert is_review_comment(PASS_BODY) is False
        assert parse_findings(PASS_BODY) == []

    def test_the_complexity_pre_pass_is_not_a_review(self):
        assert is_review_comment(PRE_PASS_BODY) is False

    def test_a_review_with_findings_is_a_review(self):
        assert is_review_comment(REVIEW_BODY) is True


class TestAssessment:
    def test_a_clean_later_review_resolves(self):
        verdict = assess(
            Finding("warning", "t", "a.py", 1),
            later_review_clean=True,
            touched_files=set(),
        )
        assert verdict.status == RESOLVED

    def test_a_touched_file_is_addressed_not_resolved(self):
        verdict = assess(
            Finding("warning", "t", "a.py", 1),
            later_review_clean=False,
            touched_files={"a.py"},
        )
        assert verdict.status == ADDRESSED
        assert "changed after the review" in verdict.evidence

    def test_an_untouched_file_stays_open(self):
        verdict = assess(
            Finding("warning", "t", "a.py", 1),
            later_review_clean=False,
            touched_files={"other.py"},
        )
        assert verdict.status == OPEN
        assert "untouched" in verdict.evidence


class TestBlock:
    def _payload(self, verdicts):
        return build_payload(
            comment_id=1,
            reviewed_at=REVIEWED_AT,
            assessed_head=LATER_SHA,
            assessed_at=NOW.isoformat(),
            verdicts=verdicts,
        )

    def test_upsert_appends_once_then_replaces(self):
        verdicts = [
            assess(Finding("warning", "first", "a.py", 1),
                   later_review_clean=False, touched_files=set()),
        ]
        body = upsert_status_block(REVIEW_BODY, self._payload(verdicts))
        assert body.count("<!-- riptide-finding-status -->") == 1
        assert "still open" in body

        verdicts2 = [
            assess(Finding("warning", "first", "a.py", 1),
                   later_review_clean=True, touched_files=set()),
        ]
        body2 = upsert_status_block(body, self._payload(verdicts2))

        assert body2.count("<!-- riptide-finding-status -->") == 1
        assert "all findings addressed" in body2
        assert "still open" not in body2
        # The review text is preserved around the block.
        assert body2.startswith("## Review: 2 warning(s).")
        assert "Riptide Review ·" in body2

    def test_the_payload_round_trips(self):
        verdicts = [
            assess(Finding("warning", "first", "a.py", 1),
                   later_review_clean=False, touched_files={"a.py"}),
        ]
        body = upsert_status_block(REVIEW_BODY, self._payload(verdicts))

        payload = parse_status(body)
        assert payload["counts"] == {OPEN: 0, ADDRESSED: 1, RESOLVED: 0}
        assert payload["findings"][0]["status"] == ADDRESSED
        assert parse_status(REVIEW_BODY) is None


class TestRefresh:
    def test_no_findings_review_does_nothing(self):
        gh = FakeGH(comments=[FakeGH.comment(PASS_BODY)])

        result = refresh_review_status("o", "r", 1, runner=gh, now=NOW)

        assert result == {"pr": 1, "updated": False, "reason": "no findings review"}
        assert gh.patches == []

    def test_a_touched_finding_is_marked_addressed(self):
        gh = FakeGH(
            comments=[
                FakeGH.comment(REVIEW_BODY, comment_id=42),
                FakeGH.comment(PASS_BODY, comment_id=43, created_at="2026-09-22T02:23:25Z"),
            ],
            commits=[_commit(REVIEWED_SHA, "2026-09-22T00:00:00Z"),
                     _commit(LATER_SHA, "2026-09-22T12:00:00Z")],
            commit_files={LATER_SHA: _files("riptide/proofshotter.py")},
        )

        result = refresh_review_status("o", "r", 1, runner=gh, now=NOW)

        assert result["updated"] is True
        assert result["counts"] == {OPEN: 1, ADDRESSED: 1, RESOLVED: 0}
        assert len(gh.patches) == 1
        assert "riptide/orchestrator.py" in gh.patched_body  # the open one
        assert "changed after the review" in gh.patched_body

    def test_the_companion_pass_never_resolves_a_findings_review(self):
        """The #220 trap: a deterministic pass posted after a review says nothing
        about that review's findings, so nothing here may be marked resolved."""
        gh = FakeGH(
            comments=[
                FakeGH.comment(REVIEW_BODY, comment_id=42),
                FakeGH.comment(PASS_BODY, comment_id=43, created_at="2026-09-22T02:23:25Z"),
            ],
            commits=[],
        )

        result = refresh_review_status("o", "r", 1, runner=gh, now=NOW)

        assert result["counts"] == {OPEN: 2, ADDRESSED: 0, RESOLVED: 0}
        assert "a later review raised no findings" not in gh.patched_body

    def test_a_later_clean_review_resolves_the_findings(self):
        gh = FakeGH(
            comments=[
                FakeGH.comment(REVIEW_BODY, comment_id=42),
                FakeGH.comment(CLEAN_REVIEW_BODY, comment_id=44,
                               created_at="2026-09-22T06:00:00Z"),
            ],
            commits=[],
        )

        result = refresh_review_status("o", "r", 1, runner=gh, now=NOW)

        assert result["counts"] == {OPEN: 0, ADDRESSED: 0, RESOLVED: 2}
        assert "all findings addressed" in gh.patched_body

    def test_an_unchanged_status_is_not_rewritten(self):
        verdicts = [
            assess(f, later_review_clean=False, touched_files=set())
            for f in parse_findings(REVIEW_BODY)
        ]
        already = upsert_status_block(
            REVIEW_BODY,
            build_payload(
                comment_id=42, reviewed_at=REVIEWED_AT, assessed_head=LATER_SHA,
                assessed_at="2026-09-22T11:00:00+00:00", verdicts=verdicts,
            ),
        )
        gh = FakeGH(
            comments=[FakeGH.comment(already, comment_id=42)],
            commits=[_commit(LATER_SHA, "2026-09-22T12:00:00Z")],
        )

        result = refresh_review_status("o", "r", 1, runner=gh, now=NOW)

        assert result["updated"] is False
        assert result["reason"] == "already current"
        assert gh.patches == []

    def test_a_failed_patch_raises_rather_than_reporting_success(self):
        gh = FakeGH(
            comments=[FakeGH.comment(REVIEW_BODY, comment_id=42)],
            commits=[_commit(LATER_SHA, "2026-09-22T12:00:00Z")],
            fail_patch=True,
        )

        with pytest.raises(RuntimeError, match="status PATCH failed"):
            refresh_review_status("o", "r", 1, runner=gh, now=NOW)


FILELESS_REVIEW_BODY = """## Review: 1 warning(s). Fix something first.
1. **A finding with no file** in `—` — the table has no path for it.

| | Finding | File |
|---|---|---|
| 🟡 | A finding with no file | — |

<sub>Riptide Review · model: `deepseek-v4-flash` · provider: `deepseek`</sub>
"""

ACK_BODY = """🛠 **Riptide Fix triggered for #220!**

A Hermes fix session has been scheduled. Scope: the problem you described.

| | Finding | File |
|---|---|---|
| 🟡 | A `localhost:8788` fallback survives in the poller | `riptide/proofshotter.py:632` |

<sub>Riptide Review · model: `deepseek-v4-flash` · provider: `deepseek`</sub>
"""


class TestReviewerFindings:
    """Fixes for the four findings the review of this PR raised.

    Each of the first three fails against the code as first written.
    """

    def test_a_review_with_a_fileless_finding_is_not_a_clean_review(self):
        """The gate counts `| 🟡 | title | — |` as a finding; the parser cannot.

        If "no parsed findings" is read as "raised no findings", a review that is
        blocking the merge simultaneously resolves every earlier finding — the one
        status this module promises only ever comes from a review that raised
        nothing.
        """
        gh = FakeGH(
            comments=[
                FakeGH.comment(REVIEW_BODY, comment_id=42),
                FakeGH.comment(FILELESS_REVIEW_BODY, comment_id=45,
                               created_at="2026-09-22T06:00:00Z"),
            ],
            commits=[],
        )

        result = refresh_review_status("o", "r", 1, runner=gh, now=NOW)

        assert result["counts"] == {OPEN: 2, ADDRESSED: 0, RESOLVED: 0}
        assert "a later review raised no findings" not in gh.patched_body

    def test_a_fileless_finding_is_still_a_finding_for_selection(self):
        assert is_review_comment(FILELESS_REVIEW_BODY) is True
        assert parse_findings(FILELESS_REVIEW_BODY) == []

    def test_the_fixers_ack_is_not_a_review(self):
        """The ack quotes the review it answers, sign-off and table included.

        Left in, it is the newest findings-bearing comment and the status block
        lands on the ack while the review it quotes stays frozen.
        """
        assert is_review_comment(ACK_BODY) is False

        gh = FakeGH(
            comments=[
                FakeGH.comment(REVIEW_BODY, comment_id=42),
                FakeGH.comment(ACK_BODY, comment_id=99,
                               created_at="2026-09-22T00:20:50Z"),
            ],
            commits=[],
        )

        refresh_review_status("o", "r", 1, runner=gh, now=NOW)

        assert len(gh.patches) == 1
        assert any("/issues/comments/42" in arg for arg in gh.patches[0])

    def test_the_newest_commits_after_the_review_are_the_ones_scanned(self):
        """Commits arrive oldest-first, so the cap must keep the tail.

        A PR with a long tail since its review otherwise reads every finding as
        untouched on the strength of the ten oldest commits.
        """
        commits = [
            _commit(f"{i:040x}", f"2026-09-22T01:{i:02d}:00Z") for i in range(12)
        ]
        gh = FakeGH(
            comments=[FakeGH.comment(REVIEW_BODY, comment_id=42)],
            commits=commits,
            commit_files={commits[-1]["sha"]: _files("riptide/proofshotter.py")},
        )

        result = refresh_review_status("o", "r", 1, runner=gh, now=NOW)

        assert result["counts"] == {OPEN: 1, ADDRESSED: 1, RESOLVED: 0}
        assert "changed after the review" in gh.patched_body
