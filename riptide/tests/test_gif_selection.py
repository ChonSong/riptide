# riptide/tests/test_gif_selection.py
"""
Tests for Companion GIF selection logic.
Covers select_gif relevance scoring, API fallback chain, determinism, and classify enhancements.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from riptide.companion import (
    select_gif,
    _search_giphy,
    _search_tenor,
    _pick_best_tag,
    classify_pr_mood,
    GIF_TAGS,
    KEYWORD_TAG_BOOST,
    GIFI_MAP,
)


class TestSelectGif:
    """Tests for select_gif function."""

    def test_returns_valid_giphy_url(self):
        url = select_gif("✨", "feat: add button", [{"filename": "a.tsx"}])
        assert url.startswith("https://media.giphy.com/media/")
        assert url.endswith("/giphy.gif")

    def test_deterministic_same_input(self):
        a = select_gif("🐛", "fix: crash", [{"filename": "x.py"}])
        b = select_gif("🐛", "fix: crash", [{"filename": "x.py"}])
        assert a == b

    def test_variety_across_different_prs(self):
        """Different PR content should not always produce the same GIF."""
        urls = set()
        for i in range(8):
            urls.add(select_gif("✨", f"feat: feature {i}", [{"filename": f"f{i}.tsx"}]))
        # Pool has 4 entries; across 8 distinct titles we expect variety
        assert len(urls) >= 2

    def test_default_emoji_pool(self):
        url = select_gif("🤷", "weird title", [{"filename": "a.py"}])
        assert url.startswith("https://media.giphy.com/media/")

    def test_no_title_falls_back_to_map(self):
        url = select_gif("✨", "", [])
        assert url == GIFI_MAP["✨"]

    def test_giphy_api_used_when_key_present(self):
        with patch.dict("os.environ", {"GIPHY_API_KEY": "test-key"}), \
             patch("riptide.companion._search_giphy", return_value="https://api.giphy.com/gif.mp4") as mock_search:
            url = select_gif("✨", "feat: shiny", [{"filename": "a.tsx"}])
            assert url == "https://api.giphy.com/gif.mp4"
            mock_search.assert_called_once()

    def test_giphy_api_failure_falls_back_to_tenor(self):
        with patch.dict("os.environ", {"GIPHY_API_KEY": "bad-key", "TENOR_API_KEY": "tenor-key"}), \
             patch("riptide.companion._search_giphy", return_value=None), \
             patch("riptide.companion._search_tenor", return_value="https://tenor.com/gif.gif") as mock_tenor:
            url = select_gif("✨", "feat: shiny", [{"filename": "a.tsx"}])
            assert url == "https://tenor.com/gif.gif"
            mock_tenor.assert_called_once()

    def test_giphy_tenor_fail_falls_back_to_static(self):
        with patch.dict("os.environ", {"GIPHY_API_KEY": "bad-key", "TENOR_API_KEY": "bad-key"}), \
             patch("riptide.companion._search_giphy", return_value=None), \
             patch("riptide.companion._search_tenor", return_value=None):
            url = select_gif("✨", "feat: shiny", [{"filename": "a.tsx"}])
            assert url.startswith("https://media.giphy.com/media/")

    def test_giphy_api_exception_falls_back_to_static(self):
        with patch.dict("os.environ", {"GIPHY_API_KEY": "bad-key"}), \
             patch("riptide.companion._search_giphy", side_effect=RuntimeError("boom")):
            url = select_gif("✨", "feat: shiny", [{"filename": "a.tsx"}])
            assert url.startswith("https://media.giphy.com/media/")


class TestPickBestTag:
    """Test keyword-relevant tag selection."""

    def test_title_keyword_boosts_specific_tag(self):
        # "bug fix" tag contains "bug" and "fix" — both in title
        tag = _pick_best_tag("🐛", "fix: critical bug in auth")
        assert "bug" in tag.lower() or "fix" in tag.lower()

    def test_no_title_gets_some_tag(self):
        tag = _pick_best_tag("✨", "")
        assert tag in GIF_TAGS["✨"]

    def test_file_content_affects_tiebreak(self):
        """Same emoji + same title prefix but different files → may differ."""
        tag_a = _pick_best_tag("✨", "feat: new UI", [{"filename": "a.tsx"}])
        tag_b = _pick_best_tag("✨", "feat: new UI", [{"filename": "b.css"}])
        # Both valid tags; file content affects hash tiebreak
        assert tag_a in GIF_TAGS["✨"]
        assert tag_b in GIF_TAGS["✨"]


class TestSearchGiphy:
    def _mock_resp(self, payload):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode()
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    def test_returns_url_on_success(self):
        mock_resp = self._mock_resp({
            "data": [
                {"images": {"fixed_height": {"url": "https://gif1.com/1.gif"}}},
                {"images": {"fixed_height": {"url": "https://gif2.com/2.gif"}}},
            ]
        })
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            url = _search_giphy("bug fix", "key")
            assert url.startswith("https://gif")
            mock_open.assert_called_once()

    def test_returns_none_on_empty_results(self):
        mock_resp = self._mock_resp({"data": []})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert _search_giphy("bug fix", "key") is None

    def test_deterministic_across_calls(self):
        mock_resp = self._mock_resp({
            "data": [{"images": {"fixed_height": {"url": f"https://gif{i}.com/{i}.gif"}}} for i in range(5)]
        })
        with patch("urllib.request.urlopen", return_value=mock_resp):
            a = _search_giphy("bug fix", "key")
            b = _search_giphy("bug fix", "key")
            assert a == b


class TestSearchTenor:
    def _mock_resp(self, payload):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode()
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    def test_returns_url_on_success(self):
        mock_resp = self._mock_resp({
            "results": [
                {"media_formats": {"gif": {"url": "https://tenor.com/1.gif"}}},
                {"media_formats": {"gif": {"url": "https://tenor.com/2.gif"}}},
            ]
        })
        with patch("urllib.request.urlopen", return_value=mock_resp):
            url = _search_tenor("bug fix", "key")
            assert url is not None
            assert url.startswith("https://tenor.com/")

    def test_returns_none_on_empty_results(self):
        mock_resp = self._mock_resp({"results": []})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert _search_tenor("bug fix", "key") is None

    def test_prefers_gif_over_mp4(self):
        mock_resp = self._mock_resp({
            "results": [
                {"media_formats": {"mp4": {"url": "https://v.com/v.mp4"}, "gif": {"url": "https://i.com/i.gif"}}},
            ]
        })
        with patch("urllib.request.urlopen", return_value=mock_resp):
            url = _search_tenor("test", "key")
            assert url is not None and "gif" in url


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
