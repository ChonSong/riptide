#!/usr/bin/env python3
"""
github_app.py — GitHub App JWT auth and API client.

Uses the existing octopus-selfhost private key (GITHUB_APP_ID=4262983)
to generate installation tokens for GitHub API calls.

No `gh` CLI dependency — pure JWT + requests.
"""
import os, json, time, base64, hashlib, hmac, struct, textwrap
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional
import requests

# ── JWT helpers ────────────────────────────────────────────────────────────────

def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def jwt_token(app_id: int, private_key_path: str, expiry_seconds: int = 600) -> str:
    """
    Generate a GitHub App JWT.
    https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generate-a-jwt-for-a-github-app
    """
    with open(private_key_path) as f:
        pem = f.read()

    # Parse RSA PEM
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa, padding as asym_padding
        from cryptography.hazmat.backends import default_backend
        private_key = serialization.load_pem_private_key(
            pem.encode(), password=None, backend=default_backend()
        )
    except ImportError:
        raise RuntimeError(
            "cryptography is required: pip install cryptography\n"
            "Already in Hermes venv at: /home/sc/.hermes/hermes-agent/venv/bin/python"
        )

    now = int(time.time())
    payload = {
        "iss": str(app_id),
        "iat": now,
        "exp": now + expiry_seconds,
    }
    # JWS header
    header = {"alg": "RS256", "typ": "JWT"}
    header_b64 = base64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = base64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    message = f"{header_b64}.{payload_b64}"

    sig = private_key.sign(
        message.encode(),
        padding=asym_padding.PKCS1v15(),
        algorithm=hashes.SHA256(),
    )
    sig_b64 = base64url_encode(sig)
    return f"{message}.{sig_b64}"


# ── Token cache ───────────────────────────────────────────────────────────────

class InstallationTokenCache:
    """
    Caches installation tokens. GitHub App tokens expire after 1 hour.
    We refresh at 55 minutes to be safe.
    """

    def __init__(self, app_id: int, private_key_path: str):
        self.app_id = app_id
        self.private_key_path = private_key_path
        self._cache: dict[int, tuple[str, float]] = {}  # installation_id → (token, expiry)

    def get_token(self, installation_id: int) -> str:
        now = time.time()
        token, expires_at = self._cache.get(installation_id, (None, 0))
        if token and expires_at > now + 300:  # refresh if < 5 min left
            return token

        # Generate fresh JWT and exchange for installation token
        jwt = jwt_token(self.app_id, self.private_key_path)
        resp = requests.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15,
        )
        if resp.status_code == 403:
            raise PermissionError(
                f"GitHub App installation {installation_id} access denied. "
                "Is the app installed on the repository?"
            )
        resp.raise_for_status()
        data = resp.json()
        token = data["token"]
        # Tokens are valid for 1 hour; cache with 55 min safety margin
        self._cache[installation_id] = (token, now + 3300)
        return token


# ── GitHub API client ──────────────────────────────────────────────────────────

class GitHubAppClient:
    """
    Authenticated GitHub API client using GitHub App installation tokens.
    All methods accept installation_id and handle token refresh automatically.
    """

    def __init__(self, app_id: int, private_key_path: str, base_url: str = "https://api.github.com"):
        self.base_url = base_url
        self._token_cache = InstallationTokenCache(app_id, private_key_path)

    def _headers(self, installation_id: int, extra: dict = None) -> dict:
        token = self._token_cache.get_token(installation_id)
        h = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if extra:
            h.update(extra)
        return h

    def compare_commits(
        self, installation_id: int, owner: str, repo: str, base: str, head: str
    ) -> dict:
        """Compare two commits and return files changed + commit metadata.

        Uses GitHub's compare API: GET /repos/{owner}/{repo}/compare/{base}...{head}
        Returns {"files": [...], "commits": [...], "total_commits": int, "ahead_by": int}
        """
        resp = requests.get(
            f"{self.base_url}/repos/{owner}/{repo}/compare/{base}...{head}",
            headers=self._headers(installation_id),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "files": data.get("files", []),
            "commits": data.get("commits", []),
            "total_commits": data.get("total_commits", 0),
            "ahead_by": data.get("ahead_by", 0),
        }

    def get_pr_files(self, installation_id: int, owner: str, repo: str, pr_number: int) -> list[dict]:
        """Fetch all files changed in a PR."""
        files = []
        page = 1
        while True:
            resp = requests.get(
                f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}/files",
                headers=self._headers(installation_id),
                params={"per_page": 100, "page": page},
                timeout=30,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            files.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return files

    def get_pr_details(self, installation_id: int, owner: str, repo: str, pr_number: int) -> dict:
        """Fetch PR metadata."""
        resp = requests.get(
            f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}",
            headers=self._headers(installation_id),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def post_pr_comment(self, installation_id: int, owner: str, repo: str, pr_number: int, body: str) -> dict:
        """Post a PR-level comment (not inline). Uses issues endpoint for top-level comments."""
        resp = requests.post(
            f"{self.base_url}/repos/{owner}/{repo}/issues/{pr_number}/comments",
            headers=self._headers(installation_id, {"Content-Type": "application/json"}),
            json={"body": body},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def update_pr_comment(self, installation_id: int, owner: str, repo: str, comment_id: int, body: str) -> dict:
        """Update (PATCH) an existing PR comment in place. Used for two-tier response enrichment."""
        resp = requests.patch(
            f"{self.base_url}/repos/{owner}/{repo}/issues/comments/{comment_id}",
            headers=self._headers(installation_id, {"Content-Type": "application/json"}),
            json={"body": body},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_comment_body(self, installation_id: int, owner: str, repo: str, comment_id: int) -> str:
        """Fetch the current body of a PR comment. Used to preserve checkbox state during enrichment."""
        resp = requests.get(
            f"{self.base_url}/repos/{owner}/{repo}/issues/comments/{comment_id}",
            headers=self._headers(installation_id),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("body", "")

    def post_inline_comment(
        self,
        installation_id: int,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        commit_id: str,
        path: str,
        line: int,
        side: str = "RIGHT",
    ) -> dict:
        """Post an inline comment on a specific file:line."""
        resp = requests.post(
            f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}/comments",
            headers=self._headers(installation_id, {"Content-Type": "application/json"}),
            json={
                "body": body,
                "commit_id": commit_id,
                "path": path,
                "line": line,
                "side": side,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def create_check_run(
        self,
        installation_id: int,
        owner: str,
        repo: str,
        name: str,
        head_sha: str,
        status: str = "in_progress",
        conclusion: Optional[str] = None,
        output: Optional[dict] = None,
    ) -> dict:
        """Create a check run (GitHub CI integration)."""
        payload: dict = {
            "name": name,
            "head_sha": head_sha,
            "status": status,
        }
        if conclusion and output:
            payload["conclusion"] = conclusion
            payload["output"] = output
        resp = requests.post(
            f"{self.base_url}/repos/{owner}/{repo}/check-runs",
            headers=self._headers(installation_id, {"Content-Type": "application/json"}),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def update_check_run(
        self,
        installation_id: int,
        owner: str,
        repo: str,
        check_run_id: int,
        conclusion: str,
        output: dict,
    ) -> dict:
        """Update a check run with results."""
        resp = requests.patch(
            f"{self.base_url}/repos/{owner}/{repo}/check-runs/{check_run_id}",
            headers=self._headers(installation_id, {"Content-Type": "application/json"}),
            json={"status": "completed", "conclusion": conclusion, "output": output},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def add_comment_reaction(self, installation_id: int, owner: str, repo: str, comment_id: int, reaction: str = "eyes") -> dict:
        """Add a reaction to a comment."""
        resp = requests.post(
            f"{self.base_url}/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions",
            headers=self._headers(installation_id, {"Content-Type": "application/json"}),
            json={"content": reaction},
            timeout=10,
        )
        # 201 = created, 200 = already exists — both fine
        if resp.status_code == 200:
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    def ensure_label(self, installation_id: int, owner: str, repo: str,
                     name: str, description: str, color: str) -> dict:
        """Create a label if it doesn't exist, update if it does."""
        from urllib.parse import quote
        encoded_name = quote(name, safe="")
        resp = requests.get(
            f"{self.base_url}/repos/{owner}/{repo}/labels/{encoded_name}",
            headers=self._headers(installation_id),
            timeout=15,
        )
        if resp.status_code == 200:
            # Label exists — update description/color
            resp = requests.patch(
                f"{self.base_url}/repos/{owner}/{repo}/labels/{encoded_name}",
                headers=self._headers(installation_id, {"Content-Type": "application/json"}),
                json={"description": description, "color": color},
                timeout=15,
            )
        elif resp.status_code == 404:
            # Create label
            resp = requests.post(
                f"{self.base_url}/repos/{owner}/{repo}/labels",
                headers=self._headers(installation_id, {"Content-Type": "application/json"}),
                json={"name": name, "description": description, "color": color},
                timeout=15,
            )
        resp.raise_for_status()
        return resp.json()

    def add_labels_to_issue(self, installation_id: int, owner: str, repo: str,
                            issue_number: int, labels: list[str]) -> dict:
        """Add labels to an issue/PR."""
        resp = requests.post(
            f"{self.base_url}/repos/{owner}/{repo}/issues/{issue_number}/labels",
            headers=self._headers(installation_id, {"Content-Type": "application/json"}),
            json={"labels": labels},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_issue_comments(self, installation_id: int, owner: str, repo: str,
                           issue_number: int) -> list[dict]:
        """Fetch all comments on an issue/PR."""
        comments = []
        page = 1
        while True:
            resp = requests.get(
                f"{self.base_url}/repos/{owner}/{repo}/issues/{issue_number}/comments",
                headers=self._headers(installation_id),
                params={"per_page": 100, "page": page},
                timeout=30,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            comments.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return comments

    def remove_label_from_issue(self, installation_id: int, owner: str, repo: str,
                                issue_number: int, label: str) -> dict:
        """Remove a label from an issue/PR."""
        from urllib.parse import quote
        encoded_label = quote(label, safe="")
        resp = requests.delete(
            f"{self.base_url}/repos/{owner}/{repo}/issues/{issue_number}/labels/{encoded_label}",
            headers=self._headers(installation_id),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_installation_repos(self, installation_id: int) -> list[dict]:
        """List all repos the installation has access to."""
        repos = []
        page = 1
        while True:
            resp = requests.get(
                f"{self.base_url}/installation/repositories",
                headers=self._headers(installation_id),
                params={"per_page": 100, "page": page},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            repos.extend(data.get("repositories", []))
            if not data.get("repositories") or len(data.get("repositories", [])) < 100:
                break
            page += 1
        return repos


# ── Webhook signature verification ─────────────────────────────────────────────

def verify_webhook_signature(
    payload_bytes: bytes,
    signature: str,
    secret: str,
) -> bool:
    """
    Verify GitHub webhook X-Hub-Signature-256 header.
    secret = WEBHOOK_SECRET env var.
    """
    if not signature or not secret:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), payload_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Entry point for testing ────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    app_id = int(os.environ.get("GITHUB_APP_ID", 0))
    key_path = os.environ.get("GITHUB_PRIVATE_KEY_PATH", "")
    if not app_id or not key_path:
        print("Set GITHUB_APP_ID and GITHUB_PRIVATE_KEY_PATH")
        sys.exit(1)

    client = GitHubAppClient(app_id, key_path)
    print(f"JWT test: {jwt_token(app_id, key_path)[:20]}...")

    # Try to get installation token for a known installation
    # You'll need to provide a real installation_id
    if len(sys.argv) > 1:
        inst_id = int(sys.argv[1])
        token = client._token_cache.get_token(inst_id)
        print(f"Installation token: {token[:20]}...")
