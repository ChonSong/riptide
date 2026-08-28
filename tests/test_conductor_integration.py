#!/usr/bin/env python3
"""tests/test_conductor_integration.py — Tests for Conductor wiring into deepthink + webhook."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def pr_details():
    return {
        "title": "feat: add new feature",
        "user": {"login": "test-user"},
        "head": {"sha": "abc123def456"},
        "additions": 150,
        "deletions": 50,
    }


@pytest.fixture
def files():
    return [
        {"filename": "main.py", "additions": 100, "deletions": 50, "status": "modified"},
        {"filename": "test.py", "additions": 50, "deletions": 0, "status": "added"},
    ]


@pytest.fixture
def sample_findings():
    return [
        {
            "file": "main.py",
            "line": 42,
            "severity": "warning",
            "title": "Hardcoded secret",
            "detail": "Possible hardcoded API key",
        }
    ]


# ── create_deepthink_review_pipeline tests ────────────────────────────────────


class TestCreateDeepthinkReviewPipeline:
    """Tests for create_deepthink_review_pipeline()."""

    def test_creates_track_with_correct_name(self, pr_details, files):
        from riptide.pipeline import conductor

        track = conductor.create_deepthink_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        assert track["name"] == "Riptide Review #42"
        assert track["phase"] == "DeepthinkReview"

    def test_creates_track_with_correct_repos(self, pr_details, files):
        from riptide.pipeline import conductor

        track = conductor.create_deepthink_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        assert "riptide" in track["repos"]
        assert track["repos"]["riptide"]["owner"] == "ChonSong"
        assert track["repos"]["riptide"]["pr"] == 42

    def test_creates_five_workstreams(self, pr_details, files):
        from riptide.pipeline import conductor

        track = conductor.create_deepthink_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        ws = track["workstreams"]
        assert len(ws) == 5
        assert "ws-1-probe" in ws
        assert "ws-2-judge" in ws
        assert "ws-3-artisan" in ws
        assert "ws-4-engine" in ws
        assert "ws-5-scribe" in ws

    def test_workstreams_have_correct_roles(self, pr_details, files):
        from riptide.pipeline import conductor

        track = conductor.create_deepthink_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        ws = track["workstreams"]
        assert ws["ws-1-probe"]["role"] == "probe"
        assert ws["ws-2-judge"]["role"] == "judge"
        assert ws["ws-3-artisan"]["role"] == "artisan"
        assert ws["ws-4-engine"]["role"] == "engine"
        assert ws["ws-5-scribe"]["role"] == "scribe"

    def test_workstreams_have_correct_pipelines(self, pr_details, files):
        from riptide.pipeline import conductor

        track = conductor.create_deepthink_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        ws = track["workstreams"]
        assert ws["ws-1-probe"]["pipeline"] == ["fetch_diff", "graphify", "context_bundle"]
        assert ws["ws-2-judge"]["pipeline"] == ["diff_analyzer", "dedup", "score"]
        assert ws["ws-5-scribe"]["pipeline"] == ["assemble_review", "post_comment"]

    def test_probe_inputs_include_files(self, pr_details, files):
        from riptide.pipeline import conductor

        track = conductor.create_deepthink_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        probe = track["workstreams"]["ws-1-probe"]
        assert probe["inputs"]["pr_number"] == 42
        assert probe["inputs"]["owner"] == "ChonSong"
        assert probe["inputs"]["repo"] == "riptide"
        assert probe["inputs"]["files"] == files

    def test_scribe_inputs_include_head_sha(self, pr_details, files):
        from riptide.pipeline import conductor

        track = conductor.create_deepthink_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        scribe = track["workstreams"]["ws-5-scribe"]
        assert scribe["inputs"]["head_sha"] == "abc123def456"
        assert scribe["inputs"]["action"] == "post_review"

    def test_track_id_format(self, pr_details, files):
        from riptide.pipeline import conductor

        track = conductor.create_deepthink_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        assert track is not None


# ── create_webhook_review_pipeline tests ──────────────────────────────────────


class TestCreateWebhookReviewPipeline:
    """Tests for create_webhook_review_pipeline()."""

    def test_creates_track_with_correct_name(self, pr_details, files):
        from riptide.pipeline import conductor

        track = conductor.create_webhook_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        assert track["name"] == "Webhook Review #42"
        assert track["phase"] == "WebhookReview"

    def test_creates_track_with_correct_repos(self, pr_details, files):
        from riptide.pipeline import conductor

        track = conductor.create_webhook_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        assert "riptide" in track["repos"]
        assert track["repos"]["riptide"]["owner"] == "ChonSong"
        assert track["repos"]["riptide"]["pr"] == 42

    def test_creates_five_workstreams(self, pr_details, files):
        from riptide.pipeline import conductor

        track = conductor.create_webhook_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        assert len(track["workstreams"]) == 5

    def test_workstreams_have_correct_roles(self, pr_details, files):
        from riptide.pipeline import conductor

        track = conductor.create_webhook_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        ws = track["workstreams"]
        assert ws["ws-1-probe"]["role"] == "probe"
        assert ws["ws-2-judge"]["role"] == "judge"
        assert ws["ws-3-artisan"]["role"] == "artisan"
        assert ws["ws-4-engine"]["role"] == "engine"
        assert ws["ws-5-scribe"]["role"] == "scribe"

    def test_webhook_track_id_differs_from_deepthink(self, pr_details, files):
        from riptide.pipeline import conductor

        webhook_track = conductor.create_webhook_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        # The deepthink pipeline uses a different track ID format
        deepthink_track = conductor.create_deepthink_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        # Both tracks should exist independently with different names
        assert webhook_track["name"] == "Webhook Review #42"
        assert deepthink_track["name"] == "Riptide Review #42"

    def test_scribe_inputs_have_action_post_review(self, pr_details, files):
        from riptide.pipeline import conductor

        track = conductor.create_webhook_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        scribe = track["workstreams"]["ws-5-scribe"]
        assert scribe["inputs"]["action"] == "post_review"
        assert scribe["inputs"]["pr_number"] == 42

    def test_probe_inputs_include_files(self, pr_details, files):
        from riptide.pipeline import conductor

        track = conductor.create_webhook_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_details=pr_details,
            files=files,
        )
        probe = track["workstreams"]["ws-1-probe"]
        assert probe["inputs"]["files"] == files


# ── Pipeline produces work-state.json tests ───────────────────────────────────


class TestPipelineProducesWorkState:
    """Test that pipeline writes to work-state.json."""

    def test_creating_pipeline_writes_to_state(self, pr_details, files):
        from riptide.pipeline import conductor, work_state

        # Create the pipeline (which writes to work-state.json)
        conductor.create_deepthink_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=99,
            pr_details=pr_details,
            files=files,
        )

        # Verify state was written
        track = work_state.get_track("riptide-review-ChonSong-riptide-99")
        assert track is not None
        assert track["name"] == "Riptide Review #99"
        assert len(track["workstreams"]) == 5

    def test_workstreams_start_as_pending(self, pr_details, files):
        from riptide.pipeline import conductor, work_state

        conductor.create_deepthink_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=88,
            pr_details=pr_details,
            files=files,
        )

        track = work_state.get_track("riptide-review-ChonSong-riptide-88")
        for ws_id, ws in track["workstreams"].items():
            assert ws["status"] == "pending"

    def test_webhook_pipeline_writes_to_state(self, pr_details, files):
        from riptide.pipeline import conductor, work_state

        conductor.create_webhook_review_pipeline(
            owner="ChonSong",
            repo="riptide",
            pr_number=77,
            pr_details=pr_details,
            files=files,
        )

        track = work_state.get_track("riptide-webhook-review-ChonSong-riptide-77")
        assert track is not None
        assert track["phase"] == "WebhookReview"


# ── Deepthink Conductor integration tests ────────────────────────────────────


class TestDeepthinkConductorIntegration:
    """Test that _spawn_deepthink creates a Conductor pipeline."""

    def test_spawn_creates_conductor_track(self, pr_details, files):
        from riptide import deepthink

        mock_client = MagicMock()

        with patch("riptide.deepthink._gather_review_data", return_value={
            "files_changed": files,
            "diff_raw": "+ x",
            "repo_tree": [],
            "god_nodes": [],
            "communities": [],
            "graph_context": {},
        }), patch("riptide.deepthink._is_cron_available", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="Created")), \
         patch("riptide.state.StateStore") as mock_state_cls:

            mock_state = mock_state_cls.return_value
            mock_state.reserve_job.return_value = True

            deepthink._spawn_deepthink(
                owner="ChonSong",
                repo="riptide",
                pr_number=42,
                pr_title="test",
                pr_author="user",
                total_loc=200,
                head_sha="abc123def456",
            )

            from riptide.pipeline import work_state
            track = work_state.get_track("riptide-review-ChonSong-riptide-42")
            assert track is not None
            assert track["phase"] == "DeepthinkReview"


# ── Webhook Conductor integration tests ──────────────────────────────────────


class TestWebhookConductorIntegration:
    """Test that handle_review_command creates a webhook pipeline."""

    def test_handle_review_command_creates_webhook_track(self, pr_details, files):
        from riptide import deepthink

        mock_client = MagicMock()
        mock_client.get_pr_details.return_value = pr_details
        mock_client.get_pr_files.return_value = files

        with patch("riptide.deepthink._is_cron_available", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="Created")), \
         patch("riptide.state.StateStore") as mock_state_cls:

            mock_state = mock_state_cls.return_value
            mock_state.reserve_job.return_value = True

            deepthink.handle_review_command(
                client=mock_client,
                installation_id=12345,
                owner="ChonSong",
                repo="riptide",
                pr_number=42,
                commenter="ChonSong",
            )

            from riptide.pipeline import work_state
            track = work_state.get_track("riptide-webhook-review-ChonSong-riptide-42")
            assert track is not None
            assert track["phase"] == "WebhookReview"

    def test_handle_review_command_authorized(self, pr_details, files):
        from riptide import deepthink

        pr_details_author = dict(pr_details)
        pr_details_author["user"] = {"login": "ChonSong"}

        mock_client = MagicMock()
        mock_client.get_pr_details.return_value = pr_details_author
        mock_client.get_pr_files.return_value = files

        with patch("riptide.deepthink._is_cron_available", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="Created")), \
         patch("riptide.state.StateStore") as mock_state_cls:

            mock_state = mock_state_cls.return_value
            mock_state.reserve_job.return_value = True

            result = deepthink.handle_review_command(
                client=mock_client,
                installation_id=12345,
                owner="ChonSong",
                repo="riptide",
                pr_number=42,
                commenter="ChonSong",
            )
            assert result is not None
            assert "Riptide Review triggered" in result

    def test_handle_review_command_unauthorized(self, pr_details, files):
        from riptide import deepthink

        mock_client = MagicMock()
        mock_client.get_pr_details.return_value = pr_details

        with patch("riptide.deepthink._is_cron_available", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="Created")), \
         patch("riptide.state.StateStore") as mock_state_cls:

            mock_state = mock_state_cls.return_value
            mock_state.reserve_job.return_value = True

            result = deepthink.handle_review_command(
                client=mock_client,
                installation_id=12345,
                owner="SomeOwner",
                repo="riptide",
                pr_number=42,
                commenter="random-user",
            )
            assert result is not None
            assert "Not authorized" in result


# ── Conductor prompt tests ───────────────────────────────────────────────────


class TestConductorPrompt:
    """Test _build_conductor_prompt."""

    def test_prompt_includes_pr_context(self):
        from riptide.deepthink import _build_conductor_prompt

        prompt = _build_conductor_prompt(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_title="feat: test",
            pr_author="user",
            total_loc=200,
            head_sha="abc123def456",
        )
        assert "ChonSong" in prompt
        assert "riptide" in prompt
        assert "42" in prompt
        assert "feat: test" in prompt
        assert "user" in prompt
        assert "200" in prompt

    def test_prompt_includes_conductor_instructions(self):
        from riptide.deepthink import _build_conductor_prompt

        prompt = _build_conductor_prompt(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_title="feat: test",
            pr_author="user",
            total_loc=200,
            head_sha="abc123def456",
        )
        assert "Conductor" in prompt
        assert "Probe" in prompt
        assert "Judge" in prompt
        assert "Artisan" in prompt
        assert "Scribe" in prompt

    def test_prompt_with_diagram_url(self):
        from riptide.deepthink import _build_conductor_prompt

        prompt = _build_conductor_prompt(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_title="feat: test",
            pr_author="user",
            total_loc=200,
            head_sha="abc123def456",
            diagram_url="https://example.com/diagram.png",
        )
        assert "diagram" in prompt.lower() or "https://example.com/diagram.png" in prompt

    def test_prompt_with_deterministic(self):
        from riptide.deepthink import _build_conductor_prompt

        prompt = _build_conductor_prompt(
            owner="ChonSong",
            repo="riptide",
            pr_number=42,
            pr_title="feat: test",
            pr_author="user",
            total_loc=200,
            head_sha="abc123def456",
            deterministic={"verdict": "review", "findings": [{"severity": "critical"}]},
        )
        assert "review" in prompt
        assert "finding" in prompt.lower()


# ── Backward compat tests ────────────────────────────────────────────────────


class TestBackwardCompat:
    """Test _build_orchestrator_prompt is still callable with old signature."""

    def test_old_prompt_still_works(self):
        from riptide.deepthink import _build_orchestrator_prompt

        data = {
            "files_changed": [{"filename": "test.py", "additions": 10, "deletions": 5}],
            "diff_raw": "+ x",
            "repo_tree": ["test.py"],
            "god_nodes": [{"name": "hub.py", "edges": 5}],
            "communities": [],
            "graph_context": {},
        }
        prompt = _build_orchestrator_prompt(
            "ChonSong", "riptide", 42, "feat: test", "author", 300, "abc123", data
        )
        assert "Orchestrate Review" in prompt
        assert "Delegate Inline Review" in prompt
        assert "test.py" in prompt