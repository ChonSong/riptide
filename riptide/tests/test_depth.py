# riptide/tests/test_depth.py
"""Tests for review-depth classification and its human-readable reasons.

`describe_depth()` is the sentence a pass comment prints next to a depth class.
It lives in depth.py, next to the thresholds it narrates, so a reader gets the
reason for `trivial` instead of a bare `Depth: trivial` — and so the wording
cannot drift away from the rule that produced the class.
"""

import pytest

from riptide.depth import (
    LOGIC_EXTENSIONS,
    ReviewDepth,
    classify_review_depth,
    describe_depth,
    select_skills,
)


def _data(files=None, god_nodes=None):
    return {"files_changed": files or [], "god_nodes": god_nodes or []}


# Fixtures chosen so each class is reached by exactly one rule.
TRIVIAL_FILES = [{"filename": "README.md", "additions": 3, "deletions": 1}]
INLINE_FILES = [{"filename": "riptide/depth.py", "additions": 15, "deletions": 5}]
STANDARD_FILES = [
    {"filename": f"riptide/mod{i}.py", "additions": 5, "deletions": 0} for i in range(3)
]
ARCH_FILES = [
    {"filename": f"riptide/mod{i}.py", "additions": 40, "deletions": 0} for i in range(6)
]
ARCH_GOD_NODES = [{"name": "conductor", "edges": 42}]


class TestDescribeDepth:
    """The reason string must match the rule that decided the class."""

    def test_every_class_has_a_reason(self):
        """A class with no reason would print an empty explanation."""
        for depth in ReviewDepth:
            reason = describe_depth(depth)
            assert reason
            assert "no depth rule matched" not in reason

    @pytest.mark.parametrize("depth", list(ReviewDepth))
    def test_accepts_the_enum_and_its_string_value(self, depth):
        """`companion._depth` is a string; the classifier returns an enum."""
        assert describe_depth(depth) == describe_depth(depth.value)

    def test_trivial_reason_names_both_conditions(self):
        """TRIVIAL fires on `<10 LOC` AND `no logic files` — say both."""
        reason = describe_depth(ReviewDepth.TRIVIAL)
        assert "10 changed LOC" in reason
        assert "logic files" in reason

    def test_inline_only_reason_names_the_single_file_rule(self):
        reason = describe_depth(ReviewDepth.INLINE_ONLY)
        assert "single file" in reason
        assert "50 changed LOC" in reason

    def test_arch_reason_names_the_size_and_blast_radius(self):
        reason = describe_depth(ReviewDepth.ARCH)
        assert "5 files" in reason
        assert "200+" in reason
        assert "graphify" in reason

    def test_unknown_class_is_reported_never_guessed(self):
        """A new enum member must surface as unmatched, not as a wrong reason."""
        assert describe_depth("nonsense") == "no depth rule matched `nonsense`"
        assert describe_depth(None) == "no depth rule matched `None`"

    def test_reason_is_true_of_the_diff_that_produced_the_class(self):
        """End to end: classify a diff, then check the printed reason fits."""
        cases = [
            (TRIVIAL_FILES, ReviewDepth.TRIVIAL, "10 changed LOC"),
            (INLINE_FILES, ReviewDepth.INLINE_ONLY, "single file"),
            (STANDARD_FILES, ReviewDepth.STANDARD, "not small enough to skip"),
            (ARCH_FILES, ReviewDepth.ARCH, "5 files"),
        ]
        for files, expected, marker in cases:
            depth = classify_review_depth(_data(files=files, god_nodes=ARCH_GOD_NODES))
            assert depth is expected, files
            assert marker in describe_depth(depth)

    def test_reason_has_no_trailing_period(self):
        """Callers own the sentence punctuation (companion appends the rest)."""
        for depth in ReviewDepth:
            assert not describe_depth(depth).endswith(".")


class TestLogicExtensions:
    """One definition of "logic file", shared with the pass comment."""

    def test_extension_list_is_non_empty_and_suffixes(self):
        assert LOGIC_EXTENSIONS
        assert all(ext.startswith(".") for ext in LOGIC_EXTENSIONS)

    def test_a_one_line_logic_file_is_never_trivial(self):
        """The classifier must use the same list the pass comment narrates."""
        for ext in LOGIC_EXTENSIONS:
            depth = classify_review_depth(
                _data(files=[{"filename": f"src/thing{ext}", "additions": 1, "deletions": 0}])
            )
            assert depth is not ReviewDepth.TRIVIAL, ext

    def test_a_one_line_doc_change_is_trivial(self):
        depth = classify_review_depth(
            _data(files=[{"filename": "NOTES.txt", "additions": 0, "deletions": 1}])
        )
        assert depth is ReviewDepth.TRIVIAL


class TestSelectSkills:
    """Guard the mapping describe_depth explains (no skills for TRIVIAL)."""

    def test_trivial_loads_no_skills(self):
        assert select_skills(ReviewDepth.TRIVIAL) == []

    def test_arch_adds_brooks_lint(self):
        assert "brooks-lint" in select_skills(ReviewDepth.ARCH)
