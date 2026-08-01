# riptide/tests/test_gif_selection.py
"""
Tests for Companion GIF selection logic (PR #7).
Covers select_gif determinism, curated pool, Giphy API fallback, and classify enhancements.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from riptide.companion import (
    select_gif,
    _search_giphy,
    _emoji_to_giphy_tag,
    classify_pr_mood,
    GIF_POOL,
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
        for i in range(5):
            urls.add(select_gif("✨", f"feat: feature {i}", [{"filename": f"f{i}.tsx"}]))
        # Pool has 3 entries; across 5 distinct titles we expect variety
        assert len(urls) >= 2

    def test_default_emoji_pool(self):
        url = select_gif("🤷", "weird title", [{"filename": "a.py"}])
        assert url.startswith("https://media.giphy.com/media/")

    def test_no_title_falls_back_to_first_pool_entry(self):
        url = select_gif("✨", "", [])
        first = GIF_POOL["✨"][0][0]
        assert first in url

    def test_giphy_api_used_when_key_present(self):
        with patch.dict("os.environ", {"GIPHY_API_KEY": "test-key"}), \
             patch("riptide.companion._search_giphy", return_value="https://api.giphy.com/gif.mp4") as mock_search:
            url = select_gif("✨", "feat: shiny", [{"filename": "a.tsx"}])
            assert url == "https://api.giphy.com/gif.mp4"
            mock_search.assert_called_once()

    def test_giphy_api_failure_falls_back_to_pool(self):
        with patch.dict("os.environ", {"GIPHY_API_KEY": "bad-key"}), \
             patch("riptide.companion._search_giphy", return_value=None):
            url = select_gif("✨", "feat: shiny", [{"filename": "a.tsx"}])
            assert url.startswith("https://media.giphy.com/media/")

    def test_giphy_api_exception_falls_back_to_pool(self):
        with patch.dict("os.environ", {"GIPHY_API_KEY": "bad-key"}), \
             patch("riptide.companion._search_giphy", side_effect=RuntimeError("boom")):
            url = select_gif("✨", "feat: shiny", [{"filename": "a.tsx"}])
            assert url.startswith("https://media.giphy.com/media/")


class TestEmojiToGiphyTag:
    def test_known_emoji(self):
        assert _emoji_to_giphy_tag("✨") == "sparkle celebration"
        assert _emoji_to_giphy_tag("🐛") == "bug fix"

    def test_unknown_emoji_default(self):
        assert _emoji_to_giphy_tag("🤷") == "reaction"


class TestSearchGiphy:
    def _mock_resp(self, payload):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode()
        mock_resp.__enter__.return_value = mock_resp  # with-statement support
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
