# riptide/tests/test_companion.py
"""
Tests for Riptide Companion bot (Bot 1).
Covers PR classification, UI detection, TL;DR generation, and Ollama edge cases.
"""

import os
import re
import time
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from riptide.companion import Companion, classify_pr_mood


# ── Helpers ──────────────────────────────────────────────────────────────────


def make_companion(tmp_path=None):
    """Create a Companion instance with mocked github client and disabled warm-up."""
    client = MagicMock()
    with patch("threading.Thread"):
        # WS-3 Stage 0: inject an isolated StateStore so tests never touch the
        # real prod state.db and legacy-skip import is a no-op (no file).
        from riptide.state import StateStore
        import tempfile as _tempfile

        state_dir = tmp_path if tmp_path else _tempfile.mkdtemp(prefix="companion-test-")
        store = StateStore(str(Path(state_dir) / "state.db"))
        companion = Companion(client, state_store=store)
    if tmp_path:
        companion._alert_file = tmp_path / "companion_alerts.json"
        companion._alert_lock = threading.Lock()
    return companion


# ── Ollama endpoint ─────────────────────────────────────────────────────────


class TestOllamaEndpoint:
    """Default Ollama base must match the actual local port (regression: 43311)."""

    def test_default_ollama_base_is_11434(self):
        with patch.dict(os.environ, {}, clear=True):
            companion = make_companion()
            assert companion.ollama_base == "http://localhost:11434"

    def test_ollama_base_honors_env_override(self):
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://ollama:8080"}, clear=True):
            companion = make_companion()
            assert companion.ollama_base == "http://ollama:8080"


# ── StateStore-backed heuristics (WS-3 Stage 0) ─────────────────────────────


class TestStateStoreHeuristics:
    """Skip/last_sha now persist in StateStore.pr_heuristics, not JSON files."""

    def test_skip_persists_via_state_store(self, tmp_path):
        companion = make_companion(tmp_path)
        assert companion.set_skip("ChonSong", "riptide", 42, True) is True
        assert companion._is_skipped("ChonSong", "riptide", 42) is True
        # A fresh instance backed by the same store still sees the skip
        fresh = make_companion(tmp_path)
        assert fresh._is_skipped("ChonSong", "riptide", 42) is True

    def test_resume_clears_skip(self, tmp_path):
        companion = make_companion(tmp_path)
        companion.set_skip("ChonSong", "riptide", 42, True)
        companion.set_skip("ChonSong", "riptide", 42, False)
        assert companion._is_skipped("ChonSong", "riptide", 42) is False

    def test_last_sha_roundtrip(self, tmp_path):
        companion = make_companion(tmp_path)
        assert companion._get_last_sha("ChonSong", "riptide", 42) is None
        assert companion._set_last_sha("ChonSong", "riptide", 42, "abc123") is True
        assert companion._get_last_sha("ChonSong", "riptide", 42) == "abc123"

    def test_skip_and_last_sha_coexist(self, tmp_path):
        """Setting skip must not clobber last_sha and vice versa."""
        companion = make_companion(tmp_path)
        companion._set_last_sha("ChonSong", "riptide", 42, "abc123")
        companion.set_skip("ChonSong", "riptide", 42, True)
        assert companion._get_last_sha("ChonSong", "riptide", 42) == "abc123"
        assert companion._is_skipped("ChonSong", "riptide", 42) is True

    def test_legacy_skip_file_import(self, tmp_path):
        """A pre-existing companion_skip.json must import into StateStore once."""
        import riptide.companion as companion_mod
        companion_mod._legacy_skip_imported = False
        legacy = tmp_path / "companion_skip.json"
        legacy.write_text(
            '{"ChonSong/riptide#42": {"skip": true, "last_sha": "legacy1"}}'
        )
        # Point RIPTIDE_DATA_DIR to tmp_path so companion.__init__ finds the legacy file
        with patch.dict(os.environ, {"RIPTIDE_DATA_DIR": str(tmp_path)}):
            companion = make_companion(tmp_path)
            # __init__ already ran _import_legacy_skip_file since the file existed
            assert companion._is_skipped("ChonSong", "riptide", 42) is True
            assert companion._get_last_sha("ChonSong", "riptide", 42) == "legacy1"


# ── classify_pr_mood ────────────────────────────────────────────────────────


class TestClassifyPrMood:
    """Tests for the module-level classify_pr_mood function."""

    @pytest.mark.parametrize(
        "title,expected_emoji",
        [
            ("feat: implement new button component", "✨"),
            ("add: support for dark mode", "✨"),
            ("feature: implement caching", "✨"),
            ("fix: resolve crash on startup", "🐛"),
            ("bug: null pointer in handler", "🐛"),
            ("hotfix: emergency patch", "🐛"),
            ("refactor: extract helper function", "♻️"),
            ("cleanup: remove dead code", "🧹"),
            ("tech debt: migrate to v2", "🧹"),
            ("perf: improve query speed", "⚡"),
            ("performance: reduce memory usage", "⚡"),
            ("docs: update README", "📝"),
            ("documentation: update api guide", "📝"),
            ("config: update settings", "🔧"),
            ("infra: migrate to new server", "🔧"),
            ("deps: bump lodash", "📦"),
            ("upgrade: react 19", "📦"),
            ("test: verify login flow", "🧪"),
            ("testing: verify edge cases", "🧪"),
            ("revert: undo bad commit", "⏪"),
            ("rollback: undo last change", "⏪"),
        ],
    )
    def test_classification(self, title, expected_emoji):
        assert classify_pr_mood(title, []) == expected_emoji

    def test_default_emoji_for_unknown_title(self):
        assert classify_pr_mood("misc: random changes", []) == "✨"

    def test_empty_title_defaults_to_sparkles(self):
        assert classify_pr_mood("", []) == "✨"

    def test_case_insensitive_matching(self):
        assert classify_pr_mood("FIX: resolve bug", []) == "🐛"
        assert classify_pr_mood("Feat: new feature", []) == "✨"
        assert classify_pr_mood("DOCS: update", []) == "📝"


# ── _is_valid ────────────────────────────────────────────────────────────────


class TestIsValid:
    """Tests for Companion._is_valid static method."""

    @pytest.mark.parametrize(
        "text",
        [
            "I cannot summarize this PR.",
            "I can't help with this.",
            "As an AI, I cannot provide...",
            "I'm sorry, but I cannot...",
            "I am sorry, I cannot do that.",
            "Unable to process this request.",
            "cannot provide a summary",
            "can't summarize this",
            "Please send a private message for help.",
            "PRIVATE MESSAGE me for details",
        ],
    )
    def test_rejects_bad_responses(self, text):
        assert Companion._is_valid(text) is False

    @pytest.mark.parametrize(
        "text",
        [
            "This PR adds a new authentication middleware.",
            "Refactored the database layer to use connection pooling.",
            "Fixed a race condition in the event handler.",
            "Updated documentation with new API examples.",
            "Adds performance optimizations to the query builder.",
        ],
    )
    def test_accepts_good_responses(self, text):
        assert Companion._is_valid(text) is True

    def test_empty_string_is_valid(self):
        assert Companion._is_valid("") is True

    def test_case_insensitive_rejection(self):
        assert Companion._is_valid("I CANNOT summarize this.") is False
        assert Companion._is_valid("AS AN AI language model...") is False


# ── _generate_tldr with Ollama mocking ────────────────────────────────────────


class TestGenerateTldr:
    """Tests for Companion._generate_tldr with mocked Ollama."""

    def test_successful_tldr_generation(self, mock_ollama):
        companion = make_companion()
        files = [
            {"filename": "src/utils/helper.py", "patch": "+def helper(): pass", "additions": 1, "deletions": 0, "status": "added"},
        ]
        result = companion._generate_tldr("feat: add helper", "testuser", files, None)
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    def test_tldr_with_failure_returns_none(self, mock_ollama_failure):
        companion = make_companion()
        files = [
            {"filename": "src/utils/helper.py", "patch": "+def helper(): pass", "additions": 1, "deletions": 0, "status": "added"},
        ]
        result = companion._generate_tldr("feat: add helper", "testuser", files, None)
        assert result is None

    def test_tldr_with_malformed_json_returns_none(self, mock_ollama_malformed):
        companion = make_companion()
        files = [
            {"filename": "src/utils/helper.py", "patch": "+def helper(): pass", "additions": 1, "deletions": 0, "status": "added"},
        ]
        result = companion._generate_tldr("feat: add helper", "testuser", files, None)
        assert result is None

    def test_tldr_with_refusal_returns_none(self, mock_ollama_refusal):
        companion = make_companion()
        files = [
            {"filename": "src/utils/helper.py", "patch": "+def helper(): pass", "additions": 1, "deletions": 0, "status": "added"},
        ]
        result = companion._generate_tldr("feat: add helper", "testuser", files, None)
        assert result is None

    def test_tldr_with_truncated_output(self, mock_ollama_truncated):
        companion = make_companion()
        files = [
            {"filename": "src/utils/helper.py", "patch": "+def helper(): pass", "additions": 1, "deletions": 0, "status": "added"},
        ]
        result = companion._generate_tldr("feat: add helper", "testuser", files, None)
        assert result is not None
        assert result == "This PR adds a"

    def test_tldr_with_graph_context(self, mock_ollama):
        companion = make_companion()
        files = [
            {"filename": "src/utils/helper.py", "patch": "+def helper(): pass", "additions": 1, "deletions": 0, "status": "added"},
        ]
        graph_context = {"raw": "helper.py → utils.py", "nodes": 2, "files_checked": 1}
        result = companion._generate_tldr("feat: add helper", "testuser", files, graph_context)
        assert result is not None


# ── _ollama_call edge cases ───────────────────────────────────────────────────


class TestOllamaCall:
    """Tests for Companion._ollama_call method."""

    def test_timeout_returns_none(self):
        companion = make_companion()
        with patch("requests.post", side_effect=__import__("requests").exceptions.Timeout("timed out")):
            result = companion._ollama_call("test prompt")
            assert result is None

    def test_connection_error_returns_none(self):
        companion = make_companion()
        with patch("requests.post", side_effect=__import__("requests").exceptions.ConnectionError("refused")):
            result = companion._ollama_call("test prompt")
            assert result is None

    def test_non_200_returns_none(self, mock_ollama_failure):
        companion = make_companion()
        result = companion._ollama_call("test prompt")
        assert result is None

    def test_valid_response_returned(self, mock_ollama):
        companion = make_companion()
        result = companion._ollama_call("test prompt")
        assert result is not None
        assert isinstance(result, str)


# ── _format_comment ──────────────────────────────────────────────────────────


class TestFormatComment:
    """Tests for Companion._format_comment method."""

    def test_emoji_included_in_output(self):
        companion = make_companion()
        result = companion._format_comment("✨", "testuser", "This PR adds feature X.", None)
        assert "✨" in result

    def test_tldr_included_in_output(self):
        companion = make_companion()
        tldr = "This PR adds a new authentication layer."
        result = companion._format_comment("✨", "testuser", tldr, None)
        assert tldr in result

    def test_author_mention_included(self):
        companion = make_companion()
        result = companion._format_comment("✨", "testuser", "Some TL;DR.", None)
        assert "@testuser" in result

    def test_graph_context_included(self):
        companion = make_companion()
        graph_context = {"raw": "helper.py → utils.py", "nodes": 3, "files_checked": 2}
        result = companion._format_comment("✨", "testuser", "Some TL;DR.", graph_context)
        assert "Blast Radius" in result

    def test_eli5_included(self):
        companion = make_companion()
        result = companion._format_comment("✨", "testuser", "Some TL;DR.", None, eli5="It's like adding a new room to a house.")
        assert "ELI5" in result
        assert "It's like adding a new room to a house." in result

    def test_proofshot_for_ui_files(self):
        companion = make_companion()
        ui_files = [{"filename": "src/components/Button.tsx"}]
        result = companion._format_comment("✨", "testuser", "Some TL;DR.", None, ui_files=ui_files)
        assert "ProofShot" in result
        assert "Button.tsx" in result

    def test_delta_prefix(self):
        companion = make_companion()
        result = companion._format_comment("✨", "testuser", "Some TL;DR.", None, is_delta=True)
        assert "🔄" in result

    def test_sign_off_included(self):
        companion = make_companion()
        result = companion._format_comment("✨", "testuser", "Some TL;DR.", None)
        assert "Riptide" in result


# ── Self-heal retry logic ────────────────────────────────────────────────────


class TestGenerateTldrWithRetry:
    def test_first_attempt_succeeds(self):
        companion = make_companion()
        with patch.object(companion, "_generate_tldr", return_value="TL;DR text") as mock_gen:
            result = companion._generate_tldr_with_retry("title", "author", [], None)
            assert result == "TL;DR text"
            assert mock_gen.call_count == 1

    def test_retries_on_failure_then_succeeds(self):
        companion = make_companion()
        with patch.object(companion, "_generate_tldr", side_effect=[None, "TL;DR text"]) as mock_gen:
            with patch("time.sleep"):
                result = companion._generate_tldr_with_retry("title", "author", [], None)
                assert result == "TL;DR text"
                assert mock_gen.call_count == 2

    def test_all_attempts_fail_returns_none(self):
        companion = make_companion()
        with patch.object(companion, "_generate_tldr", return_value=None) as mock_gen:
            with patch("time.sleep"):
                result = companion._generate_tldr_with_retry("title", "author", [], None)
                assert result is None
                assert mock_gen.call_count == 4

    def test_retry_delays(self):
        companion = make_companion()
        with patch.object(companion, "_generate_tldr", return_value=None):
            with patch("time.sleep") as mock_sleep:
                companion._generate_tldr_with_retry("title", "author", [], None)
                assert mock_sleep.call_count == 3
                mock_sleep.assert_any_call(5)
                mock_sleep.assert_any_call(30)
                mock_sleep.assert_any_call(60)


class TestHandleDegradation:
    def test_owned_repo_posts_comment(self):
        companion = make_companion()
        companion.client.post_pr_comment = MagicMock()
        with patch.object(companion, "_should_alert", return_value=True):
            with patch.object(companion, "_spawn_self_heal"):
                companion._handle_degradation(123, "ChonSong", "riptide", 42, "ChonSong/riptide")
                companion.client.post_pr_comment.assert_called_once()
                body = companion.client.post_pr_comment.call_args[0][4]
                assert "⚠️" in body
                assert "model offline" in body

    def test_unowned_repo_sends_discord(self):
        companion = make_companion()
        with patch.object(companion, "_should_alert", return_value=True):
            with patch("subprocess.run") as mock_run:
                with patch.object(companion, "_spawn_self_heal"):
                    companion._handle_degradation(123, "other", "repo", 42, "other/repo")
                    mock_run.assert_called_once()
                    cmd = mock_run.call_args[0][0]
                    assert "hermes" in cmd
                    assert "send" in cmd
                    assert "discord" in cmd

    def test_spawns_self_heal(self):
        companion = make_companion()
        companion.client.post_pr_comment = MagicMock()
        with patch.object(companion, "_should_alert", return_value=True):
            with patch.object(companion, "_spawn_self_heal") as mock_heal:
                companion._handle_degradation(123, "ChonSong", "riptide", 42, "ChonSong/riptide")
                mock_heal.assert_called_once_with("ChonSong/riptide", 42)

    def test_cooldown_suppresses_alert(self):
        companion = make_companion()
        companion.client.post_pr_comment = MagicMock()
        with patch.object(companion, "_should_alert", return_value=False):
            with patch.object(companion, "_spawn_self_heal") as mock_heal:
                companion._handle_degradation(123, "ChonSong", "riptide", 42, "ChonSong/riptide")
                companion.client.post_pr_comment.assert_not_called()
                mock_heal.assert_not_called()


class TestShouldAlert:
    def test_first_alert_allowed(self, tmp_path):
        companion = make_companion(tmp_path)
        assert companion._should_alert("ChonSong/riptide") is True

    def test_second_alert_same_pr_suppressed(self, tmp_path):
        companion = make_companion(tmp_path)
        assert companion._should_alert("ChonSong/riptide") is True
        assert companion._should_alert("ChonSong/riptide") is False

    @pytest.mark.parametrize(
        "pr1,pr2,expected",
        [
            ("ChonSong/riptide", "ChonSong/other", False),  # global blocks
            ("ChonSong/riptide", "ChonSong/riptide", False),  # per-pr blocks
        ],
    )
    def test_cooldown_blocks(self, tmp_path, pr1, pr2, expected):
        companion = make_companion(tmp_path)
        companion._pr_alert_cooldown = 600
        companion._global_alert_cooldown = 600
        assert companion._should_alert(pr1) is True
        assert companion._should_alert(pr2) is expected

    def test_different_pr_allowed_after_global_clears(self, tmp_path):
        companion = make_companion(tmp_path)
        companion._pr_alert_cooldown = 1
        companion._global_alert_cooldown = 1
        assert companion._should_alert("ChonSong/riptide") is True
        assert companion._should_alert("ChonSong/other") is False
        time.sleep(1.1)
        assert companion._should_alert("ChonSong/riptide") is True


class TestSpawnSelfHeal:
    def test_spawns_hermes_cron(self):
        companion = make_companion()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            companion._spawn_self_heal("ChonSong/riptide", 42)
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert "hermes" in cmd
            assert "cron" in cmd
            assert "create" in cmd
            assert "riptide-self-heal-42" in cmd
            assert "--repeat" in cmd
            assert "1" in cmd

    def test_failure_logged_not_raised(self):
        companion = make_companion()
        with patch("subprocess.run", side_effect=Exception("boom")):
            companion._spawn_self_heal("ChonSong/riptide", 42)

    def test_owned_org_from_env(self, monkeypatch):
        monkeypatch.setenv("RIPTIDE_OWNED_ORG", "other-org")
        companion = make_companion()
        companion.client.post_pr_comment = MagicMock()
        with patch.object(companion, "_should_alert", return_value=True):
            with patch.object(companion, "_spawn_self_heal"):
                companion._handle_degradation(123, "other-org", "repo", 42, "other-org/repo")
                companion.client.post_pr_comment.assert_called_once()


class TestDeterministicAnalysis:
    """Tests for the deterministic analysis integration in Companion."""

    def test_skip_comment_when_no_actionable_findings(self, mock_ollama):
        companion = make_companion()
        companion.enable_deterministic = True
        companion.enable_graphify = False
        companion.client.post_pr_comment = MagicMock()
        companion._get_last_sha = MagicMock(return_value=None)

        # Patch the context bundle to return a report with no findings
        mock_report = MagicMock()
        mock_report.has_actionable = False
        mock_report.findings = []
        mock_report.verdict = "pass"
        with patch("riptide.companion.build_context_bundle", return_value={"report": mock_report}):
            companion._execute(
                123, "owner", "repo", 42,
                "feat: trivial change", "author",
                [{"filename": "README.md", "patch": "+# Hello", "additions": 1, "deletions": 0, "status": "modified"}]
            )
        # Should NOT post a comment when no actionable findings
        companion.client.post_pr_comment.assert_not_called()

    def test_fallback_to_llm_when_analyzer_raises(self, mock_ollama):
        companion = make_companion()
        companion.enable_deterministic = True
        companion.enable_graphify = False
        companion.client.post_pr_comment = MagicMock()
        companion._get_last_sha = MagicMock(return_value=None)

        # Patch the context-bundle build to raise an exception (the bundle runs
        # DiffAnalyzer internally; a failure here must fall back to the LLM path)
        with patch.object(companion, "build_context_bundle", side_effect=re.error("bad regex")):
            with patch.object(companion, "_generate_tldr_with_retry", return_value="LLM fallback TL;DR") as mock_llm:
                companion._execute(
                    123, "owner", "repo", 42,
                    "feat: something", "author",
                    [{"filename": "src/main.py", "patch": "+x = 1", "additions": 1, "deletions": 0, "status": "modified"}]
                )
        # Should fall back to LLM path
        mock_llm.assert_called_once()
        companion.client.post_pr_comment.assert_called_once()
        body = companion.client.post_pr_comment.call_args[0][4]
        assert "LLM fallback TL;DR" in body


# ── Two-tier comment response (Vision Pillar 2) ──────────────────────────────


class TestTwoTierResponse:
    """Tests for the two-tier comment response flow.

    Tier 1: deterministic comment posted immediately (no LLM).
    Tier 2: LLM enrichment (ELI5) patches the SAME comment in place.
    """

    def test_two_tier_posts_then_patches_same_comment(self, mock_ollama):
        """Two-tier flow: POST Tier 1 with progress marker, then PATCH same comment_id with enriched body."""
        companion = make_companion()
        companion.enable_deterministic = True
        companion.enable_graphify = False
        companion._get_last_sha = MagicMock(return_value=None)

        # Configure client.post_pr_comment to return a comment id
        companion.client.post_pr_comment = MagicMock(return_value={"id": 999})
        companion.client.update_pr_comment = MagicMock(return_value={"id": 999})

        # Patch the analyzer to return a report with actionable findings
        mock_report = MagicMock()
        mock_report.has_actionable = True
        mock_report.verdict = "review"
        mock_report.summary = "Security risk detected"
        mock_report.findings = [
            MagicMock(severity="critical", message="Hardcoded secret", file="auth.py", category="security")
        ]
        with patch("riptide.companion.build_context_bundle", return_value={"report": mock_report}):
            companion._execute(
                123, "owner", "repo", 42,
                "feat: add auth", "author",
                [{"filename": "src/auth.py", "patch": "+secret = 'hardcoded'", "additions": 1, "deletions": 0, "status": "modified"}]
            )

        # POST should be called exactly once
        companion.client.post_pr_comment.assert_called_once()
        post_call_args = companion.client.post_pr_comment.call_args[0]
        tier1_body = post_call_args[4]
        # Tier 1 body must contain the progress marker (no LLM required)
        assert "🔍 enrichment in progress" in tier1_body

        # PATCH (update) should be called exactly once with the SAME comment_id
        companion.client.update_pr_comment.assert_called_once()
        patch_call_args = companion.client.update_pr_comment.call_args[0]
        assert patch_call_args[3] == 999  # same comment_id as returned by POST
        enriched_body = patch_call_args[4]
        # Enriched body contains ELI5 (from mocked Ollama)
        assert "ELI5" in enriched_body

    def test_two_tier_resync_patches_existing_thread_not_repost(self, mock_ollama):
        """Stage 2: when a Tier-1 comment id is already stored, a re-sync PATCHes
        the canonical thread instead of POSTing a duplicate."""
        companion = make_companion()
        companion.enable_deterministic = True
        companion.enable_graphify = False
        companion._get_last_sha = MagicMock(return_value=None)

        companion.client.post_pr_comment = MagicMock(return_value={"id": 999})
        companion.client.update_pr_comment = MagicMock(return_value={"id": 999})

        mock_report = MagicMock()
        mock_report.has_actionable = True
        mock_report.verdict = "review"
        mock_report.summary = "Security risk detected"
        mock_report.findings = [
            MagicMock(severity="critical", message="Hardcoded secret", file="auth.py", category="security")
        ]

        # Pre-seed the canonical thread id — simulates a prior Tier-1 POST.
        companion._state.set_pr_tier1_comment_id("owner/repo#42", 777)

        with patch("riptide.companion.build_context_bundle", return_value={"report": mock_report}):
            companion._execute(
                123, "owner", "repo", 42,
                "feat: add auth", "author",
                [{"filename": "src/auth.py", "patch": "+secret = 'hardcoded'", "additions": 1, "deletions": 0, "status": "modified"}]
            )

        # No new POST — the canonical thread is reused.
        companion.client.post_pr_comment.assert_not_called()
        # Tier 1 body PATCHed onto the existing thread, then enriched in place.
        assert companion.client.update_pr_comment.call_count == 2
        first_patch_args = companion.client.update_pr_comment.call_args_list[0].args
        assert first_patch_args[3] == 777  # canonical thread id
        # Stored id unchanged (still the canonical thread).
        assert companion._state.get_pr_tier1_comment_id("owner/repo#42") == 777

    def test_two_tier_fails_gracefully_when_enrichment_fails(self, mock_ollama):
        """If Tier 2 PATCH fails, Tier 1 comment remains (no deletion, no duplicate)."""
        companion = make_companion()
        companion.enable_deterministic = True
        companion.enable_graphify = False
        companion._get_last_sha = MagicMock(return_value=None)

        companion.client.post_pr_comment = MagicMock(return_value={"id": 888})
        companion.client.update_pr_comment = MagicMock(side_effect=Exception("API timeout"))

        mock_report = MagicMock()
        mock_report.has_actionable = True
        mock_report.verdict = "review"
        mock_report.summary = "Issue detected"
        mock_report.findings = [
            MagicMock(severity="warning", message="Complex function", file="util.py", category="complexity")
        ]
        with patch("riptide.companion.build_context_bundle", return_value={"report": mock_report}):
            companion._execute(
                123, "owner", "repo", 42,
                "feat: complex logic", "author",
                [{"filename": "src/util.py", "patch": "+def complex(): pass", "additions": 1, "deletions": 0, "status": "modified"}]
            )

        # Tier 1 posted
        companion.client.post_pr_comment.assert_called_once()
        # Tier 2 attempted
        companion.client.update_pr_comment.assert_called_once()
        # No second POST (no duplicate)
        assert companion.client.post_pr_comment.call_count == 1

    def test_two_tier_tier1_post_failure_does_not_record_sha(self, mock_ollama):
        """If Tier 1 POST raises, no SHA is recorded and no enrichment is attempted."""
        companion = make_companion()
        companion.enable_deterministic = True
        companion.enable_graphify = False
        companion._get_last_sha = MagicMock(return_value=None)
        companion._set_last_sha = MagicMock()

        companion.client.post_pr_comment = MagicMock(side_effect=Exception("API timeout"))
        companion.client.update_pr_comment = MagicMock()

        mock_report = MagicMock()
        mock_report.has_actionable = True
        mock_report.verdict = "review"
        mock_report.summary = "Issue detected"
        mock_report.findings = [
            MagicMock(severity="warning", message="Complex function", file="util.py", category="complexity")
        ]
        with patch("riptide.companion.build_context_bundle", return_value={"report": mock_report}):
            companion._execute(
                123, "owner", "repo", 42,
                "feat: complex logic", "author",
                [{"filename": "src/util.py", "patch": "+def complex(): pass", "additions": 1, "deletions": 0, "status": "modified"}]
            )

        # Tier 1 failed → no PATCH (enrichment), no SHA recorded
        companion.client.update_pr_comment.assert_not_called()
        companion._set_last_sha.assert_not_called()

    def test_two_tier_skips_when_no_actionable_findings(self, mock_ollama):
        """When deterministic report has no findings, neither Tier 1 nor Tier 2 runs."""
        companion = make_companion()
        companion.enable_deterministic = True
        companion.enable_graphify = False
        companion._get_last_sha = MagicMock(return_value=None)

        companion.client.post_pr_comment = MagicMock()
        companion.client.update_pr_comment = MagicMock()

        mock_report = MagicMock()
        mock_report.has_actionable = False
        mock_report.findings = []
        mock_report.verdict = "pass"
        with patch("riptide.companion.build_context_bundle", return_value={"report": mock_report}):
            companion._execute(
                123, "owner", "repo", 42,
                "feat: trivial change", "author",
                [{"filename": "README.md", "patch": "+# Hello", "additions": 1, "deletions": 0, "status": "modified"}]
            )

        companion.client.post_pr_comment.assert_not_called()
        companion.client.update_pr_comment.assert_not_called()

    def test_two_tier_not_used_when_deterministic_disabled(self, mock_ollama):
        """When deterministic is disabled, legacy single-POST path is used (no PATCH)."""
        companion = make_companion()
        companion.enable_deterministic = False
        companion.enable_graphify = False
        companion._get_last_sha = MagicMock(return_value=None)

        companion.client.post_pr_comment = MagicMock(return_value={"id": 777})
        companion.client.update_pr_comment = MagicMock()

        companion._execute(
            123, "owner", "repo", 42,
            "feat: new feature", "author",
            [{"filename": "src/main.py", "patch": "+def main(): pass", "additions": 1, "deletions": 0, "status": "modified"}]
        )

        # Only POST (legacy path), no PATCH
        companion.client.post_pr_comment.assert_called_once()
        companion.client.update_pr_comment.assert_not_called()


# ── Depth gating (WS-3 Stage 0) ─────────────────────────────────────────────


class TestDepthGate:
    """TRIVIAL depth → Tier 1 only; STANDARD/ARCH → Tier 1 + Tier 2 enrichment.

    Uses the same classify_review_depth rules as Deepthink (riptide.depth).
    """

    def _run(self, companion, files, title="feat: change", mock_report=None):
        companion.enable_deterministic = True
        companion.enable_graphify = False
        companion._get_last_sha = MagicMock(return_value=None)
        if mock_report is None:
            mock_report = MagicMock()
            mock_report.has_actionable = True
            mock_report.verdict = "review"
            mock_report.summary = "Issue detected"
            mock_report.findings = [
                MagicMock(severity="warning", message="Something", file="f.py", category="complexity")
            ]
        companion.client.post_pr_comment = MagicMock(return_value={"id": 777})
        companion.client.update_pr_comment = MagicMock(return_value={"id": 777})
        with patch("riptide.companion.build_context_bundle", return_value={"report": mock_report}):
            companion._execute(123, "owner", "repo", 42, title, "author", files)
        return companion

    def test_trivial_depth_posts_tier1_only(self, mock_ollama):
        """Docs-only tiny change (<10 LOC, no logic) → Tier 1 posted, NO enrichment PATCH."""
        companion = self._run(
            make_companion(),
            [{"filename": "README.md", "patch": "+hello", "additions": 1, "deletions": 0, "status": "modified"}],
        )
        assert companion.client.post_pr_comment.call_count == 1
        companion.client.update_pr_comment.assert_not_called()
        tier1_body = companion.client.post_pr_comment.call_args[0][4]
        assert "no LLM enrichment needed" in tier1_body
        assert "🔍 enrichment in progress" not in tier1_body

    def test_inline_only_depth_enriches(self, mock_ollama):
        """Single-file small logic change (<50 LOC) → Tier 1 + Tier 2 enrichment."""
        companion = self._run(
            make_companion(),
            [{"filename": "src/util.py", "patch": "+def f(): pass", "additions": 1, "deletions": 0, "status": "modified"}],
        )
        companion.client.update_pr_comment.assert_called_once()
        tier1_body = companion.client.post_pr_comment.call_args[0][4]
        assert "🔍 enrichment in progress" in tier1_body

    def test_standard_depth_enriches(self, mock_ollama):
        """Multi-file change (>=2 files, >=50 LOC) → full two-tier flow."""
        files = [
            {"filename": f"src/mod{i}.py", "patch": f"+x{i} = 1", "additions": 5, "deletions": 0, "status": "modified"}
            for i in range(3)
        ]
        companion = self._run(make_companion(), files)
        companion.client.update_pr_comment.assert_called_once()

    def test_depth_attached_to_instance(self, mock_ollama):
        """_depth reflects the classified ReviewDepth value."""
        companion = self._run(
            make_companion(),
            [{"filename": "README.md", "patch": "+hello", "additions": 1, "deletions": 0, "status": "modified"}],
        )
        assert companion._depth == "trivial"


# ── Timing metric (webhook received → comment posted) ────────────────────────


class TestTimingMetric:
    """Tests for the deterministic-analysis timing metric."""

    def test_timing_present_in_tier1_output(self, mock_ollama):
        """When webhook_received_at is provided, Tier 1 output includes timing."""
        import time as _time
        companion = make_companion()
        companion.enable_deterministic = True
        companion.enable_graphify = False
        companion._get_last_sha = MagicMock(return_value=None)
        companion.client.post_pr_comment = MagicMock(return_value={"id": 999})
        companion.client.update_pr_comment = MagicMock(return_value={"id": 999})

        mock_report = MagicMock()
        mock_report.has_actionable = True
        mock_report.verdict = "review"
        mock_report.summary = "Security risk detected"
        mock_report.findings = [
            MagicMock(severity="critical", message="Hardcoded secret", file="auth.py", category="security")
        ]

        # Pass webhook_received_at — this is what the webhook handler sends
        received_at = _time.time() - 2.5  # 2.5 seconds ago
        with patch("riptide.companion.build_context_bundle", return_value={"report": mock_report}):
            companion._execute(
                123, "owner", "repo", 42,
                "feat: add auth", "author",
                [{"filename": "src/auth.py", "patch": "+secret = 'hardcoded'", "additions": 1, "deletions": 0, "status": "modified"}],
                webhook_received_at=received_at,
            )

        # Tier 1 body must contain the timing metric
        tier1_body = companion.client.post_pr_comment.call_args[0][4]
        assert "⏱️ Review posted in" in tier1_body
        # Should be around 2.5s
        # Should be around 2.5s - use a tolerance check instead of exact
        # string matching ("2." breaks if elapsed rounds to "1.9s" or the
        # test runs slowly and reports "3.1s").
        match = re.search(r"(\d+\.\d+)(?:ms|s|m|h)", tier1_body)
        assert match, f"timing not found in body: {tier1_body!r}"
        elapsed = float(match.group(1))
        assert elapsed >= 2.0, f"elapsed {elapsed}s too low (expected ~2.5s)"

    def test_timing_present_in_enriched_output(self, mock_ollama):
        """Enriched (Tier 2) output also includes timing."""
        import time as _time
        companion = make_companion()
        companion.enable_deterministic = True
        companion.enable_graphify = False
        companion._get_last_sha = MagicMock(return_value=None)
        companion.client.post_pr_comment = MagicMock(return_value={"id": 999})
        companion.client.update_pr_comment = MagicMock(return_value={"id": 999})

        mock_report = MagicMock()
        mock_report.has_actionable = True
        mock_report.verdict = "review"
        mock_report.summary = "Security risk detected"
        mock_report.findings = [
            MagicMock(severity="critical", message="Hardcoded secret", file="auth.py", category="security")
        ]

        received_at = _time.time() - 1.5
        with patch("riptide.companion.build_context_bundle", return_value={"report": mock_report}):
            companion._execute(
                123, "owner", "repo", 42,
                "feat: add auth", "author",
                [{"filename": "src/auth.py", "patch": "+secret = 'hardcoded'", "additions": 1, "deletions": 0, "status": "modified"}],
                webhook_received_at=received_at,
            )

        # Enriched body must contain the timing metric
        enriched_body = companion.client.update_pr_comment.call_args[0][4]
        assert "⏱️ Review posted in" in enriched_body

    def test_timing_absent_without_webhook_received_at(self, mock_ollama):
        """When webhook_received_at is None, no timing metric is shown."""
        companion = make_companion()
        companion.enable_deterministic = True
        companion.enable_graphify = False
        companion._get_last_sha = MagicMock(return_value=None)
        companion.client.post_pr_comment = MagicMock(return_value={"id": 999})
        companion.client.update_pr_comment = MagicMock(return_value={"id": 999})

        mock_report = MagicMock()
        mock_report.has_actionable = True
        mock_report.verdict = "review"
        mock_report.summary = "Security risk detected"
        mock_report.findings = [
            MagicMock(severity="critical", message="Hardcoded secret", file="auth.py", category="security")
        ]

        # No webhook_received_at (e.g., poller path)
        with patch("riptide.companion.build_context_bundle", return_value={"report": mock_report}):
            companion._execute(
                123, "owner", "repo", 42,
                "feat: add auth", "author",
                [{"filename": "src/auth.py", "patch": "+secret = 'hardcoded'", "additions": 1, "deletions": 0, "status": "modified"}],
            )

        tier1_body = companion.client.post_pr_comment.call_args[0][4]
        assert "⏱️" not in tier1_body


# ── Semaphore + heal probe placement ─────────────────────────────────────────


class TestHealProbePlacement:
    """Verify Ollama heal probe runs inside _execute (after semaphore acquire),
    not in run_for_pr (before semaphore). This prevents the webhook thread
    from blocking on heal while other PRs wait for the semaphore.
    """

    def test_heal_probe_runs_inside_execute(self, mock_ollama):
        """_execute must call ollama_heal after acquiring the semaphore."""
        companion = make_companion()
        companion.enable_deterministic = False
        companion.enable_graphify = False
        companion._get_last_sha = MagicMock(return_value=None)
        companion.client.post_pr_comment = MagicMock(return_value={"id": 999})
        companion.client.update_pr_comment = MagicMock(return_value={"id": 999})

        with patch("riptide.ollama_heal.heal", return_value=0) as mock_heal:
            companion._execute(
                123, "owner", "repo", 42,
                "feat: change", "author",
                [{"filename": "src/main.py", "patch": "+x = 1", "additions": 1, "deletions": 0, "status": "modified"}]
            )
            mock_heal.assert_called_once_with(base_url=companion.ollama_base)

    def test_run_for_pr_skips_when_semaphore_held(self):
        """When semaphore is already held, run_for_pr returns immediately
        without calling _execute or ollama_heal."""
        companion = make_companion()
        # Hold the semaphore
        companion._semaphore.acquire()

        with patch.object(companion, "_execute") as mock_execute, \
             patch("riptide.ollama_heal.heal") as mock_heal:
            companion.run_for_pr(123, "owner", "repo", 42, "title", "author", [])
            mock_execute.assert_not_called()
            mock_heal.assert_not_called()

        companion._semaphore.release()

    def test_run_for_pr_acquires_semaphore_before_execute(self):
        """run_for_pr must acquire semaphore before calling _execute."""
        companion = make_companion()
        companion.enable_deterministic = False
        companion.enable_graphify = False
        companion._get_last_sha = MagicMock(return_value=None)
        companion.client.post_pr_comment = MagicMock(return_value={"id": 999})
        companion.client.update_pr_comment = MagicMock(return_value={"id": 999})

        acquire_order = []
        original_acquire = companion._semaphore.acquire
        def track_acquire(*args, **kwargs):
            acquire_order.append("acquire")
            return original_acquire(*args, **kwargs)

        with patch.object(companion._semaphore, "acquire", side_effect=track_acquire), \
             patch.object(companion, "_execute", side_effect=lambda *a, **k: acquire_order.append("execute")), \
             patch("riptide.ollama_heal.heal", return_value=0):
            companion.run_for_pr(123, "owner", "repo", 42, "title", "author", [])

        assert acquire_order == ["acquire", "execute"]


class TestBuildTier1BodyFooter:
    """Tests for Companion._build_tier1_body checkbox footer behavior.

    Verifies single footer rendering and ProofShot conditional inclusion.
    These are separate from the ui_files parameter tests in test_route1_fallback.py.
    """

    def test_single_checkbox_footer_no_duplication(self):
        """The checkbox footer should appear exactly once in the body.

        Regression test: _build_tier1_body previously had a duplicate
        checkbox block at the end that re-rendered without ui_files.
        """
        from riptide.companion import Companion
        from riptide.diff_analyzer import DiffReport

        companion = Companion.__new__(Companion)
        companion.model = "test"
        companion.client = MagicMock()

        report = DiffReport(
            verdict="pass",
            summary="Test summary",
            findings=[],
            stats={"files": 1, "additions": 1, "deletions": 0},
        )

        body = companion._build_tier1_body(
            emoji="✨",
            author="testuser",
            tldr="Test TL;DR",
            deterministic_report=report,
            depth="trivial",
        )

        # Count checkbox footer occurrences — should be exactly 3
        checkbox_count = body.count("- [ ]")
        assert checkbox_count == 3, f"Expected 3 checkbox items (review/fix/relabel), found {checkbox_count}"

    def test_checkbox_footer_includes_proofshot_when_ui_files(self):
        """When UI files are changed, ProofShot action should be included."""
        from riptide.companion import Companion
        from riptide.diff_analyzer import DiffReport

        companion = Companion.__new__(Companion)
        companion.model = "test"
        companion.client = MagicMock()

        report = DiffReport(
            verdict="pass",
            summary="Test summary",
            findings=[],
            stats={"files": 1, "additions": 1, "deletions": 0},
        )

        body = companion._build_tier1_body(
            emoji="✨",
            author="testuser",
            tldr="Test TL;DR",
            deterministic_report=report,
            depth="trivial",
            ui_files=["src/ui/App.tsx"],
        )

        assert "ProofShot" in body

    def test_checkbox_footer_excludes_proofshot_without_ui_files(self):
        """When no UI files are changed, ProofShot action should not be included."""
        from riptide.companion import Companion
        from riptide.diff_analyzer import DiffReport

        companion = Companion.__new__(Companion)
        companion.model = "test"
        companion.client = MagicMock()

        report = DiffReport(
            verdict="pass",
            summary="Test summary",
            findings=[],
            stats={"files": 1, "additions": 1, "deletions": 0},
        )

        body = companion._build_tier1_body(
            emoji="✨",
            author="testuser",
            tldr="Test TL;DR",
            deterministic_report=report,
            depth="trivial",
            ui_files=None,
        )

        assert "ProofShot" not in body
