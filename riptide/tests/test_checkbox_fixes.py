#!/usr/bin/env python3
# riptide/tests/test_checkbox_fixes.py — Edge case / regression tests.

import pytest
from riptide.checkbox import (
    parse_checkbox_state,
    parse_checkbox_toggles,
    parse_checkbox_unchecks,
    reset_checkboxes,
    reset_all_checkboxes,
    build_checkbox_footer,
    build_comment_with_footer,
    strip_checkbox_footer,
    extract_comment_edit,
    extract_pr_body_edit,
)


class TestEdgeCases:
    """Edge cases and regression tests."""

    def test_checkbox_with_special_chars_in_label(self):
        """Checkbox labels with special regex chars should still match."""
        body = "- [ ] label with (parens)\n"
        state = parse_checkbox_state(body)
        # Not in CHECKBOX_ACTIONS, so ignored
        assert "label with (parens)" not in state

    def test_multiple_checkboxes_same_label(self):
        """Multiple checkboxes with same label should use last value."""
        body = (
            "- [ ] 🔍 Trigger review\n"
            "- [x] 🔍 Trigger review\n"
        )
        state = parse_checkbox_state(body)
        # Regex finditer will find both; last one wins in dict
        assert state.get("🔍 Trigger review") is True

    def test_footer_at_end_of_long_body(self):
        """Footer appended to a long body should still be found."""
        body = "## Review\n" + "x" * 1000 + "\n\n---\n- [x] 🔍 Trigger review\n"
        result = reset_all_checkboxes(body)
        assert "- [ ] 🔍 Trigger review" in result

    def test_multiple_footer_blocks(self):
        """Multiple checkbox blocks should be handled."""
        body = (
            "## Review\n"
            "---\n"
            "- [x] 🔍 Trigger review\n"
            "Some text between\n"
            "---\n"
            "- [x] 🛠 Fix issues\n"
        )
        # First block match is replaced
        result = build_comment_with_footer(body, ["review"])
        # The body should still have the review checkbox
        assert "🔍 Trigger review" in result

    def test_toggle_detection_with_spaces_in_label(self):
        """Toggles with labels containing multiple spaces."""
        old = "- [ ] 📸 ProofShot\n"
        new = "- [x] 📸 ProofShot\n"
        result = parse_checkbox_toggles(old, new)
        assert result == ["📸 ProofShot"]

    def test_body_with_no_checkboxes(self):
        """Body with no checkboxes at all."""
        body = "Just plain text\nNo checkboxes here"
        assert parse_checkbox_state(body) == {}
        assert parse_checkbox_toggles(body, body) == []
        assert parse_checkbox_unchecks(body, body) == []

    def test_reset_unknown_label_doesnt_modify(self):
        """Resetting an unknown label should not modify the body."""
        body = "- [x] unknown label\n"
        result = reset_checkboxes(body, ["🔍 Trigger review"])
        assert result == body

    def test_empty_label_in_checkbox(self):
        """Checkbox with empty label should parse but not match actions."""
        body = "- [ ] \n"
        state = parse_checkbox_state(body)
        assert state == {}

    def test_whitespace_only_label(self):
        """Checkbox with whitespace-only label."""
        body = "- [ ]    \n"
        state = parse_checkbox_state(body)
        assert state == {}

    def test_extract_comment_edit_with_no_changes_key(self):
        """Payload without changes key - body is present so it should work."""
        payload = {
            "action": "edited",
            "comment": {"id": 1, "body": "new body", "user": {"login": "a"}},
            "issue": {"number": 1, "pull_request": {"url": "https://api.github.com/repos/owner/repo/pulls/1"}},
            "repository": {"full_name": "owner/repo"},
        }
        result = extract_comment_edit(payload)
        assert result is not None
        assert result["body"] == "new body"
        assert result["old_body"] == ""

    def test_extract_comment_edit_with_empty_changes(self):
        """Payload with empty changes body."""
        payload = {
            "action": "edited",
            "comment": {"id": 1, "body": "", "user": {"login": "a"}},
            "issue": {"number": 1, "pull_request": {}},
            "changes": {"body": {"from": ""}},
            "repository": {"full_name": "owner/repo"},
        }
        result = extract_comment_edit(payload)
        # Both body and old_body empty
        assert result is None

    def test_extract_pr_body_edit_with_none_body(self):
        """PR body that is None (empty)."""
        payload = {
            "action": "edited",
            "pull_request": {
                "number": 42,
                "body": None,
                "user": {"login": "a"},
            },
            "changes": {"body": {"from": None}},
            "repository": {"full_name": "owner/repo"},
        }
        result = extract_pr_body_edit(payload)
        # Both None → empty string, so returns None
        assert result is None

    def test_build_checkbox_footer_with_duplicates(self):
        """Building footer with duplicate actions."""
        footer = build_checkbox_footer(["review", "review"])
        # Should have two review lines
        count = footer.count("🔍 Trigger review")
        assert count == 2

    def test_reset_checkboxes_preserves_order(self):
        """Reset should preserve the order of checkboxes."""
        body = (
            "- [x] 🔍 Trigger review\n"
            "- [x] 🛠 Fix issues\n"
            "- [x] 📸 ProofShot\n"
            "- [x] 🏷️ Relabel\n"
        )
        result = reset_checkboxes(body, ["🔍 Trigger review"])
        lines = result.strip().split("\n")
        assert lines[0] == "- [ ] 🔍 Trigger review"
        assert lines[1] == "- [x] 🛠 Fix issues"

    def test_strip_footer_with_only_footer(self):
        """Body that is only a checkbox footer."""
        body = "---\n- [x] 🔍 Trigger review\n"
        result = strip_checkbox_footer(body)
        assert result.strip() == ""

    def test_comment_with_footer_no_trailing_newlines(self):
        """Body without trailing newlines should get footer appended properly."""
        body = "## Review"
        result = build_comment_with_footer(body, ["review"])
        assert "## Review" in result
        assert "- [ ] 🔍 Trigger review" in result

    def test_toggle_with_empty_old_body(self):
        """Toggle detection when old body is empty."""
        old = ""
        new = "- [x] 🔍 Trigger review\n"
        result = parse_checkbox_toggles(old, new)
        assert result == ["🔍 Trigger review"]

    def test_toggle_with_empty_new_body(self):
        """Toggle detection when new body is empty."""
        old = "- [ ] 🔍 Trigger review\n"
        new = ""
        result = parse_checkbox_toggles(old, new)
        assert result == []


class TestRegressions:
    """Regression tests for known edge cases."""

    def test_checkbox_re_does_not_match_indented(self):
        """Indented checkboxes should not match (regression)."""
        body = "  - [ ] 🔍 Trigger review\n"
        state = parse_checkbox_state(body)
        assert state == {}

    def test_checkbox_re_does_not_match_inline(self):
        """Inline checkboxes in a paragraph should not match."""
        body = "Text - [ ] 🔍 Trigger review more text\n"
        state = parse_checkbox_state(body)
        assert state == {}

    def test_reset_with_label_containing_regex_metacharacters(self):
        """Reset with a label containing regex metacharacters."""
        body = "- [x] label.with.dots\n"
        # The label is not in CHECKBOX_ACTIONS, so it won't be affected
        # by reset_all_checkboxes
        result = reset_all_checkboxes(body)
        assert result == body

    def test_strip_footer_handles_windows_line_endings(self):
        """Strip footer with \\r\\n line endings."""
        body = "Review text\r\n\r\n---\r\n- [x] 🔍 Trigger review\r\n"
        result = strip_checkbox_footer(body)
        assert "🔍" not in result

    def test_parse_toggles_with_windows_line_endings(self):
        """Toggle detection with \\r\\n line endings."""
        old = "- [ ] 🔍 Trigger review\r\n"
        new = "- [x] 🔍 Trigger review\r\n"
        result = parse_checkbox_toggles(old, new)
        assert result == ["🔍 Trigger review"]