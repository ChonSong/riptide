"""Tests for the review-pipeline wiring fixes.

Covers four defects that let Riptide silently do nothing or post misleading
reviews:

1. Conductor workstreams disagreed on output paths (probe wrote
   /tmp/output.json while the judge read /tmp/pr-N-context.json), and the
   scribe never received the judge's findings — so a bare pipeline run either
   crashed or posted a false-clean "Ready to merge" review.
2. The scribe posted a review even when it had no findings to post.
3. A finished (or crashed) review left a `pending` reservation behind, which
   blocked every later `@riptide-bot review` for two hours.
4. The poller treated a remembered SHA as proof of a review, so a PR whose
   review never landed was skipped forever.
"""

import json
import time
from pathlib import Path

import pytest

import riptide.deepthink as deepthink
from riptide.assemble_review import assemble_review_body
from riptide.pipeline import conductor
from riptide.pipeline.conductor import Conductor, _canonical_output_path
from riptide.pipeline.roles import WorkerBrief
from riptide.state import StateStore


# ── helpers ──────────────────────────────────────────────────────────────────


class _AllPassWarden:
    """Stub Warden: the file-exists check always passes."""

    def verify_all(self, checks):
        return {"pass": True, "results": []}


def _conductor(monkeypatch, track: dict, dispatched: list) -> Conductor:
    """Build a Conductor whose track and dispatch are stubbed."""
    c = Conductor.__new__(Conductor)
    c.track_id = "track-under-test"
    c.track = track
    monkeypatch.setattr(c, "_get_track", lambda: track)
    monkeypatch.setattr(
        c, "_dispatch",
        lambda role, brief: dispatched.append(brief) or {"ok": True},
    )
    monkeypatch.setattr(conductor, "update_workstream", lambda *a, **k: None)
    monkeypatch.setattr(conductor, "Warden", _AllPassWarden)
    return c


def _brief(role: str, inputs: dict) -> WorkerBrief:
    return WorkerBrief(
        role=role,
        name=f"{role}-ws",
        track="track-under-test",
        workstream=f"ws-{role}",
        pipeline="",
        position="",
        key_facts={},
        inputs=inputs,
        acceptance={},
        recovery={},
        output_protocol={"path": "/tmp/unused.json"},
    )


# ── 1. Conductor wiring ──────────────────────────────────────────────────────


class TestCanonicalOutputPaths:
    def test_each_role_gets_its_own_path(self):
        paths = {
            role: _canonical_output_path(190, role)
            for role in ("probe", "judge", "artisan", "engine", "warden", "scribe")
        }
        assert len(set(paths.values())) == len(paths), "roles must not share a path"
        assert paths["probe"].endswith("-context.json")
        assert paths["judge"].endswith("-findings.json")

    def test_paths_are_per_pr(self):
        assert _canonical_output_path(190, "probe") != _canonical_output_path(191, "probe")

    def test_unknown_pr_never_collapses_to_a_shared_path(self):
        """A missing PR number must not make concurrent reviews share a file."""
        a = _canonical_output_path(0, "judge", "track-a")
        b = _canonical_output_path(0, "judge", "track-b")
        assert a != b
        assert "pr-0" not in a


class TestWorkstreamInputPropagation:
    def test_downstream_brief_gets_upstream_outputs(self, monkeypatch):
        """The judge must read the path the probe actually wrote."""
        dispatched: list = []
        track = {
            "key_facts": {},
            "workstreams": {
                "ws-1-probe": {
                    "role": "probe",
                    "inputs": {"pr_number": 190},
                    "outputs": {"context_path": "/tmp/riptide-review-190-context.json"},
                },
                "ws-2-judge": {
                    "role": "judge",
                    # Stale hardcoded path — propagated output must win.
                    "inputs": {"pr_number": 190, "context_path": "/tmp/pr-190-context.json"},
                    "outputs": {},
                },
            },
        }
        c = _conductor(monkeypatch, track, dispatched)
        c._run_workstream("ws-2-judge", track["workstreams"]["ws-2-judge"])

        brief = dispatched[0]
        assert brief.inputs["context_path"] == "/tmp/riptide-review-190-context.json"
        # No shared default output path any more.
        assert brief.output_protocol["path"] != "/tmp/output.json"
        assert brief.output_protocol["path"].endswith("-findings.json")

    def test_scribe_receives_judge_findings_path(self, monkeypatch):
        dispatched: list = []
        track = {
            "key_facts": {},
            "workstreams": {
                "ws-2-judge": {
                    "role": "judge",
                    "inputs": {"pr_number": 190},
                    "outputs": {"findings_path": "/tmp/riptide-review-pr-190-findings.json"},
                },
                "ws-5-scribe": {
                    "role": "scribe",
                    "inputs": {"pr_number": 190, "action": "post_review"},
                    "outputs": {},
                },
            },
        }
        c = _conductor(monkeypatch, track, dispatched)
        c._run_workstream("ws-5-scribe", track["workstreams"]["ws-5-scribe"])
        assert dispatched[0].inputs["findings_path"] == "/tmp/riptide-review-pr-190-findings.json"

    def test_judge_gets_a_per_pr_path_without_pr_number_in_its_inputs(self, monkeypatch):
        """ws-2-judge declares only context_path — the PR must come from the track.

        Regression: resolving the PR number per-workstream collapsed every PR's
        judge output onto /tmp/riptide-review-0-findings.json, so concurrent
        reviews overwrote each other's findings.
        """
        dispatched: list = []
        track = {
            "key_facts": {},
            "workstreams": {
                "ws-1-probe": {
                    "role": "probe",
                    "inputs": {"pr_number": 190},
                    "outputs": {},
                },
                "ws-2-judge": {
                    "role": "judge",
                    "inputs": {"context_path": "/tmp/pr-190-context.json"},
                    "outputs": {},
                },
            },
        }
        c = _conductor(monkeypatch, track, dispatched)
        c._run_workstream("ws-2-judge", track["workstreams"]["ws-2-judge"])

        path = dispatched[0].output_protocol["path"]
        assert "pr-190" in path
        assert path.endswith("-findings.json")
        assert "pr-0-" not in path


class TestJudgeFailsLoudly:
    def test_missing_context_file_does_not_yield_zero_findings(self):
        c = Conductor.__new__(Conductor)
        c.track_id = "t"
        out = c._run_judge(_brief("judge", {"context_path": "/tmp/does-not-exist-riptide.json"}))
        assert out.get("failed") is True
        assert "context file missing" in out["error"]
        assert "findings_path" not in out

    def test_unset_context_path_fails(self):
        c = Conductor.__new__(Conductor)
        c.track_id = "t"
        out = c._run_judge(_brief("judge", {}))
        assert out.get("failed") is True


# ── 2. Scribe never posts a false-clean review ───────────────────────────────


class TestScribeFindingsResolution:
    def test_refuses_when_no_findings_available(self, monkeypatch):
        posted = []
        monkeypatch.setattr(
            conductor, "Scribe",
            lambda: type("S", (), {"post_review_with_assembler": lambda *a, **k: posted.append(a) or {"posted": True}})(),
        )
        c = Conductor.__new__(Conductor)
        c.track_id = "t"
        out = c._run_scribe(_brief("scribe", {"action": "post_review", "pr_number": 190}))
        assert out["posted"] is False
        assert "refusing" in out["error"]
        assert posted == [], "must not post a review without findings"

    def test_posts_findings_loaded_from_findings_path(self, monkeypatch, tmp_path):
        findings = [{"severity": "warning", "title": "Missing retry", "file": "a.py", "line": 3}]
        findings_file = tmp_path / "findings.json"
        # The judge stamps its payload as judged; only then is it postable.
        findings_file.write_text(json.dumps({"judged": True, "findings": findings}))

        posted = []
        monkeypatch.setattr(
            conductor, "Scribe",
            lambda: type("S", (), {"post_review_with_assembler": staticmethod(lambda *a, **k: posted.append(a) or {"posted": True})})(),
        )
        c = Conductor.__new__(Conductor)
        c.track_id = "t"
        out = c._run_scribe(
            _brief("scribe", {
                "action": "post_review",
                "pr_number": 190,
                "findings_path": str(findings_file),
            })
        )
        assert out["posted"] is True
        assert posted[0][3] == findings, "scribe must post the judge's findings"

    def test_refuses_unjudged_payload(self, monkeypatch, tmp_path):
        """An empty/unspecified judge result must not become a clean review."""
        f = tmp_path / "findings.json"
        f.write_text(json.dumps({"findings": []}))
        posted = []
        monkeypatch.setattr(
            conductor, "Scribe",
            lambda: type("S", (), {"post_review_with_assembler": staticmethod(lambda *a, **k: posted.append(a) or {"posted": True})})(),
        )
        c = Conductor.__new__(Conductor)
        c.track_id = "t"
        out = c._run_scribe(
            _brief("scribe", {"action": "post_review", "pr_number": 190, "findings_path": str(f)})
        )
        assert out["posted"] is False
        assert "not marked judged" in out["error"]
        assert posted == []

    def test_refuses_already_reviewed_without_findings(self, monkeypatch, tmp_path):
        f = tmp_path / "findings.json"
        f.write_text(json.dumps({"judged": True, "already_reviewed": True, "findings": []}))
        posted = []
        monkeypatch.setattr(
            conductor, "Scribe",
            lambda: type("S", (), {"post_review_with_assembler": staticmethod(lambda *a, **k: posted.append(a) or {"posted": True})})(),
        )
        c = Conductor.__new__(Conductor)
        c.track_id = "t"
        out = c._run_scribe(
            _brief("scribe", {"action": "post_review", "pr_number": 190, "findings_path": str(f)})
        )
        assert out["posted"] is False
        assert "already_reviewed" in out["error"]
        assert posted == []

    def test_unreadable_findings_file_is_refused(self, monkeypatch, tmp_path):
        bad = tmp_path / "findings.json"
        bad.write_text("{not json")
        posted = []
        monkeypatch.setattr(
            conductor, "Scribe",
            lambda: type("S", (), {"post_review_with_assembler": lambda *a, **k: posted.append(a) or {"posted": True}})(),
        )
        c = Conductor.__new__(Conductor)
        c.track_id = "t"
        out = c._run_scribe(
            _brief("scribe", {"action": "post_review", "pr_number": 190, "findings_path": str(bad)})
        )
        assert out["posted"] is False
        assert posted == []


# ── 3. Gate markers on assembled reviews ─────────────────────────────────────


class TestGateMarkers:
    CRITICAL = {
        "severity": "critical", "title": "Race condition", "detail": "d",
        "file": "webhook.py", "line": 344,
    }
    WARNING = {
        "severity": "warning", "title": "Missing retry", "detail": "d",
        "file": "worker.py", "line": 12,
    }

    def test_findings_body_carries_gate_marker(self):
        body = assemble_review_body([self.WARNING], "ChonSong", "riptide", 190)
        assert body.startswith("## Review:")
        assert "## Review:" in body

    def test_findings_body_has_detectable_severity_rows(self):
        """The gate requires a '| 🔴' or '| 🟡' row to require a follow-up commit."""
        body = assemble_review_body([self.CRITICAL, self.WARNING], "ChonSong", "riptide", 190)
        assert "| 🔴" in body
        assert "| 🟡" in body

    def test_clean_body_is_marked_and_has_no_severity_rows(self):
        body = assemble_review_body([], "ChonSong", "riptide", 190)
        assert body.startswith("## Review: ✅ No findings")
        assert "🔴" not in body
        assert "🟡" not in body

    def test_suggestion_only_review_has_no_severity_rows(self):
        body = assemble_review_body(
            [{"severity": "suggestion", "title": "Nit", "detail": "d", "file": "a.py", "line": 1}],
            "ChonSong", "riptide", 190,
        )
        assert "🔴" not in body
        assert "🟡" not in body


# ── 4. Reservation liveness + poller re-review ───────────────────────────────


@pytest.fixture()
def store(tmp_path):
    return StateStore(str(tmp_path / "state.db"))


class TestReservationRelease:
    NAME = "riptide-review-ChonSong-riptide-190"

    def _reserve(self, store):
        job_id = f"{self.NAME}-c5ca51663385-abcdef123456"
        assert store.reserve_job(job_id, 190, "t1", self.NAME) is True
        return job_id

    def _cron_store(self, monkeypatch, tmp_path, state, enabled=True):
        jobs = tmp_path / "jobs.json"
        jobs.write_text(json.dumps({
            "jobs": [{"name": self.NAME, "state": state, "enabled": enabled}]
        }))
        monkeypatch.setattr(deepthink, "CRON_JOBS_PATH", Path(jobs))

    def test_finished_job_releases_reservation(self, store, monkeypatch, tmp_path):
        self._reserve(store)
        self._cron_store(monkeypatch, tmp_path, "completed", enabled=False)
        assert deepthink._release_finished_reservations(store, self.NAME) == 1
        assert store.list_pending_jobs(self.NAME) == []
        # A later review is no longer blocked.
        assert store.reserve_job(f"{self.NAME}-new-1", 190, "t1", self.NAME) is True

    def test_vanished_job_releases_reservation(self, store, monkeypatch, tmp_path):
        self._reserve(store)
        jobs = tmp_path / "jobs.json"
        jobs.write_text(json.dumps({"jobs": []}))
        monkeypatch.setattr(deepthink, "CRON_JOBS_PATH", Path(jobs))
        assert deepthink._release_finished_reservations(store, self.NAME) == 1

    def test_scheduled_job_keeps_reservation(self, store, monkeypatch, tmp_path):
        self._reserve(store)
        self._cron_store(monkeypatch, tmp_path, "scheduled")
        assert deepthink._release_finished_reservations(store, self.NAME) == 0
        assert len(store.list_pending_jobs(self.NAME)) == 1

    def test_unknown_store_state_is_conservative(self, store, monkeypatch, tmp_path):
        self._reserve(store)
        monkeypatch.setattr(deepthink, "CRON_JOBS_PATH", Path(tmp_path / "missing.json"))
        assert deepthink._release_finished_reservations(store, self.NAME) == 0

    def test_delivered_review_releases_reservation(self, store, monkeypatch, tmp_path):
        """A long session leaves the job record at 'scheduled' long after the
        review is posted — the delivered review must still release the lock."""
        self._reserve(store)
        self._cron_store(monkeypatch, tmp_path, "scheduled")  # record lags reality
        monkeypatch.setattr(deepthink, "_has_riptide_review", lambda o, r, p: True)
        released = deepthink._release_finished_reservations(
            store, self.NAME, "ChonSong", "riptide", 190
        )
        assert released == 1
        assert store.list_pending_jobs(self.NAME) == []

    def test_running_job_without_review_keeps_reservation(self, store, monkeypatch, tmp_path):
        self._reserve(store)
        self._cron_store(monkeypatch, tmp_path, "scheduled")
        monkeypatch.setattr(deepthink, "_has_riptide_review", lambda o, r, p: False)
        assert deepthink._release_finished_reservations(
            store, self.NAME, "ChonSong", "riptide", 190
        ) == 0
        assert len(store.list_pending_jobs(self.NAME)) == 1


class TestWorkstreamVerification:
    """Roles that report in-band must not be marked failed for writing no file."""

    def _run(self, monkeypatch, output, role="scribe"):
        statuses = []
        monkeypatch.setattr(
            conductor, "update_workstream",
            lambda *a, **k: statuses.append(k.get("status")),
        )
        monkeypatch.setattr(conductor, "Warden", _AllPassWarden)
        c = Conductor.__new__(Conductor)
        c.track_id = "track-under-test"
        c.track = {"key_facts": {}, "workstreams": {}}
        monkeypatch.setattr(c, "_get_track", lambda: c.track)
        monkeypatch.setattr(c, "_dispatch", lambda r, b: output)
        result = c._run_workstream(
            f"ws-{role}", {"role": role, "inputs": {"pr_number": 190}}
        )
        return result, statuses

    def test_successful_scribe_post_is_marked_done(self, monkeypatch):
        result, _ = self._run(monkeypatch, {"posted": True})
        assert result["status"] == "done"

    def test_scribe_refusal_is_marked_failed(self, monkeypatch):
        result, _ = self._run(monkeypatch, {"posted": False, "error": "refusing to post"})
        assert result["status"] == "failed"

    def test_error_output_is_marked_failed(self, monkeypatch):
        result, _ = self._run(monkeypatch, {"error": "boom"})
        assert result["status"] == "failed"


class TestModelAttribution:
    """A review must name the model that actually ran, not a code default.

    The spawned session's environment does not carry the app's .env, so the
    model/provider has to travel with the pipeline — otherwise the sign-off
    reported `custom:LongCat-2.0` for a deepseek-v4-flash review.
    """

    def test_scribe_receives_model_and_provider(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            conductor, "Scribe",
            lambda: type("S", (), {"post_review_with_assembler": staticmethod(
                lambda *a, **k: captured.update(k=k) or {"posted": True})})(),
        )
        c = Conductor.__new__(Conductor)
        c.track_id = "t"
        out = c._run_scribe(_brief("scribe", {
            "action": "post_review",
            "pr_number": 191,
            "owner": "ChonSong",
            "repo": "riptide",
            "findings": [],
            "model": "deepseek-v4-flash",
            "provider": "deepseek",
        }))
        assert out["posted"] is True
        assert captured["k"]["model"] == "deepseek-v4-flash"
        assert captured["k"]["provider"] == "deepseek"

    def test_builders_carry_model_into_scribe_inputs(self, monkeypatch):
        created: dict = {}
        monkeypatch.setattr(conductor, "get_track", lambda t: {"repos": {}})
        monkeypatch.setattr(conductor, "create_track", lambda *a, **k: {"repos": {}})
        monkeypatch.setattr(
            conductor, "create_workstream",
            lambda track_id, ws_id, **k: created.setdefault(ws_id, k.get("inputs", {})),
        )
        for builder in (conductor.create_webhook_review_pipeline,
                        conductor.create_deepthink_review_pipeline):
            created.clear()
            builder(owner="ChonSong", repo="riptide", pr_number=191,
                    pr_details={"head": {"sha": "abc123"}}, files=[],
                    model="deepseek-v4-flash", provider="deepseek")
            assert created["ws-5-scribe"]["model"] == "deepseek-v4-flash"
            assert created["ws-5-scribe"]["provider"] == "deepseek"


class TestFixerPromptReadiness:
    """The @riptide-bot fix prompt must match the current review format."""

    def _prompt(self):
        from riptide import fixer
        return fixer._build_fix_prompt(
            owner="ChonSong", repo="riptide", pr_number=191,
            pr_title="t", pr_author="ChonSong", total_loc=10,
            head_sha="dcd7a5a1234567890", head_ref="fix/x", description="d",
            push_eligible=True, job_id="job-1",
        )

    def test_points_at_the_current_review_format(self):
        prompt = self._prompt()
        assert "## Review:" in prompt
        assert "## Riptide Pass:" in prompt, "must warn that a pass is not a review"
        assert "issues/191/comments" in prompt

    def test_footer_names_the_configured_model(self):
        from riptide import fixer
        prompt = self._prompt()
        assert fixer.FIX_MODEL in prompt
        assert "<model_name>" not in prompt


class TestPollerReviewDetection:
    def test_detects_review_signoff(self, monkeypatch):
        out = type("P", (), {
            "returncode": 0,
            "stdout": json.dumps([
                "## Review: 3 warning(s).\n\n1. **X** ...\n\n<sub>Riptide Review · model: `deepseek-v4-flash`</sub>",
                "nice work",
            ]),
            "stderr": "",
        })()
        monkeypatch.setattr(deepthink.subprocess, "run", lambda *a, **k: out)
        assert deepthink._has_riptide_review("ChonSong", "riptide", 190) is True

    def test_companion_pass_confirmation_is_not_a_review(self, monkeypatch):
        """The pass confirmation used to count, which kept unreviewed PRs skipped."""
        out = type("P", (), {
            "returncode": 0,
            "stdout": json.dumps([
                "## Review: ✅ No findings\n\n**Riptide Review Complete — No findings**\n\n"
                "Deterministic analysis found no issues with this PR.\n\n"
                "**Depth:** standard | **Verdict:** pass",
            ]),
            "stderr": "",
        })()
        monkeypatch.setattr(deepthink.subprocess, "run", lambda *a, **k: out)
        assert deepthink._has_riptide_review("ChonSong", "riptide", 190) is False

    def test_no_review_marker_returns_false(self, monkeypatch):
        out = type("P", (), {
            "returncode": 0,
            "stdout": json.dumps(["@riptide-bot review", "LGTM"]),
            "stderr": "",
        })()
        monkeypatch.setattr(deepthink.subprocess, "run", lambda *a, **k: out)
        assert deepthink._has_riptide_review("ChonSong", "riptide", 190) is False

    def test_gh_failure_fails_closed(self, monkeypatch):
        out = type("P", (), {"returncode": 1, "stdout": "", "stderr": "boom"})()
        monkeypatch.setattr(deepthink.subprocess, "run", lambda *a, **k: out)
        assert deepthink._has_riptide_review("ChonSong", "riptide", 190) is True

    def test_exception_fails_closed(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("gh missing")
        monkeypatch.setattr(deepthink.subprocess, "run", boom)
        assert deepthink._has_riptide_review("ChonSong", "riptide", 190) is True
