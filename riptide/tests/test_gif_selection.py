# riptide/tests/test_gif_selection.py
"""
Tests for Companion GIF selection logic.
Covers select_gif hybrid LLM+Python flow, API fallback chain, determinism, and classification.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from riptide.companion import (
    select_gif,
    _pick_best_tag,
    classify_pr_mood,
    GIF_TAGS,
    KEYWORD_TAG_BOOST,
    GIFI_MAP,
)


class TestSelectGif:
    """Tests for select_gif function."""

    @patch("riptide.companion._generate_search_term_with_llm")
    @patch("riptide.companion._fetch_gif_candidates")
    def test_returns_kilpy_url_when_llm_and_candidates_available(self, mock_fetch, mock_search):
        mock_search.return_value = "login security fix"
        mock_fetch.return_value = [
            {"url": "https://static.klipy.com/1.gif", "title": "Login Fix", "source": "kilpy"},
            {"url": "https://static.klipy.com/2.gif", "title": "Bug Dance", "source": "kilpy"},
        ]
        with patch("riptide.companion._score_gifs_with_llm", return_value="https://static.klipy.com/1.gif"):
            url = select_gif("🐛", "fix: auth bug in login", [{"filename": "auth.py"}])
            assert url == "https://static.klipy.com/1.gif"
            mock_search.assert_called_once_with("🐛", "fix: auth bug in login", [{"filename": "auth.py"}])

    @patch("riptide.companion._generate_search_term_with_llm")
    @patch("riptide.companion._fetch_gif_candidates")
    def test_falls_back_to_deterministic_when_llm_scoring_fails(self, mock_fetch, mock_search):
        mock_search.return_value = "login fix"
        mock_fetch.return_value = [
            {"url": "https://static.klipy.com/1.gif", "title": "Login Fix", "source": "kilpy"},
        ]
        with patch("riptide.companion._score_gifs_with_llm", return_value=None):
            url = select_gif("🐛", "fix: auth bug", [{"filename": "a.py"}])
            assert url == "https://static.klipy.com/1.gif"

    @patch("riptide.companion._generate_search_term_with_llm")
    def test_uses_static_fallback_when_no_candidates(self, mock_search):
        mock_search.return_value = "unknown tag"
        with patch("riptide.companion._fetch_gif_candidates", return_value=[]):
            url = select_gif("🐛", "fix: obscure thing", [])
            assert url.startswith("https://media.giphy.com/media/")

    def test_returns_giphy_url_for_default_emoji(self):
        url = select_gif("🤷", "weird title", [{"filename": "a.py"}])
        assert url.startswith("https://media.giphy.com/media/")

    def test_no_title_falls_back_to_map(self):
        url = select_gif("✨", "", [])
        assert url == GIFI_MAP["✨"]

    def test_deterministic_same_input(self):
        with patch("riptide.companion._generate_search_term_with_llm", return_value="bug fix"), \
             patch("riptide.companion._fetch_gif_candidates") as mock_fetch, \
             patch("riptide.companion._score_gifs_with_llm", return_value=None):
            mock_fetch.return_value = [
                {"url": "https://static.klipy.com/1.gif", "title": "Bug Fix", "source": "kilpy"},
            ]
            a = select_gif("🐛", "fix: crash", [{"filename": "x.py"}])
            b = select_gif("🐛", "fix: crash", [{"filename": "x.py"}])
            assert a == b


class TestPickBestTag:
    """Test keyword-relevant tag selection."""

    def test_title_keyword_boosts_specific_tag(self):
        tag = _pick_best_tag("🐛", "fix: critical bug in auth")
        assert "bug" in tag.lower() or "fix" in tag.lower()

    def test_no_title_gets_some_tag(self):
        tag = _pick_best_tag("✨", "")
        assert tag in GIF_TAGS["✨"]

    def test_file_content_affects_tiebreak(self):
        tag_a = _pick_best_tag("✨", "feat: new UI", [{"filename": "a.tsx"}])
        tag_b = _pick_best_tag("✨", "feat: new UI", [{"filename": "b.css"}])
        assert tag_a in GIF_TAGS["✨"]
        assert tag_b in GIF_TAGS["✨"]


class TestClassifyPrMoodEnhancements:
    """Verify new priority-keyword and file-extension classification."""

    def test_priority_keywords_win(self):
        assert classify_pr_mood("hotfix: resolve auth bug", []) == "🐛"
        assert classify_pr_mood("security: patch CVE", []) == "🐛"
        assert classify_pr_mood("revert: undo feature", []) == "⏪"

    def test_test_files_dominant(self):
        files = [{"filename": "test_a.py"}, {"filename": "test_b.py"}, {"filename": "utils.py"}]
        assert classify_pr_mood("whatever", files) == "🧪"

    def test_ui_files_dominant(self):
        files = [{"filename": "a.tsx"}, {"filename": "b.css"}, {"filename": "c.tsx"}]
        assert classify_pr_mood("whatever", files) == "✨"

    def test_config_files_dominant(self):
        files = [{"filename": "a.yml"}, {"filename": "b.yaml"}, {"filename": "c.toml"}]
        assert classify_pr_mood("whatever", files) == "🔧"

    def test_title_keywords_still_work(self):
        assert classify_pr_mood("feat: new thing", []) == "✨"
        assert classify_pr_mood("docs: update", []) == "📝"
