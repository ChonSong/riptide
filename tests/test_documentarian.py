#!/usr/bin/env python3
"""Tests for documentarian module."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from riptide.documentarian import (
    on_merge,
    update_graphify,
    generate_changelog_entry,
    update_review_profile,
    get_review_profile,
    _classify_pr_title,
    _insert_changelog_entry,
    _get_db,
    DEFAULT_CHANGELOG_PATH,
)


@pytest.fixture(autouse=True)
def reset_thread_local():
    """Reset thread-local DB connection between tests."""
    import riptide.documentarian as doc
    doc._local = type(doc._local)()
    yield
    if hasattr(doc._local, "conn") and doc._local.conn:
        doc._local.conn.close()
        doc._local.conn = None


@pytest.fixture
def tmp_db_path(tmp_path):
    """Create a temporary DB path."""
    return str(tmp_path / "test_doc.db")


@pytest.fixture
def tmp_changelog(tmp_path):
    """Create a temporary changelog file."""
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## [Unreleased]\n\n### Added\n- Initial release\n", encoding="utf-8")
    return changelog


class TestClassifyPrTitle:
    """Tests for _classify_pr_title."""

    def test_feat_classifies_as_added(self):
        assert _classify_pr_title("feat(scope): new feature") == "Added"

    def test_fix_classifies_as_fixed(self):
        assert _classify_pr_title("fix: something broke") == "Fixed"

    def test_hotfix_classifies_as_fixed(self):
        assert _classify_pr_title("hotfix: critical bug") == "Fixed"

    def test_refactor_classifies_as_changed(self):
        assert _classify_pr_title("refactor: simplify") == "Changed"

    def test_perf_classifies_as_changed(self):
        assert _classify_pr_title("perf: speed up") == "Changed"

    def test_docs_classifies_as_changed(self):
        assert _classify_pr_title("docs: update readme") == "Changed"

    def test_chore_classifies_as_changed(self):
        assert _classify_pr_title("chore: clean up") == "Changed"

    def test_unknown_classifies_as_added(self):
        assert _classify_pr_title("Add new feature") == "Added"


class TestInsertChangelogEntry:
    """Tests for _insert_changelog_entry."""

    def test_inserts_into_existing_section(self):
        content = "# Changelog\n\n## [Unreleased]\n\n### Added\n- Existing entry\n"
        result = _insert_changelog_entry(content, "Added", "- New entry")
        assert "- Existing entry" in result
        assert "- New entry" in result

    def test_creates_missing_section(self):
        content = "# Changelog\n\n## [Unreleased]\n"
        result = _insert_changelog_entry(content, "Fixed", "- Bug fix")
        assert "### Fixed" in result
        assert "- Bug fix" in result

    def test_creates_unreleased_if_missing(self):
        content = "# Changelog\n"
        result = _insert_changelog_entry(content, "Added", "- New thing")
        assert "## [Unreleased]" in result
        assert "### Added" in result
        assert "- New thing" in result

    def test_handles_multiple_sections(self):
        content = "# Changelog\n\n## [Unreleased]\n\n### Added\n- Old\n\n## [0.1.0]\n- Release\n"
        result = _insert_changelog_entry(content, "Added", "- New")
        assert "- New" in result
        assert "## [0.1.0]" in result  # Preserve existing section


class TestUpdateGraphify:
    """Tests for update_graphify."""

    def test_success_returns_true(self):
        with patch("riptide.documentarian.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            assert update_graphify("abc123") is True

    def test_failure_returns_false(self):
        with patch("riptide.documentarian.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="error")
            assert update_graphify("abc123") is False

    def test_missing_command_returns_false(self):
        with patch("riptide.documentarian.subprocess.run", side_effect=FileNotFoundError):
            assert update_graphify("abc123") is False

    def test_timeout_returns_false(self):
        import subprocess
        with patch("riptide.documentarian.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="graphify", timeout=120)):
            assert update_graphify("abc123") is False


class TestGenerateChangelogEntry:
    """Tests for generate_changelog_entry."""

    def test_writes_entry_to_file(self, tmp_path):
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text("# Changelog\n\n## [Unreleased]\n\n### Added\n- Initial\n", encoding="utf-8")
        
        with patch("riptide.documentarian.DEFAULT_CHANGELOG_PATH", changelog):
            generate_changelog_entry("owner", "repo", 42, "feat: new feature", "body text")
        
        content = changelog.read_text()
        assert "#42" in content
        assert "feat: new feature" in content

    def test_creates_file_if_missing(self, tmp_path):
        changelog = tmp_path / "CHANGELOG.md"
        
        with patch("riptide.documentarian.DEFAULT_CHANGELOG_PATH", changelog):
            generate_changelog_entry("owner", "repo", 99, "fix: bug fix")
        
        content = changelog.read_text()
        assert "## [Unreleased]" in content
        assert "### Fixed" in content
        assert "#99" in content

    def test_includes_findings(self, tmp_path):
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")
        
        with patch("riptide.documentarian.DEFAULT_CHANGELOG_PATH", changelog):
            generate_changelog_entry(
                "owner", "repo", 5, "feat: x",
                findings=["Finding A", "Finding B"]
            )
        
        content = changelog.read_text()
        assert "Finding A" in content
        assert "Finding B" in content

    def test_caps_findings_at_3(self, tmp_path):
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")
        
        with patch("riptide.documentarian.DEFAULT_CHANGELOG_PATH", changelog):
            generate_changelog_entry(
                "owner", "repo", 5, "feat: x",
                findings=["F1", "F2", "F3", "F4", "F5"]
            )
        
        content = changelog.read_text()
        assert "F1" in content
        assert "F3" in content
        assert "F4" not in content
        assert "F5" not in content


class TestUpdateReviewProfile:
    """Tests for update_review_profile."""

    def test_creates_new_profile(self, tmp_db_path):
        update_review_profile("owner", "repo", db_path=tmp_db_path)
        profile = get_review_profile("owner", "repo", db_path=tmp_db_path)
        assert profile is not None
        assert profile["repo_full_name"] == "owner/repo"
        assert profile["merge_count"] == 1

    def test_increments_merge_count(self, tmp_db_path):
        update_review_profile("owner", "repo", db_path=tmp_db_path)
        update_review_profile("owner", "repo", db_path=tmp_db_path)
        profile = get_review_profile("owner", "repo", db_path=tmp_db_path)
        assert profile["merge_count"] == 2

    def test_updates_timestamp(self, tmp_db_path):
        update_review_profile("owner", "repo", db_path=tmp_db_path)
        profile = get_review_profile("owner", "repo", db_path=tmp_db_path)
        assert profile["last_merge_at"] is not None
        # Verify it's a valid ISO timestamp
        datetime.fromisoformat(profile["last_merge_at"])

    def test_get_nonexistent_returns_none(self, tmp_db_path):
        profile = get_review_profile("nonexistent", "repo", db_path=tmp_db_path)
        assert profile is None


class TestOnMerge:
    """Tests for on_merge entry point."""

    def test_calls_graphify_and_changelog(self, tmp_path, tmp_db_path):
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")
        
        with patch("riptide.documentarian.update_graphify", return_value=True) as mock_graphify, \
             patch("riptide.documentarian.generate_changelog_entry") as mock_changelog, \
             patch("riptide.documentarian.update_review_profile") as mock_profile:
            on_merge("owner", "repo", 42, "feat: test", "body")
            mock_graphify.assert_called_once_with("")
            mock_changelog.assert_called_once_with("owner", "repo", 42, "feat: test", "body")
            mock_profile.assert_called_once_with("owner", "repo")

    def test_graphify_failure_doesnt_crash(self, tmp_path, tmp_db_path):
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")
        
        with patch("riptide.documentarian.update_graphify", return_value=False), \
             patch("riptide.documentarian.generate_changelog_entry") as mock_changelog, \
             patch("riptide.documentarian.update_review_profile") as mock_profile:
            on_merge("owner", "repo", 1, "feat: test")
            # Should still proceed to changelog and profile
            mock_changelog.assert_called_once()
            mock_profile.assert_called_once()

    def test_handles_empty_pr_body(self, tmp_path, tmp_db_path):
        with patch("riptide.documentarian.update_graphify", return_value=True), \
             patch("riptide.documentarian.generate_changelog_entry") as mock_changelog, \
             patch("riptide.documentarian.update_review_profile"):
            on_merge("owner", "repo", 1, "feat: test", "")
            mock_changelog.assert_called_once_with("owner", "repo", 1, "feat: test", "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])