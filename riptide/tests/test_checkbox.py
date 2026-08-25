#!/usr/bin/env python3
"""
Tests for riptide/checkbox.py — interactive checkbox button system.

Covers parsing, toggles, reset, footer generation, and webhook payload extraction.
"""

import pytest

from riptide.checkbox import (
    CHECKBOX_RE,
    CHECKBOX_ACTIONS,
    ACTION_LABELS,
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


# ── Regex ─────────────────────────────────────────────────────────────────────


class TestCheckboxRegex:
    """Verify CHECKBOX_RE matches valid checkbox lines."""

    def test_unchecked(self):
        m = CHECKBOX_RE.match("- [ ] 🔍 Trigger review")
        assert m is not None
        assert m.group(1) == " "
        assert m.group(2) == "🔍 Trigger review"

    def test_checked_lowercase(self):
        m = CHECKBOX_RE.match("- [x] 🛠 Fix issues")
        assert m.group(1) == "x"
        assert m.group(2) == "🛠 Fix issues"

    def test_checked_uppercase(self):
        m = CHECKBOX_RE.match("- [X] 📸 ProofShot")
        assert m.group(1) == "X"
        assert m.group(2) == "📸 ProofShot"

    def test_no_match_non_checkbox(self):
        assert CHECKBOX_RE.match("Regular text line") is None

    def test_no_match_empty_line(self):
        assert CHECKBOX_RE.match("") is None

    def test_no_match_partial(self):
        assert CHECKBOX_RE.match("- [ ]") is None

    def test_multiple_checkboxes(self):
        body = "- [ ] 🔍 Trigger review\n- [x] 🛠 Fix issues\n- [ ] 📸 ProofShot"
        matches = list(CHECKBOX_RE.finditer(body))
        assert len(matches) == 3
        assert matches[0].group(2) == "🔍 Trigger review"
        assert matches[1].group(2) == "🛠 Fix issues"
        assert matches[2].group(2) == "📸 ProofShot"


# ── Checkbox state parsing ───────────────────────────────────────────────────


class TestParseCheckboxState:
    """Verify parse_checkbox_state extracts all checkboxes from a body."""

    def test_empty_body(self):
        assert parse_checkbox_state("") == {}

    def test_no_checkboxes(self):
        assert parse_checkbox_state("Just some text\nNo boxes here") == {}

    def test_single_unchecked(self):
        result = parse_checkbox_state("- [ ] 🔍 Trigger review")
        assert result == {"🔍 Trigger review": False}

    def test_single_checked(self):
        result = parse_checkbox_state("- [x] 🔍 Trigger review")
        assert result == {"🔍 Trigger review": True}

    def test_mixed(self):
        body = "- [ ] 🔍 Trigger review\n- [x] 🛠 Fix issues\n- [X] 📸 ProofShot"
        result = parse_checkbox_state(body)
        assert result == {
            "🔍 Trigger review": False,
            "🛠 Fix issues": True,
            "📸 ProofShot": True,
        }

    def test_ignores_non_checkbox_lines(self):
        body = "## Header\n- [ ] 🔍 Trigger review\nSome text\n- [x] 🛠 Fix issues"
        result = parse_checkbox_state(body)
        assert result == {
            "🔍 Trigger review": False,
            "🛠 Fix issues": True,
        }


# ── Toggle detection ─────────────────────────────────────────────────────────


class TestParseCheckboxToggles:
    """Verify parse_checkbox_toggles detects [ ] → [x] transitions."""

    def test_no_old_body(self):
        """When old_body is None, all checked boxes are considered toggled."""
        result = parse_checkbox_toggles(None, "- [x] 🔍 Trigger review")
        assert result == ["🔍 Trigger review"]

    def test_single_toggle(self):
        old = "- [ ] 🔍 Trigger review\n- [ ] 🛠 Fix issues"
        new = "- [x] 🔍 Trigger review\n- [ ] 🛠 Fix issues"
        result = parse_checkbox_toggles(old, new)
        assert result == ["🔍 Trigger review"]

    def test_multiple_toggles(self):
        old = "- [ ] 🔍 Trigger review\n- [ ] 🛠 Fix issues\n- [ ] 📸 ProofShot"
        new = "- [x] 🔍 Trigger review\n- [x] 🛠 Fix issues\n- [ ] 📸 ProofShot"
        result = parse_checkbox_toggles(old, new)
        assert result == ["🔍 Trigger review", "🛠 Fix issues"]

    def test_no_toggle_when_already_checked(self):
        old = "- [x] 🔍 Trigger review"
        new = "- [x] 🔍 Trigger review"
        result = parse_checkbox_toggles(old, new)
        assert result == []

    def test_no_toggle_on_uncheck(self):
        """Unchecking should NOT trigger action."""
        old = "- [x] 🔍 Trigger review"
        new = "- [ ] 🔍 Trigger review"
        result = parse_checkbox_toggles(old, new)
        assert result == []

    def test_no_toggle_when_unchanged(self):
        old = "- [ ] 🔍 Trigger review\n- [ ] 🛠 Fix issues"
        new = "- [ ] 🔍 Trigger review\n- [ ] 🛠 Fix issues"
        result = parse_checkbox_toggles(old, new)
        assert result == []

    def test_text_edit_without_toggle(self):
        """Editing other text in the comment should not trigger."""
        old = "## TL;DR\nSome text\n- [ ] 🔍 Trigger review"
        new = "## TL;DR\nEdited text\n- [ ] 🔍 Trigger review"
        result = parse_checkbox_toggles(old, new)
        assert result == []

    def test_empty_bodies(self):
        assert parse_checkbox_toggles("", "") == []
        assert parse_checkbox_toggles(None, "") == []


class TestParseCheckboxUnchecks:
    """Verify parse_checkbox_unchecks detects [x] → [ ] transitions."""

    def test_single_uncheck(self):
        old = "- [x] 🔍 Trigger review"
        new = "- [ ] 🔍 Trigger review"
        result = parse_checkbox_unchecks(old, new)
        assert result == ["🔍 Trigger review"]

    def test_no_uncheck_when_checked(self):
        old = "- [ ] 🔍 Trigger review"
        new = "- [x] 🔍 Trigger review"
        result = parse_checkbox_unchecks(old, new)
        assert result == []


# ── Reset ─────────────────────────────────────────────────────────────────────


class TestResetCheckboxes:
    """Verify reset_checkboxes unchecks specified boxes."""

    def test_reset_single(self):
        body = "- [x] 🔍 Trigger review"
        result = reset_checkboxes(body, ["🔍 Trigger review"])
        assert result == "- [ ] 🔍 Trigger review"

    def test_reset_multiple(self):
        body = "- [x] 🔍 Trigger review\n- [X] 🛠 Fix issues\n- [ ] 📸 ProofShot"
        result = reset_checkboxes(body, ["🔍 Trigger review", "🛠 Fix issues"])
        assert result == "- [ ] 🔍 Trigger review\n- [ ] 🛠 Fix issues\n- [ ] 📸 ProofShot"

    def test_reset_nonexistent_label(self):
        """Labels not in body are silently ignored."""
        body = "- [x] 🔍 Trigger review"
        result = reset_checkboxes(body, ["Nonexistent"])
        assert result == "- [x] 🔍 Trigger review"

    def test_reset_empty_list(self):
        body = "- [x] 🔍 Trigger review"
        result = reset_checkboxes(body, [])
        assert result == "- [x] 🔍 Trigger review"


class TestResetAllCheckboxes:
    """Verify reset_all_checkboxes unchecks all boxes."""

    def test_reset_all(self):
        body = "- [x] 🔍 Trigger review\n- [X] 🛠 Fix issues\n- [ ] 📸 ProofShot"
        result = reset_all_checkboxes(body)
        assert result == "- [ ] 🔍 Trigger review\n- [ ] 🛠 Fix issues\n- [ ] 📸 ProofShot"

    def test_no_checkboxes(self):
        body = "Just text"
        assert reset_all_checkboxes(body) == "Just text"


# ── Footer generation ─────────────────────────────────────────────────────────


class TestBuildCheckboxFooter:
    """Verify build_checkbox_footer generates correct markdown."""

    def test_default_all_actions(self):
        result = build_checkbox_footer()
        assert "- [ ] 🔍 Trigger review" in result
        assert "- [ ] 🛠 Fix issues" in result
        assert "- [ ] 📸 ProofShot" in result
        assert "- [ ] 🏷️ Relabel" in result

    def test_subset_of_actions(self):
        result = build_checkbox_footer(actions=["review", "fix"])
        assert "- [ ] 🔍 Trigger review" in result
        assert "- [ ] 🛠 Fix issues" in result
        assert "📸 ProofShot" not in result

    def test_with_checked(self):
        result = build_checkbox_footer(actions=["review", "fix"], checked=["review"])
        assert "- [x] 🔍 Trigger review" in result
        assert "- [ ] 🛠 Fix issues" in result

    def test_empty_actions(self):
        result = build_checkbox_footer(actions=[])
        assert result == ""


class TestBuildCommentWithFooter:
    """Verify build_comment_with_footer appends footer correctly."""

    def test_append_to_simple_body(self):
        body = "## TL;DR\nSome review text"
        result = build_comment_with_footer(body, actions=["review"])
        assert "## TL;DR\nSome review text" in result
        assert "---" in result
        assert "- [ ] 🔍 Trigger review" in result

    def test_replace_existing_footer(self):
        body = "## TL;DR\nText\n\n---\n- [x] 🔍 Trigger review"
        result = build_comment_with_footer(body, actions=["review"])
        # Should have exactly one checkbox block
        assert result.count("- [ ] 🔍 Trigger review") == 1
        assert result.count("---") == 1

    def test_no_duplicate_separator(self):
        body = "## TL;DR\nText"
        result = build_comment_with_footer(body, actions=["review"])
        assert result.count("---") == 1


class TestStripCheckboxFooter:
    """Verify strip_checkbox_footer removes footer correctly."""

    def test_strip_with_separator(self):
        body = "## TL;DR\nText\n\n---\n- [ ] 🔍 Trigger review"
        result = strip_checkbox_footer(body)
        assert result == "## TL;DR\nText"

    def test_strip_without_separator(self):
        body = "## TL;DR\nText\n- [ ] 🔍 Trigger review"
        result = strip_checkbox_footer(body)
        assert result == "## TL;DR\nText"

    def test_no_footer_to_strip(self):
        body = "## TL;DR\nText"
        result = strip_checkbox_footer(body)
        assert result == "## TL;DR\nText"


# ── Webhook payload extraction ────────────────────────────────────────────────


class TestExtractCommentEdit:
    """Verify extract_comment_edit parses webhook payloads correctly."""

    def test_valid_edit_payload(self):
        payload = {
            "action": "edited",
            "comment": {
                "id": 12345,
                "body": "- [x] 🔍 Trigger review",
                "user": {"login": "sean", "type": "User"},
            },
            "changes": {"body": {"from": "- [ ] 🔍 Trigger review"}},
            "issue": {"number": 456, "pull_request": {}},
            "repository": {"full_name": "ChonSong/riptide", "name": "riptide"},
            "installation": {"id": 789},
        }
        result = extract_comment_edit(payload)
        assert result is not None
        assert result["comment_id"] == 12345
        assert result["old_body"] == "- [ ] 🔍 Trigger review"
        assert result["new_body"] == "- [x] 🔍 Trigger review"
        assert result["commenter"] == "sean"
        assert result["is_bot"] is False
        assert result["pr_number"] == 456
        assert result["owner"] == "ChonSong"
        assert result["repo"] == "riptide"
        assert result["installation_id"] == 789

    def test_non_edit_action(self):
        payload = {"action": "created", "comment": {}}
        assert extract_comment_edit(payload) is None

    def test_not_a_pr_comment(self):
        payload = {
            "action": "edited",
            "comment": {"body": "- [x] 🔍 Trigger review"},
            "changes": {"body": {"from": "- [ ] 🔍 Trigger review"}},
            "issue": {"number": 456},  # No pull_request key
            "repository": {"full_name": "ChonSong/riptide"},
            "installation": {"id": 789},
        }
        assert extract_comment_edit(payload) is None

    def test_no_body_change(self):
        payload = {
            "action": "edited",
            "comment": {"body": "- [ ] 🔍 Trigger review"},
            "changes": {},  # No body change
            "issue": {"number": 456, "pull_request": {}},
            "repository": {"full_name": "ChonSong/riptide"},
            "installation": {"id": 789},
        }
        assert extract_comment_edit(payload) is None

    def test_bot_user(self):
        payload = {
            "action": "edited",
            "comment": {
                "id": 12345,
                "body": "- [x] 🔍 Trigger review",
                "user": {"login": "riptide-bot", "type": "Bot"},
            },
            "changes": {"body": {"from": "- [ ] 🔍 Trigger review"}},
            "issue": {"number": 456, "pull_request": {}},
            "repository": {"full_name": "ChonSong/riptide", "name": "riptide"},
            "installation": {"id": 789},
        }
        result = extract_comment_edit(payload)
        assert result is not None
        assert result["is_bot"] is True


class TestExtractPrBodyEdit:
    """Verify extract_pr_body_edit parses PR body edit payloads."""

    def test_valid_pr_body_edit(self):
        payload = {
            "action": "edited",
            "pull_request": {
                "number": 456,
                "body": "- [x] 🔍 Trigger review",
            },
            "changes": {"body": {"from": "- [ ] 🔍 Trigger review"}},
            "repository": {"full_name": "ChonSong/riptide", "name": "riptide"},
            "installation": {"id": 789},
        }
        result = extract_pr_body_edit(payload)
        assert result is not None
        assert result["pr_number"] == 456
        assert result["old_body"] == "- [ ] 🔍 Trigger review"
        assert result["new_body"] == "- [x] 🔍 Trigger review"

    def test_non_edit_action(self):
        payload = {"action": "opened"}
        assert extract_pr_body_edit(payload) is None

    def test_no_body_change(self):
        payload = {
            "action": "edited",
            "pull_request": {"number": 456, "body": "Some text"},
            "changes": {},  # No body change
            "repository": {"full_name": "ChonSong/riptide"},
            "installation": {"id": 789},
        }
        assert extract_pr_body_edit(payload) is None


# ── Taxonomy ─────────────────────────────────────────────────────────────────


class TestCheckboxTaxonomy:
    """Verify the checkbox taxonomy is consistent."""

    def test_all_actions_have_labels(self):
        for action in CHECKBOX_ACTIONS.values():
            assert action in ACTION_LABELS

    def test_all_labels_have_actions(self):
        for label in CHECKBOX_ACTIONS:
            assert label in ACTION_LABELS.values()

    def test_review_action(self):
        assert CHECKBOX_ACTIONS["🔍 Trigger review"] == "review"

    def test_fix_action(self):
        assert CHECKBOX_ACTIONS["🛠 Fix issues"] == "fix"

    def test_visual_action(self):
        assert CHECKBOX_ACTIONS["📸 ProofShot"] == "visual"

    def test_relabel_action(self):
        assert CHECKBOX_ACTIONS["🏷️ Relabel"] == "relabel"
