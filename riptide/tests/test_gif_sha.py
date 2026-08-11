#!/usr/bin/env python3
"""Tests for SHA-aware GIF selection (re-sync safe)."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from riptide.companion import select_gif


class TestShaAwareGifSelection:
    """Test that GIF selection is deterministic per SHA (re-sync safe)."""

    def test_same_sha_same_gif(self):
        """Same SHA must always produce the same GIF."""
        candidates = [
            {"url": "https://example.com/1.gif", "title": "a", "source": "kilpy"},
            {"url": "https://example.com/2.gif", "title": "b", "source": "kilpy"},
            {"url": "https://example.com/3.gif", "title": "c", "source": "kilpy"},
        ]
        with patch("riptide.companion._fetch_gif_candidates", return_value=candidates), \
             patch("riptide.companion._score_gifs_with_llm", return_value=None):
            url1 = select_gif("✨", "feat: test", [{"filename": "a.tsx"}], sha="abc123")
            url2 = select_gif("✨", "feat: test", [{"filename": "a.tsx"}], sha="abc123")
            url3 = select_gif("✨", "feat: test", [{"filename": "a.tsx"}], sha="abc123")
        assert url1 == url2 == url3

    def test_different_sha_different_gif(self):
        """Different SHAs should (likely) produce different GIFs."""
        candidates = [
            {"url": "https://example.com/1.gif", "title": "a", "source": "kilpy"},
            {"url": "https://example.com/2.gif", "title": "b", "source": "kilpy"},
            {"url": "https://example.com/3.gif", "title": "c", "source": "kilpy"},
        ]
        urls = set()
        for sha in ["aaa", "bbb", "ccc", "ddd", "eee"]:
            with patch("riptide.companion._fetch_gif_candidates", return_value=candidates), \
                 patch("riptide.companion._score_gifs_with_llm", return_value=None):
                url = select_gif("✨", "feat: test", [{"filename": "a.tsx"}], sha=sha)
                urls.add(url)
        # At least 2 different GIFs across 5 SHAs (deterministic but varied)
        assert len(urls) >= 2

    def test_no_sha_uses_tag_seed(self):
        """Without SHA, falls back to tag-based seed."""
        candidates = [
            {"url": "https://example.com/1.gif", "title": "a", "source": "kilpy"},
            {"url": "https://example.com/2.gif", "title": "b", "source": "kilpy"},
        ]
        with patch("riptide.companion._fetch_gif_candidates", return_value=candidates), \
             patch("riptide.companion._score_gifs_with_llm", return_value=None):
            url1 = select_gif("✨", "feat: test", [{"filename": "a.tsx"}])
            url2 = select_gif("✨", "feat: test", [{"filename": "a.tsx"}])
        # Same title → same tag → same seed → same GIF (deterministic)
        assert url1 == url2
