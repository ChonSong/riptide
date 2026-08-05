#!/usr/bin/env python3
"""Tests for riptide.labeler — the labeling engine."""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure we test against the real taxonomy
import riptide.labeler as labeler_module
from riptide.labeler import Labeler, LLMVoter


@pytest.fixture
def labeler(tmp_path):
    """Create a Labeler with a minimal taxonomy for testing."""
    definitions = {
        "version": "1.0.0",
        "shared_labels": {
            "dimensions": {
                "type": {
                    "labels": {
                        "type/bug": {"description": "Bug", "color": "#C5DEF5"},
                        "type/feature": {"description": "Feature", "color": "#C5DEF5"},
                        "type/docs": {"description": "Docs", "color": "#C5DEF5"},
                        "type/refactor": {"description": "Refactor", "color": "#C5DEF5"},
                        "type/security": {"description": "Security", "color": "#C5DEF5"},
                        "type/deps": {"description": "Deps", "color": "#C5DEF5"},
                    }
                },
                "priority": {
                    "labels": {
                        "priority/critical": {"description": "Critical", "color": "#B60205"},
                        "priority/high": {"description": "High", "color": "#D93F0B"},
                        "priority/medium": {"description": "Medium", "color": "#FBCA04"},
                        "priority/low": {"description": "Low", "color": "#0E8A16"},
                    }
                },
                "scope": {
                    "labels": {
                        "scope/tiny": {"description": "<10", "color": "#848d97"},
                        "scope/small": {"description": "10-99", "color": "#848d97"},
                        "scope/medium": {"description": "100-499", "color": "#848d97"},
                        "scope/large": {"description": "500-999", "color": "#848d97"},
                        "scope/massive": {"description": "1000+", "color": "#848d97"},
                    }
                },
                "status": {
                    "labels": {
                        "status/draft": {"description": "Draft", "color": "#D876E3"},
                        "status/needs-triage": {"description": "Needs triage", "color": "#D876E3"},
                        "status/needs-repro": {"description": "Needs repro", "color": "#D876E3"},
                        "status/blocked": {"description": "Blocked", "color": "#D876E3"},
                    }
                },
                "bot": {
                    "labels": {
                        "bot/labeled": {"description": "Labeled", "color": "#E99695"},
                    }
                }
            }
        },
        "repo_components": {
            "repos": {
                "ChonSong/riptide": {
                    "comp/companion": {
                        "description": "Companion bot",
                        "color": "#BFD4F2",
                        "paths": ["riptide/companion.py"]
                    },
                    "comp/infra": {
                        "description": "Infrastructure",
                        "color": "#BFD4F2",
                        "paths": ["Dockerfile", "docker-compose.yml"]
                    }
                }
            }
        },
        "classification_rules": {
            "type": [
                {"label": "type/bug", "pattern": "^fix[:\\s]|\\b(bug|bugfix|hotfix|regression)\\b|steps to reproduce"},
                {"label": "type/docs", "pattern": "^docs[:\\s]|\\b(readme|documentation|wiki)\\b"},
                {"label": "type/feature", "pattern": "^feat[:\\s]|\\b(feature|add)\\b"},
                {"label": "type/refactor", "pattern": "^(refactor|chore)[:\\s]|\\b(restructure|reorganize)\\b"},
                {"label": "type/security", "pattern": "\\b(security|vuln|cve|xss|injection|exploit)\\b"},
                {"label": "type/deps", "pattern": "^(deps|depend)[:\\s]|\\b(dependencies|requirements)\\b"},
            ],
            "priority": [
                {"label": "priority/critical", "pattern": "\\b(security|data loss|crash loop|p0|data breach|exploit|rce)\\b"},
                {"label": "priority/high", "pattern": "\\b(crash|down|blocking|broken|regression)\\b"},
                {"label": "priority/medium", "condition": "loc >= 200"},
                {"label": "priority/low"},
            ],
            "scope": [
                {"label": "scope/tiny", "condition": "loc < 10"},
                {"label": "scope/small", "condition": "10 <= loc < 100"},
                {"label": "scope/medium", "condition": "100 <= loc < 500"},
                {"label": "scope/large", "condition": "500 <= loc < 1000"},
                {"label": "scope/massive", "condition": "loc >= 1000"},
            ],
            "status": [
                {"label": "status/draft", "condition": "pr.draft == true"},
                {"label": "status/needs-triage", "condition": "confidence < 0.7"},
                {"label": "status/needs-repro", "pattern": "type/bug", "condition": "no_repro_steps"},
                {"label": "status/blocked", "pattern": "\\b(blocked on|waiting for|depends on)\\b"},
            ]
        },
        "ai_fallback_config": {
            "model": "qwen2.5-coder:7b",
            "endpoint": "http://localhost:43311/api/generate",
            "confidence_threshold": 0.7,
        }
    }
    definitions_path = tmp_path / "label-definitions.json"
    definitions_path.write_text(json.dumps(definitions))
    return Labeler(definitions_path=definitions_path)


class TestLabelerClassification:
    """Test deterministic label classification."""

    def test_bug_from_fix_prefix(self, labeler):
        pr = {"title": "fix: resolve crash on startup", "body": ""}
        files = [{"filename": "riptide/companion.py", "additions": 10, "deletions": 5}]
        labels = labeler.classify_pr(pr, files)
        assert "type/bug" in labels

    def test_bug_from_keyword(self, labeler):
        pr = {"title": "Fix memory leak in session store", "body": "This is a bugfix for the session memory leak"}
        files = [{"filename": "riptide/companion.py", "additions": 20, "deletions": 10}]
        labels = labeler.classify_pr(pr, files)
        assert "type/bug" in labels

    def test_feature_from_feat_prefix(self, labeler):
        pr = {"title": "feat: add labeler engine", "body": ""}
        files = [{"filename": "riptide/labeler.py", "additions": 100, "deletions": 0}]
        labels = labeler.classify_pr(pr, files)
        assert "type/feature" in labels

    def test_docs_from_prefix(self, labeler):
        pr = {"title": "docs: update README with new installation steps", "body": ""}
        files = [{"filename": "README.md", "additions": 30, "deletions": 5}]
        labels = labeler.classify_pr(pr, files)
        assert "type/docs" in labels

    def test_security_from_keyword(self, labeler):
        pr = {"title": "fix: address CVE-2024-1234", "body": "Security vulnerability in auth handler"}
        files = [{"filename": "riptide/webhook.py", "additions": 15, "deletions": 8}]
        labels = labeler.classify_pr(pr, files)
        assert "type/security" in labels
        assert "priority/critical" in labels

    def test_refactor_from_prefix(self, labeler):
        pr = {"title": "refactor: restructure orchestrator dispatch", "body": ""}
        files = [{"filename": "riptide/orchestrator.py", "additions": 50, "deletions": 40}]
        labels = labeler.classify_pr(pr, files)
        assert "type/refactor" in labels

    def test_deps_from_prefix(self, labeler):
        pr = {"title": "deps: update fastapi to 0.115", "body": ""}
        files = [{"filename": "requirements.txt", "additions": 3, "deletions": 3}]
        labels = labeler.classify_pr(pr, files)
        assert "type/deps" in labels

    def test_needs_triage_for_unknown(self, labeler):
        pr = {"title": "Update orchestrator", "body": "Made some changes"}
        files = [{"filename": "riptide/orchestrator.py", "additions": 50, "deletions": 30}]
        labels = labeler.classify_pr(pr, files)
        assert "status/needs-triage" in labels

    def test_needs_repro_for_bug_without_steps(self, labeler):
        pr = {"title": "fix: crash on save", "body": "Saving causes a crash"}
        files = [{"filename": "riptide/companion.py", "additions": 5, "deletions": 2}]
        labels = labeler.classify_pr(pr, files)
        assert "type/bug" in labels
        assert "status/needs-repro" in labels

    def test_no_repro_when_steps_present(self, labeler):
        pr = {"title": "fix: crash on save", "body": "Steps to reproduce:\n1. Click save\n2. Crash"}
        files = [{"filename": "riptide/companion.py", "additions": 5, "deletions": 2}]
        labels = labeler.classify_pr(pr, files)
        assert "type/bug" in labels
        assert "status/needs-repro" not in labels

    def test_draft_label(self, labeler):
        pr = {"title": "feat: new feature", "body": "", "draft": True}
        files = [{"filename": "riptide/companion.py", "additions": 50, "deletions": 10}]
        labels = labeler.classify_pr(pr, files)
        assert "status/draft" in labels

    def test_blocked_label(self, labeler):
        pr = {"title": "feat: new feature", "body": "This is blocked on #123"}
        files = [{"filename": "riptide/companion.py", "additions": 50, "deletions": 10}]
        labels = labeler.classify_pr(pr, files)
        assert "status/blocked" in labels


class TestPriorityClassification:
    """Test priority label assignment."""

    def test_critical_from_security(self, labeler):
        pr = {"title": "Security fix for auth bypass", "body": ""}
        files = [{"filename": "riptide/webhook.py", "additions": 10, "deletions": 5}]
        labels = labeler.classify_pr(pr, files)
        assert "priority/critical" in labels

    def test_critical_from_data_loss(self, labeler):
        pr = {"title": "fix: prevent data loss on crash", "body": ""}
        files = [{"filename": "riptide/orchestrator.py", "additions": 20, "deletions": 10}]
        labels = labeler.classify_pr(pr, files)
        assert "priority/critical" in labels

    def test_high_from_crash(self, labeler):
        pr = {"title": "fix: crash on startup", "body": ""}
        files = [{"filename": "riptide/companion.py", "additions": 15, "deletions": 8}]
        labels = labeler.classify_pr(pr, files)
        assert "priority/high" in labels

    def test_medium_from_loc(self, labeler):
        pr = {"title": "fix: improve logging", "body": ""}
        files = [{"filename": "riptide/companion.py", "additions": 150, "deletions": 50}]
        labels = labeler.classify_pr(pr, files)
        assert "priority/medium" in labels

    def test_low_default(self, labeler):
        pr = {"title": "feat: add new button", "body": ""}
        files = [{"filename": "static/style.css", "additions": 5, "deletions": 2}]
        labels = labeler.classify_pr(pr, files)
        assert "priority/low" in labels


class TestScopeClassification:
    """Test scope label assignment."""

    def test_tiny(self, labeler):
        pr = {"title": "fix: typo", "body": ""}
        files = [{"filename": "README.md", "additions": 3, "deletions": 2}]
        labels = labeler.classify_pr(pr, files)
        assert "scope/tiny" in labels

    def test_small(self, labeler):
        pr = {"title": "fix: bug", "body": ""}
        files = [{"filename": "riptide/companion.py", "additions": 50, "deletions": 30}]
        labels = labeler.classify_pr(pr, files)
        assert "scope/small" in labels

    def test_medium(self, labeler):
        pr = {"title": "feat: new feature", "body": ""}
        files = [{"filename": "riptide/companion.py", "additions": 200, "deletions": 100}]
        labels = labeler.classify_pr(pr, files)
        assert "scope/medium" in labels

    def test_large(self, labeler):
        pr = {"title": "feat: big feature", "body": ""}
        files = [{"filename": "riptide/companion.py", "additions": 400, "deletions": 200}]
        labels = labeler.classify_pr(pr, files)
        assert "scope/large" in labels

    def test_massive(self, labeler):
        pr = {"title": "feat: huge feature", "body": ""}
        files = [{"filename": "riptide/companion.py", "additions": 800, "deletions": 400}]
        labels = labeler.classify_pr(pr, files)
        assert "scope/massive" in labels


class TestComponentClassification:
    """Test per-repo component label assignment."""

    def test_companion_component(self, labeler):
        pr = {"title": "fix: companion crash", "body": ""}
        files = [{"filename": "riptide/companion.py", "additions": 20, "deletions": 10}]
        labels = labeler.classify_pr(pr, files, repo="ChonSong/riptide")
        assert "comp/companion" in labels

    def test_infra_component_dockerfile(self, labeler):
        pr = {"title": "chore: update Docker image", "body": ""}
        files = [{"filename": "Dockerfile", "additions": 5, "deletions": 3}]
        labels = labeler.classify_pr(pr, files, repo="ChonSong/riptide")
        assert "comp/infra" in labels

    def test_infra_component_compose(self, labeler):
        pr = {"title": "chore: update compose", "body": ""}
        files = [{"filename": "docker-compose.yml", "additions": 10, "deletions": 5}]
        labels = labeler.classify_pr(pr, files, repo="ChonSong/riptide")
        assert "comp/infra" in labels

    def test_no_component_for_unknown_repo(self, labeler):
        pr = {"title": "fix: bug", "body": ""}
        files = [{"filename": "riptide/companion.py", "additions": 10, "deletions": 5}]
        labels = labeler.classify_pr(pr, files, repo="Unknown/repo")
        comp_labels = [label for label in labels if label.startswith("comp/")]
        assert len(comp_labels) == 0


class TestIssueClassification:
    """Test issue (no files) classification."""

    def test_issue_bug(self, labeler):
        issue = {"title": "Bug: crash on save", "body": "Steps to reproduce: click save"}
        labels = labeler.classify_issue(issue)
        assert "type/bug" in labels
        assert "status/needs-repro" not in labels  # has repro steps

    def test_issue_feature_request(self, labeler):
        issue = {"title": "Feature: add dark mode", "body": "Would be nice to have"}
        labels = labeler.classify_issue(issue)
        assert "type/feature" in labels

    def test_issue_needs_triage(self, labeler):
        issue = {"title": "Something's wrong", "body": ""}
        labels = labeler.classify_issue(issue)
        assert "status/needs-triage" in labels


class TestBotMarker:
    """Test that bot/labeled marker is always applied."""

    def test_bot_labeled_always(self, labeler):
        pr = {"title": "feat: something", "body": ""}
        files = [{"filename": "riptide/companion.py", "additions": 50, "deletions": 10}]
        labels = labeler.classify_pr(pr, files)
        assert "bot/labeled" in labels


class TestLLMVoter:
    """Test LLM fallback voter."""

    def test_classify_success(self):
        voter = LLMVoter()
        with patch("riptide.labeler.http_requests") as mock_requests:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": '["type/bug", "priority/high"]'}
            mock_requests.post.return_value = mock_response

            result = voter.classify("Fix crash", "Body", ["file.py"], ["type/bug", "priority/high"])
            assert "type/bug" in result
            assert "priority/high" in result

    def test_classify_handles_non_json(self):
        voter = LLMVoter()
        with patch("riptide.labeler.http_requests") as mock_requests:
            mock_response = MagicMock()
            mock_response.json.return_value = {"response": "I think it's a bug"}
            mock_requests.post.return_value = mock_response

            result = voter.classify("Fix crash", "Body", ["file.py"], ["type/bug"])
            assert result == []


class TestLabelerInit:
    """Test Labeler initialization."""

    def test_loads_default_path(self):
        """Test that Labeler loads from default path (the real taxonomy)."""
        labeler = Labeler()
        assert labeler.definitions is not None
        assert "shared_labels" in labeler.definitions
        assert "classification_rules" in labeler.definitions

    def test_loads_custom_path(self, tmp_path):
        """Test that Labeler loads from custom path."""
        definitions = {
            "version": "1.0.0",
            "shared_labels": {"dimensions": {}},
            "repo_components": {"repos": {}},
            "classification_rules": {},
        }
        path = tmp_path / "custom.json"
        path.write_text(json.dumps(definitions))
        labeler = Labeler(definitions_path=path)
        assert labeler.definitions == definitions


class TestSetupLabels:
    """Test repo label setup."""

    def test_creates_labels(self, labeler):
        github = MagicMock()
        github.ensure_label.return_value = {}
        labeler.setup_labels_on_repo(123, "ChonSong", "riptide", github)
        # Should call ensure_label for all shared + comp labels
        assert github.ensure_label.call_count > 0

    def test_setup_caching(self, labeler):
        """Test that repeated setup with unchanged version makes no per-label calls."""
        github = MagicMock()
        github.ensure_label.return_value = {}
        # First call
        labeler.setup_labels_on_repo(123, "ChonSong", "riptide", github)
        first_call_count = github.ensure_label.call_count
        assert first_call_count > 0
        # Second call (cache hit — should not call ensure_label again)
        labeler.setup_labels_on_repo(123, "ChonSong", "riptide", github)
        assert github.ensure_label.call_count == first_call_count

    def test_setup_cache_bump_on_version_change(self, labeler):
        """Test that changing taxonomy version triggers re-provisioning."""
        github = MagicMock()
        github.ensure_label.return_value = {}
        # First call
        labeler.setup_labels_on_repo(123, "ChonSong", "riptide", github)
        first_call_count = github.ensure_label.call_count
        # Bump version
        labeler.definitions["version"] = "2.0.0"
        labeler.setup_labels_on_repo(123, "ChonSong", "riptide", github)
        assert github.ensure_label.call_count > first_call_count

    def test_normalize_color_strips_hash(self, labeler):
        color = labeler._normalize_color("#FF5733")
        assert color == "FF5733"

    def test_normalize_color_validates_hex(self, labeler):
        with pytest.raises(ValueError):
            labeler._normalize_color("ZZZZZZ")


class TestLLMFallback:
    """Test LLM fallback integration."""

    def test_llm_fallback_on_unknown(self, labeler):
        """Test that LLM voter is invoked for unknown types."""
        # Mock the LLM voter
        mock_llm = MagicMock()
        mock_llm.classify.return_value = ["type/feature", "priority/low"]
        labeler._llm = mock_llm

        pr = {"title": "Something completely unknown", "body": "No conventional commit"}
        files = [{"filename": "random.txt", "additions": 5, "deletions": 2}]
        labels = labeler.classify_pr(pr, files)
        # LLM should have been called
        mock_llm.classify.assert_called_once()
        # Should include LLM result
        assert "type/feature" in labels
        # Should NOT have needs-triage (LLM resolved it)
        assert "status/needs-triage" not in labels

    def test_llm_fallback_empty_returns_triage(self, labeler):
        """Test that empty LLM result falls back to needs-triage."""
        mock_llm = MagicMock()
        mock_llm.classify.return_value = []
        labeler._llm = mock_llm

        pr = {"title": "Something unknown", "body": ""}
        files = [{"filename": "random.txt", "additions": 5, "deletions": 2}]
        labels = labeler.classify_pr(pr, files)
        assert "status/needs-triage" in labels

    def test_llm_result_filtered_to_canonical(self, labeler):
        """Test that non-canonical LLM results are filtered out."""
        mock_llm = MagicMock()
        mock_llm.classify.return_value = ["type/feature", "bogus/nonexistent", "priority/low"]
        labeler._llm = mock_llm

        pr = {"title": "Something unknown", "body": ""}
        files = [{"filename": "random.txt", "additions": 5, "deletions": 2}]
        labels = labeler.classify_pr(pr, files)
        assert "type/feature" in labels
        assert "priority/low" in labels
        assert "bogus/nonexistent" not in labels


class TestOrderPreservingDedup:
    """Test that dedup preserves rule order."""

    def test_order_preserved(self, labeler):
        pr = {"title": "fix: crash on save", "body": "Steps to reproduce: click save"}
        files = [{"filename": "riptide/companion.py", "additions": 50, "deletions": 30}]
        labels1 = labeler.classify_pr(pr, files)
        labels2 = labeler.classify_pr(pr, files)
        assert labels1 == labels2  # Deterministic order


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
