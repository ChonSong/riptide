#!/usr/bin/env python3
# riptide/tests/test_checkbox.py — Pure unit tests for checkbox parsing.

import pytest
from riptide.checkbox import (
    CHECKBOX_ACTIONS,
    ACTION_LABELS,
    CHECKBOX_RE,
    CHECKBOX_BLOCK_RE,
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


class TestConstants:
    """Test that constants are correctly defined."""

    def test_checkbox_actions_dict(self):
        assert "🔍 Trigger review" in CHECKBOX_ACTIONS
        assert CHECKBOX_ACTIONS["🔍 Trigger review"] == "review"
        assert CHECKBOX_ACTIONS["🛠 Fix issues"] == "fix"
        assert CHECKBOX_ACTIONS["📸 ProofShot"] == "visual"
        assert CHECKBOX_ACTIONS["🏷️ Relabel"] == "relabel"

    def test_action_labels_reverse(self):
        assert ACTION_LABELS["review"] == "🔍 Trigger review"
        assert ACTION_LABELS["fix"] == "🛠 Fix issues"
        assert ACTION_LABELS["visual"] == "📸 ProofShot"
        assert ACTION_LABELS["relabel"] == "🏷️ Relabel"

    def test_actions_are_complete(self):
        assert len(CHECKBOX_ACTIONS) == 4
        assert set(CHECKBOX_ACTIONS.values()) == {"review", "fix", "visual", "relabel"}


class TestCheckboxRegex:
    """Test the CHECKBOX_RE pattern."""

    def test_match_unchecked(self):
        m = CHECKBOX_RE.match("- [ ] some label")
        assert m is not None
        assert m.group(1) == " "
        assert m.group(2) == "some label"

    def test_match_checked_lowercase(self):
        m = CHECKBOX_RE.match("- [x] some label")
        assert m is not None
        assert m.group(1) == "x"

    def test_match_checked_uppercase(self):
        m = CHECKBOX_RE.match("- [X] some label")
        assert m is not None
        assert m.group(1) == "X"

    def test_no_match_non_checkbox(self):
        assert CHECKBOX_RE.match("just text") is None

    def test_no_match_partial(self):
        assert CHECKBOX_RE.match("[ ] missing dash") is None

    def test_no_match_empty_label(self):
        m = CHECKBOX_RE.match("- [ ] ")
        # Empty label after strip - regex requires at least 1 char
        assert m is None


class TestCheckboxBlockRegex:
    """Test CHECKBOX_BLOCK_RE."""

    def test_match_block(self):
        text = "---\n- [ ] line 1\n- [x] line 2\n"
        m = CHECKBOX_BLOCK_RE.search(text)
        assert m is not None
        assert "- [ ] line 1" in m.group(0)
        assert "- [x] line 2" in m.group(0)

    def test_match_block_with_separator(self):
        text = "some text\n\n---\n- [ ] action\nmore text"
        m = CHECKBOX_BLOCK_RE.search(text)
        assert m is not None


class TestParseCheckboxState:
    """Test parse_checkbox_state."""

    def test_empty_body(self):
        result = parse_checkbox_state("")
        assert result == {}

    def test_all_unchecked(self):
        body = "- [ ] 🔍 Trigger review\n- [ ] 🛠 Fix issues\n"
        result = parse_checkbox_state(body)
        assert result["🔍 Trigger review"] is False
        assert result["🛠 Fix issues"] is False

    def test_all_checked(self):
        body = "- [x] 🔍 Trigger review\n- [x] 🛠 Fix issues\n"
        result = parse_checkbox_state(body)
        assert result["🔍 Trigger review"] is True
        assert result["🛠 Fix issues"] is True

    def test_mixed_state(self):
        body = "- [x] 🔍 Trigger review\n- [ ] 🛠 Fix issues\n"
        result = parse_checkbox_state(body)
        assert result["🔍 Trigger review"] is True
        assert result["🛠 Fix issues"] is False

    def test_uppercase_x(self):
        body = "- [X] 🔍 Trigger review\n"
        result = parse_checkbox_state(body)
        assert result["🔍 Trigger review"] is True

    def test_ignores_unknown_labels(self):
        body = "- [ ] unknown label\n- [x] 🔍 Trigger review\n"
        result = parse_checkbox_state(body)
        assert "unknown label" not in result
        assert result["🔍 Trigger review"] is True

    def test_all_four_actions(self):
        body = (
            "- [ ] 🔍 Trigger review\n"
            "- [ ] 🛠 Fix issues\n"
            "- [ ] 📸 ProofShot\n"
            "- [ ] 🏷️ Relabel\n"
        )
        result = parse_checkbox_state(body)
        assert len(result) == 4
        assert all(not v for v in result.values())

    def test_body_with_other_content(self):
        body = "Some review text\n\n---\n- [x] 🔍 Trigger review\n"
        result = parse_checkbox_state(body)
        assert result == {"🔍 Trigger review": True}


class TestParseCheckboxToggles:
    """Test parse_checkbox_toggles."""

    def test_no_changes(self):
        body = "- [ ] 🔍 Trigger review\n"
        assert parse_checkbox_toggles(body, body) == []

    def test_single_check(self):
        old = "- [ ] 🔍 Trigger review\n"
        new = "- [x] 🔍 Trigger review\n"
        result = parse_checkbox_toggles(old, new)
        assert result == ["🔍 Trigger review"]

    def test_multiple_checks(self):
        old = "- [ ] 🔍 Trigger review\n- [ ] 🛠 Fix issues\n"
        new = "- [x] 🔍 Trigger review\n- [x] 🛠 Fix issues\n"
        result = parse_checkbox_toggles(old, new)
        assert "🔍 Trigger review" in result
        assert "🛠 Fix issues" in result
        assert len(result) == 2

    def test_uncheck_not_reported(self):
        old = "- [x] 🔍 Trigger review\n"
        new = "- [ ] 🔍 Trigger review\n"
        result = parse_checkbox_toggles(old, new)
        assert result == []

    def test_check_then_uncheck_reports_nothing(self):
        # Both happen → should only report check (since state went back)
        old = "- [ ] 🔍 Trigger review\n"
        new = "- [ ] 🔍 Trigger review\n"
        result = parse_checkbox_toggles(old, new)
        assert result == []

    def test_unknown_label_not_reported(self):
        old = "- [ ] some random label\n"
        new = "- [x] some random label\n"
        result = parse_checkbox_toggles(old, new)
        assert result == []


class TestParseCheckboxUnchecks:
    """Test parse_checkbox_unchecks."""

    def test_single_uncheck(self):
        old = "- [x] 🔍 Trigger review\n"
        new = "- [ ] 🔍 Trigger review\n"
        result = parse_checkbox_unchecks(old, new)
        assert result == ["🔍 Trigger review"]

    def test_check_not_reported(self):
        old = "- [ ] 🔍 Trigger review\n"
        new = "- [x] 🔍 Trigger review\n"
        result = parse_checkbox_unchecks(old, new)
        assert result == []


class TestResetCheckboxes:
    """Test reset_checkboxes."""

    def test_reset_single(self):
        body = "- [x] 🔍 Trigger review\n- [ ] 🛠 Fix issues\n"
        result = reset_checkboxes(body, ["🔍 Trigger review"])
        assert result == "- [ ] 🔍 Trigger review\n- [ ] 🛠 Fix issues\n"

    def test_reset_multiple(self):
        body = "- [x] 🔍 Trigger review\n- [x] 🛠 Fix issues\n"
        result = reset_checkboxes(body, ["🔍 Trigger review", "🛠 Fix issues"])
        assert result == "- [ ] 🔍 Trigger review\n- [ ] 🛠 Fix issues\n"

    def test_reset_uppercase_x(self):
        body = "- [X] 🔍 Trigger review\n"
        result = reset_checkboxes(body, ["🔍 Trigger review"])
        assert result == "- [ ] 🔍 Trigger review\n"

    def test_reset_preserves_other_content(self):
        body = "Some text\n- [x] 🔍 Trigger review\n\nMore text\n"
        result = reset_checkboxes(body, ["🔍 Trigger review"])
        assert "Some text" in result
        assert "More text" in result

    def test_reset_non_matching_label(self):
        body = "- [x] unknown label\n"
        result = reset_checkboxes(body, ["🔍 Trigger review"])
        # Should not modify unknown labels
        assert result == "- [x] unknown label\n"

    def test_reset_already_unchecked(self):
        body = "- [ ] 🔍 Trigger review\n"
        result = reset_checkboxes(body, ["🔍 Trigger review"])
        assert result == "- [ ] 🔍 Trigger review\n"


class TestResetAllCheckboxes:
    """Test reset_all_checkboxes."""

    def test_reset_all(self):
        body = (
            "- [x] 🔍 Trigger review\n"
            "- [x] 🛠 Fix issues\n"
            "- [x] 📸 ProofShot\n"
            "- [x] 🏷️ Relabel\n"
        )
        result = reset_all_checkboxes(body)
        assert "- [ ] 🔍 Trigger review" in result
        assert "- [ ] 🛠 Fix issues" in result
        assert "- [ ] 📸 ProofShot" in result
        assert "- [ ] 🏷️ Relabel" in result


class TestBuildCheckboxFooter:
    """Test build_checkbox_footer."""

    def test_default_unchecked(self):
        footer = build_checkbox_footer(["review", "fix"])
        assert "---" in footer
        assert "- [ ] 🔍 Trigger review" in footer
        assert "- [ ] 🛠 Fix issues" in footer

    def test_with_checked(self):
        footer = build_checkbox_footer(["review", "fix"], checked=["review"])
        assert "- [x] 🔍 Trigger review" in footer
        assert "- [ ] 🛠 Fix issues" in footer

    def test_empty_actions(self):
        footer = build_checkbox_footer([])
        assert footer.strip() == "---"

    def test_unknown_action_uses_key_as_label(self):
        footer = build_checkbox_footer(["unknown_action"])
        assert "- [ ] unknown_action" in footer


class TestBuildCommentWithFooter:
    """Test build_comment_with_footer."""

    def test_append_to_body_without_footer(self):
        body = "Review text here"
        result = build_comment_with_footer(body, ["review"])
        assert "Review text here" in result
        assert "- [ ] 🔍 Trigger review" in result

    def test_replace_existing_footer(self):
        body = "Review text\n\n---\n- [x] 🔍 Trigger review\n"
        result = build_comment_with_footer(body, ["review"])
        # Should have exactly one footer block
        count = result.count("🔍 Trigger review")
        assert count == 1

    def test_preserves_original_content(self):
        body = "## Review\n\nSome analysis\n\n---\n- [x] 🔍 Trigger review\n"
        result = build_comment_with_footer(body, ["review"])
        assert "## Review" in result
        assert "Some analysis" in result


class TestStripCheckboxFooter:
    """Test strip_checkbox_footer."""

    def test_strip_with_footer(self):
        body = "Review text\n\n---\n- [x] 🔍 Trigger review\n"
        result = strip_checkbox_footer(body)
        assert result.strip() == "Review text"

    def test_strip_without_footer(self):
        body = "Just review text"
        result = strip_checkbox_footer(body)
        assert result == "Just review text"

    def test_strip_preserves_content_before_separator(self):
        body = "Line 1\nLine 2\n\n---\n- [ ] 🔍 Trigger review\n"
        result = strip_checkbox_footer(body)
        assert "Line 1" in result
        assert "Line 2" in result
        assert "🔍" not in result


class TestExtractCommentEdit:
    """Test extract_comment_edit."""

    def test_valid_edited_comment(self):
        payload = {
            "action": "edited",
            "comment": {
                "id": 123,
                "body": "new body",
                "user": {"login": "testuser"},
            },
            "issue": {
                "number": 42,
                "pull_request": {"url": "..."},
                "user": {"login": "testuser"},
            },
            "changes": {"body": {"from": "old body"}},
            "repository": {"full_name": "owner/repo"},
        }
        result = extract_comment_edit(payload)
        assert result is not None
        assert result["body"] == "new body"
        assert result["old_body"] == "old body"
        assert result["comment_id"] == 123
        assert result["pr_number"] == 42

    def test_not_edited_action(self):
        payload = {"action": "created", "comment": {"body": "test"}}
        assert extract_comment_edit(payload) is None

    def test_not_a_pr(self):
        payload = {
            "action": "edited",
            "comment": {"id": 1, "body": "x", "user": {"login": "a"}},
            "issue": {"number": 1},  # No pull_request key
            "changes": {"body": {"from": "y"}},
            "repository": {"full_name": "owner/repo"},
        }
        assert extract_comment_edit(payload) is None

    def test_missing_body_change(self):
        payload = {
            "action": "edited",
            "comment": {"id": 1, "body": "", "user": {"login": "a"}},
            "issue": {"number": 1, "pull_request": {}},
            "changes": {},
            "repository": {"full_name": "owner/repo"},
        }
        # No body and no old body → None
        assert extract_comment_edit(payload) is None


class TestExtractPrBodyEdit:
    """Test extract_pr_body_edit."""

    def test_valid_pr_body_edit(self):
        payload = {
            "action": "edited",
            "pull_request": {
                "number": 42,
                "body": "new pr body",
                "user": {"login": "testuser"},
            },
            "changes": {"body": {"from": "old pr body"}},
            "repository": {"full_name": "owner/repo"},
        }
        result = extract_pr_body_edit(payload)
        assert result is not None
        assert result["body"] == "new pr body"
        assert result["old_body"] == "old pr body"
        assert result["pr_number"] == 42

    def test_not_edited_action(self):
        payload = {"action": "opened", "pull_request": {"number": 1}}
        assert extract_pr_body_edit(payload) is None

    def test_missing_pull_request(self):
        payload = {"action": "edited", "repository": {"full_name": "owner/repo"}}
        assert extract_pr_body_edit(payload) is None