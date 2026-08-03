#!/usr/bin/env python3
"""
gh_cli_client.py — GitHub API client using `gh` CLI (PAT-based).

Drop-in replacement for GitHubAppClient when the GitHub App is not
installed on a target repo.  Uses `gh api` which is authenticated via
PAT/GITHUB_TOKEN — works on any public repo the PAT can read, no app
installation required.

All methods mirror GitHubAppClient's interface so handlers (fixer.py,
webhook.py, deepthink.py) can use either client transparently.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Optional

log = logging.getLogger("riptide.gh_cli")


class GhCliClient:
    """`gh` CLI-backed GitHub API client.

    Methods accept an `installation_id` parameter for API compatibility
    but ignore it — `gh api` uses the configured PAT.
    """

    def __init__(self, base_url: str = "https://api.github.com"):
        self.base_url = base_url
        self._gh_available: Optional[bool] = None

    def _check_gh(self) -> bool:
        if self._gh_available is None:
            from shutil import which
            self._gh_available = which("gh") is not None
            if not self._gh_available:
                log.error("gh CLI not found in PATH")
        return self._gh_available

    def _gh_api(self, path: str, method: str = "GET",
                params: Optional[dict] = None,
                body: Optional[dict] = None) -> dict:
        """Call `gh api` and return parsed JSON."""
        if not self._check_gh():
            raise RuntimeError("gh CLI not available")

        cmd = ["gh", "api", path, "--method", method]
        if params:
            for k, v in params.items():
                cmd += ["-f", f"{k}={str(v)}"]
        input_file = None
        if body:
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                json.dump(body, f)
                cmd += ["--input", f.name]
                input_file = f.name

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            if input_file:
                os.unlink(input_file)
            if result.returncode != 0:
                raise RuntimeError(f"gh api failed: {result.stderr[:300]}")
            if not result.stdout.strip():
                return {}
            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            if input_file:
                os.unlink(input_file)
            raise RuntimeError("gh api timeout")

    # ── PR operations ──────────────────────────────────────────────────────

    def get_pr_details(self, installation_id, owner: str, repo: str,
                       pr_number: int) -> dict:
        return self._gh_api(f"repos/{owner}/{repo}/pulls/{pr_number}")

    def get_pr_files(self, installation_id, owner: str, repo: str,
                     pr_number: int) -> list[dict]:
        files = []
        page = 1
        while True:
            data = self._gh_api(
                f"repos/{owner}/{repo}/pulls/{pr_number}/files",
                params={"per_page": "100", "page": str(page)}
            )
            if not data:
                break
            files.extend(data)
            if len(data) < 100:
                break
            page += 1
        return files

    def compare_commits(self, installation_id, owner: str, repo: str,
                        base: str, head: str) -> dict:
        data = self._gh_api(f"repos/{owner}/{repo}/compare/{base}...{head}")
        return {
            "files": data.get("files", []),
            "commits": data.get("commits", []),
            "total_commits": data.get("total_commits", 0),
            "ahead_by": data.get("ahead_by", 0),
        }

    def post_pr_comment(self, installation_id, owner: str, repo: str,
                        pr_number: int, body: str) -> dict:
        return self._gh_api(
            f"repos/{owner}/{repo}/issues/{pr_number}/comments",
            method="POST",
            body={"body": body}
        )

    def post_inline_comment(self, installation_id, owner: str, repo: str,
                            pr_number: int, body: str, commit_id: str,
                            path: str, line: int, side: str = "RIGHT") -> dict:
        return self._gh_api(
            f"repos/{owner}/{repo}/pulls/{pr_number}/comments",
            method="POST",
            body={
                "body": body,
                "commit_id": commit_id,
                "path": path,
                "line": line,
                "side": side,
            }
        )

    # ── Installation (not applicable) ──────────────────────────────────────

    def get_installation_repos(self, installation_id) -> list[dict]:
        # gh CLI cannot list installations; return empty
        return []

    # ── Check runs ──────────────────────────────────────────────────────────

    def create_check_run(self, installation_id, owner: str, repo: str,
                         name: str, head_sha: str, status: str = "in_progress",
                         conclusion: Optional[str] = None,
                         output: Optional[dict] = None) -> dict:
        payload: dict = {
            "name": name,
            "head_sha": head_sha,
            "status": status,
        }
        if conclusion is not None and output is not None:
            payload["conclusion"] = conclusion
            payload["output"] = output
        return self._gh_api(
            f"repos/{owner}/{repo}/check-runs",
            method="POST",
            body=payload
        )

    def update_check_run(self, installation_id, owner: str, repo: str,
                         check_run_id: int, conclusion: str,
                         output: dict) -> dict:
        return self._gh_api(
            f"repos/{owner}/{repo}/check-runs/{check_run_id}",
            method="PATCH",
            body={
                "status": "completed",
                "conclusion": conclusion,
                "output": output,
            }
        )

    # ── Reactions ───────────────────────────────────────────────────────────

    def add_comment_reaction(self, installation_id, owner: str, repo: str,
                             comment_id: int, reaction: str = "eyes") -> dict:
        return self._gh_api(
            f"repos/{owner}/{repo}/issues/comments/{comment_id}/reactions",
            method="POST",
            body={"content": reaction}
        )


def make_gh_cli_client() -> Optional[GhCliClient]:
    """Factory: create a gh CLI client if gh is available."""
    from shutil import which
    if which("gh"):
        return GhCliClient()
    return None
