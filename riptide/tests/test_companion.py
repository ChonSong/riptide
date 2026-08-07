# riptide/tests/test_companion.py
"""
Tests for Riptide Companion bot (Bot 1).
Covers PR classification, UI detection, TL;DR generation, and Ollama edge cases.
"""

import os
import re
import time
import threading
from unittest.mock import patch, MagicMock

import pytest

from riptide.companion import Companion, classify_pr_mood


# ── Helpers ──────────────────────────────────────────────────────────────────


def make_companion(tmp_path=None):
    """Create a Companion instance with mocked github client and disabled warm-up."""
    client = MagicMock()
    with patch("threading.Thread"):
        companion = Companion(client)
    if tmp_path:
        companion._alert_file = tmp_path / "companion_alerts.json"
        companion._alert_lock = threading.Lock()
    return companion


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

        # Patch the analyzer to return a report with no findings
        mock_report = MagicMock()
        mock_report.has_actionable = False
        mock_report.findings = []
        mock_report.verdict = "pass"
        with patch.object(companion._analyzer, "analyze", return_value=mock_report):
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
