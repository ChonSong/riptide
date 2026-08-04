"""Tests for riptide/deepthink.py — diagram promise removal in trigger confirmation.

The deterministic review pipeline intentionally dropped Excalidraw diagram
generation (the review prompt has no diagram subagent and the assembler is
called without --diagram-url). The trigger confirmation must NOT promise a
diagram the assembled review never includes.
"""

from unittest.mock import patch, MagicMock

from riptide.deepthink import handle_review_command, _build_orchestrator_prompt


class TestReviewConfirmationNoDiagramPromise:
    def test_confirmation_does_not_promise_diagram(self):
        client = MagicMock()
        client.get_pr_details.return_value = {
            "title": "feat: test",
            "user": {"login": "test-author"},
            "additions": 100,
            "deletions": 50,
            "head": {"sha": "abc123def456"},
        }

        with patch("riptide.deepthink._spawn_deepthink", return_value=True):
            msg = handle_review_command(client, 123, "ChonSong", "riptide", 46, "test-user")

        assert msg is not None
        assert "Excalidraw" not in msg
        assert "diagram" not in msg
        assert "Riptide Review triggered" in msg
        assert "inline suggestions" in msg

    def test_orchestrator_prompt_has_no_diagram_steps(self):
        data = {
            "files_changed": [{"filename": "a.py", "additions": 10, "deletions": 2}],
            "diff_raw": "+ line\n",
            "repo_tree": ["a.py"],
            "god_nodes": [],
            "communities": [],
            "graph_context": {},
        }
        prompt = _build_orchestrator_prompt(
            owner="ChonSong",
            repo="riptide",
            pr_number=46,
            pr_title="feat: test",
            pr_author="test-author",
            total_loc=12,
            head_sha="abc123def456",
            data=data,
        )

        assert "Excalidraw" not in prompt
        assert "--diagram-url" not in prompt
        # Deterministic assembly is still instructed
        assert "assemble_review" in prompt
