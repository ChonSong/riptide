#!/usr/bin/env python3
"""Review provenance.

Two things are asserted here:

1. The sign-off carries a deterministic handle for the job that ran the review
   (and the session that rendered it, when there is one), *after* the existing
   model/provider attribution, without disturbing the `Riptide Review ·` prefix
   that the CI gate and the fixer both match literally.
2. The review_memory row records which model reviewed which revision, and stores
   it as readable JSON rather than a JSON string literal.
"""

import argparse
import json
import os
from unittest.mock import patch

from riptide.assemble_review import (
    _build_signoff,
    _record_review_outcome,
    assemble_review_body,
    review_job_name,
)
from riptide.state import StateStore

SIGNOFF_PREFIX = "Riptide Review ·"
JOB = "riptide-review-ChonSong-riptide-215"


class TestSignoffProvenance:
    def test_job_name_is_deterministic(self):
        """The sign-off handle must equal the name the spawner creates."""
        assert review_job_name("ChonSong", "riptide", 215) == JOB

    def test_job_follows_model_and_provider(self):
        signoff = _build_signoff(
            "deepseek-v4-flash", "deepseek", None, None,
            owner="ChonSong", repo="riptide", pr_number=215,
        )
        assert SIGNOFF_PREFIX in signoff
        assert "model: `deepseek-v4-flash`" in signoff
        assert "provider: `deepseek`" in signoff
        assert f"job: `{JOB}`" in signoff
        assert signoff.index("provider:") < signoff.index("job:")

    def test_without_provenance_the_line_is_unchanged(self):
        """No owner/repo/pr and no session → exactly the historical line.

        The `Riptide Review ·` prefix is what the `riptide-review-required` gate
        and the fixer's review-detection both key off, so it must stay verbatim.
        """
        with patch.dict(os.environ, {}, clear=True):
            signoff = _build_signoff("m", "p", None, None)
        assert signoff == "\n<sub>Riptide Review · model: `m` · provider: `p`</sub>"

    def test_session_is_rendered_when_the_process_has_one(self):
        with patch.dict(os.environ, {"HERMES_SESSION_ID": "cron_abc_20260921_120000"}):
            signoff = _build_signoff("m", "p", None, None, "o", "r", 1)
        assert "session: `cron_abc_20260921_120000`" in signoff

    def test_session_is_omitted_when_there_is_none(self):
        """The webhook/service process is not a session — never invent an id."""
        with patch.dict(os.environ, {}, clear=True):
            signoff = _build_signoff("m", "p", None, None, "o", "r", 1)
        assert "session:" not in signoff
        assert "job: `riptide-review-o-r-1`" in signoff

    def test_findings_and_clean_pass_bodies_both_carry_the_job(self):
        findings = [
            {"severity": "warning", "title": "t", "detail": "d", "file": "f.py", "line": 1}
        ]
        with patch.dict(os.environ, {}, clear=True):
            with_findings = assemble_review_body(findings, "ChonSong", "riptide", 215)
            clean = assemble_review_body([], "ChonSong", "riptide", 215)
        for body in (with_findings, clean):
            assert SIGNOFF_PREFIX in body
            assert f"job: `{JOB}`" in body


class TestReviewMemoryMetadata:
    def _args(self, **overrides):
        values = {
            "owner": "ChonSong",
            "repo": "riptide-provenance-test",
            "pr": 215,
            "head_sha": "a" * 40,
            "model": "deepseek-v4-flash",
            "provider": "deepseek",
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def _row(self, repo):
        conn = StateStore()._get_conn()
        return conn.execute(
            "SELECT head_sha, findings_count, critical_count, warning_count,"
            " verdict, metadata FROM review_memory WHERE repo = ?",
            (repo,),
        ).fetchone()

    def test_outcome_records_job_model_provider_and_sha(self):
        repo = "riptide-provenance-test"
        _record_review_outcome(
            self._args(),
            [
                {"severity": "critical"},
                {"severity": "warning"},
                {"severity": "warning"},
            ],
        )
        row = self._row(repo)
        assert row is not None
        head_sha, findings_count, criticals, warnings, verdict, metadata = row
        assert head_sha == "a" * 40
        assert (findings_count, criticals, warnings) == (3, 1, 2)
        assert verdict == "findings"
        # Readable JSON object, not a JSON string literal
        assert json.loads(metadata) == {
            "job": "riptide-review-ChonSong-riptide-provenance-test-215",
            "model": "deepseek-v4-flash",
            "provider": "deepseek",
            "head_sha": "a" * 40,
        }

    def test_clean_pass_is_recorded_as_pass(self):
        repo = "riptide-provenance-clean"
        _record_review_outcome(self._args(repo=repo), [])
        row = self._row(repo)
        assert row is not None
        assert row[4] == "pass"
        assert row[1] == 0

    def test_store_does_not_double_encode_a_dict(self):
        """`state.store_review_outcome` must serialise a dict exactly once."""
        repo = "riptide-double-encode"
        StateStore().store_review_outcome(
            owner="o",
            repo=repo,
            pr_number=1,
            head_sha="s",
            findings_count=0,
            critical_count=0,
            warning_count=0,
            verdict="pass",
            metadata={"a": 1},
        )
        row = self._row(repo)
        assert row is not None
        assert json.loads(row[5]) == {"a": 1}

    def test_recording_failure_never_raises(self):
        """A bookkeeping failure must not fail a review that already posted."""
        with patch("riptide.review_memory.store_review_outcome", side_effect=RuntimeError("db down")):
            _record_review_outcome(self._args(), [])


class TestScribePassesHeadSha:
    def test_head_sha_travels_to_the_assembler(self, tmp_path):
        from riptide.pipeline.scribe import Scribe

        captured = {}

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _Result()

        findings_path = tmp_path / "findings.json"
        findings_path.write_text("[]")

        with patch("riptide.pipeline.scribe.subprocess.run", _fake_run):
            Scribe().post_review_with_assembler(
                "ChonSong",
                "riptide",
                215,
                [],
                findings_path=str(findings_path),
                head_sha="deadbeef",
            )

        cmd = captured["cmd"]
        assert "--head-sha" in cmd
        assert cmd[cmd.index("--head-sha") + 1] == "deadbeef"
