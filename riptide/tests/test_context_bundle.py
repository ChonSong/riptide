# riptide/tests/test_context_bundle.py
"""
Tests for the deterministic context-bundle pipeline (Vision Pillar 1).
Covers concept classification, aggregate stats, summary helper, and DiffAnalyzer integration.
"""

import pytest

from riptide.context_bundle import (
    DiffConcept,
    build_context_bundle,
    classify_concept,
    concept_summary,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def make_file(filename: str, additions: int = 0, deletions: int = 0,
              status: str = "modified", patch: str = "") -> dict:
    return {
        "filename": filename,
        "additions": additions,
        "deletions": deletions,
        "status": status,
        "patch": patch,
    }


# ── classify_concept ────────────────────────────────────────────────────────


class TestClassifyConcept:
    """Tests for deterministic filename→concept classification."""

    @pytest.mark.parametrize(
        "filename,expected_concept",
        [
            ("src/auth/login.py", "auth"),
            ("server/auth/handler.py", "auth"),
            ("api/oauth.py", "auth"),
            ("routes/login.py", "auth"),
            ("src/auth/token.py", "auth"),
        ],
    )
    def test_auth_files(self, filename, expected_concept):
        assert classify_concept(filename) == expected_concept

    @pytest.mark.parametrize(
        "filename,expected_concept",
        [
            ("src/payments/stripe.py", "payments"),
            ("billing/invoice.py", "payments"),
            ("api/subscription.py", "payments"),
        ],
    )
    def test_payments_files(self, filename, expected_concept):
        assert classify_concept(filename) == expected_concept

    @pytest.mark.parametrize(
        "filename,expected_concept",
        [
            ("src/api/routes.py", "api"),
            ("controllers/user.py", "api"),
            ("api/endpoints.py", "api"),
            ("middleware/handler.py", "api"),  # middleware without auth keyword
        ],
    )
    def test_api_files(self, filename, expected_concept):
        assert classify_concept(filename) == expected_concept

    @pytest.mark.parametrize(
        "filename,expected_concept",
        [
            ("ui/components/button.tsx", "ui"),
            ("src/styles/main.css", "ui"),
            ("components/navbar.jsx", "ui"),
            ("pages/home.vue", "ui"),
            ("theme/dark.scss", "ui"),
        ],
    )
    def test_ui_files(self, filename, expected_concept):
        assert classify_concept(filename) == expected_concept

    @pytest.mark.parametrize(
        "filename,expected_concept",
        [
            ("tests/test_user.py", "tests"),
            ("test_login.py", "tests"),
            ("specs/auth.spec.js", "tests"),  # specs/ directory
            ("src/__tests__/helper.js", "tests"),
            ("foo_test.py", "tests"),    # basename _test. suffix
            ("foo.spec.js", "tests"),    # basename .spec. suffix
            ("src/foo_test.py", "tests"),
            ("src/foo.spec.js", "tests"),
        ],
    )
    def test_test_files(self, filename, expected_concept):
        assert classify_concept(filename) == expected_concept

    @pytest.mark.parametrize(
        "filename,expected_concept",
        [
            ("README.md", "docs"),
            ("docs/guide.mdx", "docs"),
            ("CHANGELOG.rst", "docs"),
            ("tutorials/setup.md", "docs"),
        ],
    )
    def test_docs_files(self, filename, expected_concept):
        assert classify_concept(filename) == expected_concept

    @pytest.mark.parametrize(
        "filename,expected_concept",
        [
            ("config/settings.yml", "config"),
            ("Dockerfile", "config"),
            (".github/workflows/ci.yml", "config"),
            ("pyproject.toml", "config"),
        ],
    )
    def test_config_files(self, filename, expected_concept):
        assert classify_concept(filename) == expected_concept

    @pytest.mark.parametrize(
        "filename,expected_concept",
        [
            ("server.py", "core"),
            ("webhook.py", "core"),
            ("src/api/webhook.py", "core"),  # webhook under api/ must not be api
            ("riptide/orchestrator.py", "core"),
            ("deepthink.py", "core"),
            ("companion.py", "core"),
            ("diff_analyzer.py", "core"),
            ("state.py", "core"),
        ],
    )
    def test_core_files(self, filename, expected_concept):
        assert classify_concept(filename) == expected_concept

    def test_unknown_file_defaults_to_core(self):
        assert classify_concept("random_xyz.py") == "core"

    def test_empty_string_defaults_to_core(self):
        assert classify_concept("") == "core"


# ── DiffConcept ─────────────────────────────────────────────────────────────


class TestDiffConcept:
    """Tests for the DiffConcept dataclass."""

    def test_creation(self):
        dc = DiffConcept(
            filename="src/auth/login.py",
            concept="auth",
            additions=10,
            deletions=2,
            status="modified",
            has_patch=True,
        )
        assert dc.filename == "src/auth/login.py"
        assert dc.concept == "auth"
        assert dc.additions == 10
        assert dc.deletions == 2
        assert dc.status == "modified"
        assert dc.has_patch is True

    def test_has_patch_false(self):
        dc = DiffConcept(filename="README.md", concept="docs", additions=0, deletions=0, status="modified", has_patch=False)
        assert dc.has_patch is False


# ── build_context_bundle ────────────────────────────────────────────────────


class TestBuildContextBundle:
    """Tests for the main build_context_bundle function."""

    def test_basic_bundle_structure(self):
        files = [
            make_file("src/auth/login.py", additions=10, deletions=2, status="modified", patch="+def hello():\n+    return 1\n"),
            make_file("src/api/routes.py", additions=5, deletions=0, status="added"),
        ]
        bundle = build_context_bundle(files, graph_context=None)

        assert "findings" in bundle
        assert "stats" in bundle
        assert "verdict" in bundle
        assert "summary" in bundle
        assert "concepts" in bundle
        assert "aggregate" in bundle
        assert "test_status" in bundle
        assert "graph_context" in bundle

    def test_aggregate_stats_total_loc(self):
        files = [
            make_file("a.py", additions=100, deletions=50),
            make_file("b.py", additions=30, deletions=20),
        ]
        bundle = build_context_bundle(files, graph_context=None)
        # total_loc = sum of additions + deletions = 100 + 50 + 30 + 20 = 200
        assert bundle["aggregate"]["total_loc"] == 200

    def test_aggregate_stats_files_count(self):
        files = [
            make_file("a.py"),
            make_file("b.py"),
            make_file("c.py"),
        ]
        bundle = build_context_bundle(files, graph_context=None)
        assert bundle["aggregate"]["files_count"] == 3

    def test_aggregate_concepts_deduped(self):
        files = [
            make_file("src/auth/login.py"),
            make_file("src/auth/logout.py"),
            make_file("src/api/routes.py"),
        ]
        bundle = build_context_bundle(files, graph_context=None)
        concepts = bundle["aggregate"]["concepts"]
        assert "auth" in concepts
        assert "api" in concepts
        # Deduped: only one "auth" entry
        assert concepts.count("auth") == 1

    def test_touches_core_true(self):
        files = [
            make_file("server.py"),
            make_file("random.py"),
        ]
        bundle = build_context_bundle(files, graph_context=None)
        assert bundle["aggregate"]["touches_core"] is True

    def test_touches_core_false(self):
        files = [
            make_file("src/auth/login.py"),
            make_file("tests/test_api.py"),
        ]
        bundle = build_context_bundle(files, graph_context=None)
        assert bundle["aggregate"]["touches_core"] is False

    def test_touches_core_false_basename_test_files(self):
        """Basename _test. / .spec. files classify as tests, not core."""
        files = [
            make_file("foo_test.py"),
            make_file("foo.spec.js"),
        ]
        bundle = build_context_bundle(files, graph_context=None)
        assert bundle["aggregate"]["touches_core"] is False

    def test_touches_core_via_core_rule(self):
        """Core files (matching the CONCEPT_RULES core regex) are classified as core concept."""
        files = [
            make_file("webhook.py"),
        ]
        bundle = build_context_bundle(files, graph_context=None)
        assert bundle["aggregate"]["touches_core"] is True
        assert bundle["concepts"][0]["concept"] == "core"

    def test_is_draft_true(self):
        files = [make_file("a.py")]
        pr_details = {"title": "WIP", "body": "", "author": "user", "draft": True}
        bundle = build_context_bundle(files, graph_context=None, pr_details=pr_details)
        assert bundle["aggregate"]["is_draft"] is True

    def test_is_draft_false(self):
        files = [make_file("a.py")]
        pr_details = {"title": "Ready", "body": "", "author": "user", "draft": False}
        bundle = build_context_bundle(files, graph_context=None, pr_details=pr_details)
        assert bundle["aggregate"]["is_draft"] is False

    def test_is_draft_default(self):
        files = [make_file("a.py")]
        bundle = build_context_bundle(files, graph_context=None)
        assert bundle["aggregate"]["is_draft"] is False

    def test_has_repro_steps_true(self):
        files = [make_file("a.py")]
        pr_details = {
            "title": "Bug",
            "body": "Steps to reproduce:\n1. Click button\n2. Crash",
            "author": "user",
        }
        bundle = build_context_bundle(files, graph_context=None, pr_details=pr_details)
        assert bundle["aggregate"]["has_repro_steps"] is True

    def test_has_repro_steps_variations(self):
        files = [make_file("a.py")]
        variations = [
            "Reproduction steps:\n1. Do X",
            "How to reproduce:\n1. Click",
            "Repro steps:\n- step 1",
        ]
        for body in variations:
            pr_details = {"title": "Bug", "body": body, "author": "user"}
            bundle = build_context_bundle(files, graph_context=None, pr_details=pr_details)
            assert bundle["aggregate"]["has_repro_steps"] is True, f"Failed for: {body}"

    def test_has_repro_steps_false(self):
        files = [make_file("a.py")]
        pr_details = {
            "title": "Feature",
            "body": "This PR adds a new button component.",
            "author": "user",
        }
        bundle = build_context_bundle(files, graph_context=None, pr_details=pr_details)
        assert bundle["aggregate"]["has_repro_steps"] is False

    def test_has_repro_steps_no_body(self):
        files = [make_file("a.py")]
        bundle = build_context_bundle(files, graph_context=None)
        assert bundle["aggregate"]["has_repro_steps"] is False

    def test_has_repro_steps_empty_body(self):
        files = [make_file("a.py")]
        pr_details = {"title": "Bug", "body": None, "author": "user"}
        bundle = build_context_bundle(files, graph_context=None, pr_details=pr_details)
        assert bundle["aggregate"]["has_repro_steps"] is False

    def test_test_status_placeholder(self):
        files = [make_file("a.py")]
        bundle = build_context_bundle(files, graph_context=None)
        assert bundle["test_status"] == {"available": False, "status": None}

    def test_graph_context_none(self):
        files = [make_file("a.py")]
        bundle = build_context_bundle(files, graph_context=None)
        assert bundle["graph_context"] is None

    def test_graph_context_included(self):
        files = [make_file("a.py")]
        graph_context = {"raw": "--- a.py ---\n- node1\n- node2", "nodes": 2, "files_checked": 1}
        bundle = build_context_bundle(files, graph_context=graph_context)
        assert bundle["graph_context"] == "--- a.py ---\n- node1\n- node2"

    def test_per_file_concepts(self):
        files = [
            make_file("src/auth/login.py", additions=10, deletions=2, status="modified", patch="+def hello():\n+    return 1\n"),
            make_file("tests/test_auth.py", additions=20, deletions=0, status="added", patch="+def test():\n+    pass\n"),
            make_file("README.md", additions=5, deletions=1, status="modified"),
        ]
        bundle = build_context_bundle(files, graph_context=None)

        concepts = bundle["concepts"]
        assert len(concepts) == 3
        assert concepts[0]["concept"] == "auth"
        assert concepts[0]["additions"] == 10
        assert concepts[0]["deletions"] == 2
        assert concepts[0]["status"] == "modified"
        assert concepts[0]["has_patch"] is True

        assert concepts[1]["concept"] == "tests"
        assert concepts[1]["status"] == "added"
        assert concepts[1]["has_patch"] is True

        assert concepts[2]["concept"] == "docs"
        assert concepts[2]["has_patch"] is False  # empty patch

    def test_diff_analyzer_findings_included(self):
        """Bundle should include DiffAnalyzer findings when security issues present."""
        SECURITY_PATCH = '+api_key = "AKIAIOSFODNN7EXAMPLE"\n'
        files = [
            make_file("src/auth/login.py", additions=1, deletions=0, patch=SECURITY_PATCH),
        ]
        bundle = build_context_bundle(files, graph_context=None)

        # Should have findings (AWS key detected)
        assert len(bundle["findings"]) > 0
        assert any(f["category"] == "security" for f in bundle["findings"])

    def test_diff_analyzer_verdict_block(self):
        """Verdict should be 'block' when critical findings exist."""
        SECURITY_PATCH = '+password = "super_secret_12345"\n'
        files = [
            make_file("config/settings.py", additions=1, deletions=0, patch=SECURITY_PATCH),
        ]
        bundle = build_context_bundle(files, graph_context=None)
        assert bundle["verdict"] == "block"

    def test_diff_analyzer_verdict_pass(self):
        """Verdict should be 'pass' when no findings."""
        SAFE_PATCH = '+def hello():\n+    return "world"\n'
        files = [
            make_file("utils/helpers.py", additions=2, deletions=0, patch=SAFE_PATCH),
        ]
        bundle = build_context_bundle(files, graph_context=None)
        assert bundle["verdict"] == "pass"

    def test_empty_files_list(self):
        """Bundle should handle empty files list gracefully."""
        bundle = build_context_bundle([], graph_context=None)
        assert bundle["aggregate"]["total_loc"] == 0
        assert bundle["aggregate"]["files_count"] == 0
        assert bundle["aggregate"]["concepts"] == []
        assert bundle["verdict"] == "pass"

    def test_pr_details_title_and_author(self):
        files = [make_file("a.py")]
        pr_details = {"title": "feat: new feature", "body": "", "author": "testuser"}
        bundle = build_context_bundle(files, graph_context=None, pr_details=pr_details)
        assert bundle["aggregate"]["title"] == "feat: new feature"
        assert bundle["aggregate"]["author"] == "testuser"


# ── concept_summary ─────────────────────────────────────────────────────────


class TestConceptSummary:
    """Tests for the concept_summary helper."""

    def test_basic_shape(self):
        bundle = {
            "aggregate": {
                "concepts": ["auth", "api"],
                "total_loc": 412,
            },
            "concepts": [],
        }
        summary = concept_summary(bundle)
        assert isinstance(summary, str)
        assert "auth" in summary
        assert "api" in summary
        assert "412 LOC total" in summary

    def test_touches_prefix(self):
        bundle = {
            "aggregate": {
                "concepts": ["payments"],
                "total_loc": 100,
            },
            "concepts": [],
        }
        summary = concept_summary(bundle)
        assert "touches" in summary
        assert "payments" in summary

    def test_test_files_added(self):
        bundle = {
            "aggregate": {
                "concepts": ["tests"],
                "total_loc": 200,
            },
            "concepts": [
                {"concept": "tests", "status": "added"},
                {"concept": "tests", "status": "added"},
            ],
        }
        summary = concept_summary(bundle)
        assert "adds 2 test files" in summary

    def test_single_test_file(self):
        bundle = {
            "aggregate": {
                "concepts": ["tests"],
                "total_loc": 50,
            },
            "concepts": [
                {"concept": "tests", "status": "added"},
            ],
        }
        summary = concept_summary(bundle)
        assert "adds 1 test file" in summary

    def test_modified_test_files_not_counted(self):
        """Only 'added' test files count towards 'adds N test files'."""
        bundle = {
            "aggregate": {
                "concepts": ["tests"],
                "total_loc": 100,
            },
            "concepts": [
                {"concept": "tests", "status": "modified"},
            ],
        }
        summary = concept_summary(bundle)
        # Should not say "adds test file" for modified
        assert "adds" not in summary

    def test_empty_concepts(self):
        bundle = {
            "aggregate": {
                "concepts": [],
                "total_loc": 0,
            },
            "concepts": [],
        }
        summary = concept_summary(bundle)
        assert "0 LOC total" in summary

    def test_concept_summary_full_integration(self):
        """Full integration: build bundle then summarize."""
        files = [
            make_file("src/auth/login.py", additions=50, deletions=10, status="modified", patch="+def login():\n+    pass\n"),
            make_file("src/payments/stripe.py", additions=100, deletions=20, status="modified", patch="+def charge():\n+    pass\n"),
            make_file("tests/test_auth.py", additions=200, deletions=0, status="added", patch="+def test_login():\n+    pass\n"),
        ]
        bundle = build_context_bundle(files, graph_context=None)
        summary = concept_summary(bundle)

        assert "auth" in summary
        assert "payments" in summary
        assert "adds 1 test file" in summary
        # total_loc = 50+10 + 100+20 + 200+0 = 380
        assert "380 LOC total" in summary


# ── Integration: companion can build bundle ─────────────────────────────────


class TestCompanionBundleIntegration:
    """Test that companion can build a context bundle without crashing."""

    def test_companion_builds_bundle(self):
        from riptide.companion import Companion
        from unittest.mock import patch, MagicMock

        client = MagicMock()
        with patch("threading.Thread"):
            companion = Companion(client)

        files = [
            make_file("src/auth/login.py", additions=10, deletions=2, status="modified", patch="+def login():\n+    pass\n"),
            make_file("tests/test_auth.py", additions=20, deletions=0, status="added"),
        ]
        # Should not crash
        bundle = companion.build_context_bundle(files, graph_context=None)
        assert bundle is not None
        assert "concepts" in bundle
        assert "aggregate" in bundle