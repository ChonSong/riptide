# riptide/tests/test_review_pipeline.py
"""
Tests for review pipeline classification and skill selection.
"""

import pytest
from riptide.deepthink import (
    ReviewDepth,
    classify_review_depth,
    select_skills,
)


# ── Classification Tests ────────────────────────────────────────────────────


class TestClassifyReviewDepth:
    """Tests for rule-based PR depth classification."""

    def _make_data(self, files=None, god_nodes=None, communities=None):
        return {
            "files_changed": files or [],
            "god_nodes": god_nodes or [],
            "communities": communities or [],
            "diff_raw": "",
            "repo_tree": [],
            "graph_context": {},
        }

    def test_trivial_doc_change(self):
        data = self._make_data(
            files=[{"filename": "README.md", "additions": 3, "deletions": 1}],
        )
        assert classify_review_depth(data) == ReviewDepth.TRIVIAL

    def test_trivial_under_10_loc_no_logic(self):
        data = self._make_data(
            files=[{"filename": "docs/guide.md", "additions": 5, "deletions": 2}],
        )
        assert classify_review_depth(data) == ReviewDepth.TRIVIAL

    def test_inline_only_single_file_small(self):
        data = self._make_data(
            files=[{"filename": "fix.py", "additions": 15, "deletions": 5}],
        )
        assert classify_review_depth(data) == ReviewDepth.INLINE_ONLY

    def test_inline_only_50_loc_boundary(self):
        data = self._make_data(
            files=[{"filename": "fix.py", "additions": 30, "deletions": 19}],
        )
        assert classify_review_depth(data) == ReviewDepth.INLINE_ONLY

    def test_standard_normal_pr(self):
        data = self._make_data(
            files=[
                {"filename": "a.py", "additions": 40, "deletions": 10},
                {"filename": "b.py", "additions": 30, "deletions": 5},
            ],
            god_nodes=[{"name": "a.py", "edges": 10}],
        )
        assert classify_review_depth(data) == ReviewDepth.STANDARD

    def test_arch_many_files_high_impact(self):
        data = self._make_data(
            files=[
                {"filename": f"f{i}.py", "additions": 50, "deletions": 20}
                for i in range(6)
            ],
            god_nodes=[{"name": "core.py", "edges": 25}],
        )
        assert classify_review_depth(data) == ReviewDepth.ARCH

    def test_arch_large_loc_high_impact(self):
        data = self._make_data(
            files=[{"filename": "big.py", "additions": 250, "deletions": 50}],
            god_nodes=[{"name": "core.py", "edges": 30}],
        )
        assert classify_review_depth(data) == ReviewDepth.ARCH

    def test_many_files_low_impact_standard(self):
        """Many files but low graphify impact → STANDARD, not ARCH."""
        data = self._make_data(
            files=[
                {"filename": f"f{i}.py", "additions": 50, "deletions": 20}
                for i in range(6)
            ],
            god_nodes=[{"name": "small.py", "edges": 5}],
        )
        assert classify_review_depth(data) == ReviewDepth.STANDARD


# ── Skill Selection Tests ───────────────────────────────────────────────────


class TestSelectSkills:
    """Tests for skill selection based on review depth."""

    def test_trivial_no_skills(self):
        assert select_skills(ReviewDepth.TRIVIAL) == []

    def test_inline_only_skills(self):
        assert select_skills(ReviewDepth.INLINE_ONLY) == [
            "deep-think", "github-pr-lifecycle"
        ]

    def test_standard_skills(self):
        assert select_skills(ReviewDepth.STANDARD) == [
            "deep-think", "github-pr-lifecycle", "excalidraw"
        ]

    def test_arch_skills_includes_brooks(self):
        assert select_skills(ReviewDepth.ARCH) == [
            "deep-think", "github-pr-lifecycle", "excalidraw", "brooks-lint"
        ]
