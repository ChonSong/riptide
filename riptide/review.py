"""
review.py — LLM code review module for Riptide.

Adapts Octopus's review pipeline:
  - SYSTEM_PROMPT.md design (severity levels, finding categories)
  - Finding deduplication (review-dedup.ts logic)
  - Inline comment rendering (review-helpers.ts format)

Sits behind the webhook server; called by review_worker.py.
"""
import os
from typing import List, Tuple

OLLAMA_BASE  = os.environ.get("OLLAMA_BASE_URL",     "http://localhost:43311")
OLLAMA_EMBED = os.environ.get("OLLAMA_EMBED_MODEL",  "nomic-embed-text")
REVIEW_MODEL = os.environ.get("OLLAMA_REVIEW_MODEL", "qwen2.5-coder:7b")
RETRIEVE_TOP_K = int(os.environ.get("RETRIEVE_TOP_K", "8"))

from .embed import embed_query
import requests


def llm_review(prompt: str, model: str = REVIEW_MODEL) -> str:
    """Call Ollama /api/generate for code review."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 1536,
        }
    }
    try:
        resp = requests.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=300)
        if resp.status_code != 200:
            return f"[HTTP {resp.status_code}] {resp.text[:200]}"
        return resp.json().get("response", "[no response]")
    except requests.exceptions.Timeout:
        return "[timeout after 300s]"
    except Exception as e:
        return f"[error: {e}]"


def retrieve_context(diff_query: str, db_path: str, top_k: int = RETRIEVE_TOP_K) -> List[Tuple[str, str, float]]:
    """Top-K vector search using the numpy store."""
    from .store import search
    vec = embed_query(diff_query)
    if all(v == 0 for v in vec):
        return []
    return search(db_path, vec, top_k=top_k)


def build_prompt(diff_content: str, repo: str, pr_num: int,
                 context_results: List[Tuple[str, str, float]]) -> str:
    """
    Build the code-review prompt, adapted from Octopus SYSTEM_PROMPT.md.

    Severity levels (Octopus convention):
      🔴 Critical  — security, data loss, production crash
      🟠 High      — bug with easy repro, breaking API change
      🟡 Medium    — code smell, missing tests, error handling
      🔵 Low       — style, nits, minor perf

    Finding categories (Octopus):
      CORRECTNESS, SECURITY, PERFORMANCE, ERROR_HANDLING,
      BREAKING_CHANGE, TESTING, MAINTAINABILITY, OTHER
    """
    ctx_block = ""
    if context_results:
        ctx_lines = ["## Relevant codebase context:"]
        for i, (text, path, score) in enumerate(context_results, 1):
            ctx_lines.append(f"\n### [{i}] {path} (relevance={score:.2f})")
            ctx_lines.append(f"```\n{text[:800]}\n```")
        ctx_block = "\n".join(ctx_lines)
    else:
        ctx_block = "(no relevant context found — proceeding from diff only)"

    return f"""You are reviewing PR #{pr_num} in {repo}.

## Your role
You are Riptide, a careful code reviewer. You find bugs, security issues, and breaking changes. You do NOT approve PRs — you report findings to help the author.

## Severity levels (use exactly these)
- 🔴 Critical: Security vulnerability, data loss risk, production crash, auth bypass
- 🟠 High: Bug with clear reproduction path, breaking API change, unhandled error that will definitely fire
- 🟡 Medium: Code smell, missing null-check, error handling gap, unclear naming, missing tests
- 🔵 Low: Style nits, minor perf issue, verbose but correct code

## Finding categories
CORRECTNESS · SECURITY · PERFORMANCE · ERROR_HANDLING · BREAKING_CHANGE · TESTING · MAINTAINABILITY · OTHER

## Changed files diff
```diff
{diff_content[:10000]}
```

{ctx_block}

## Instructions
1. Review the diff carefully for bugs, security issues, and breaking changes
2. Check the retrieved context for relevant patterns that apply to this diff
3. If the change looks good, respond with: "## ✅ LGTM — no issues found"
4. Format findings as:

```
**Finding**: <one-line description>
**Severity**: <Critical/High/Medium/Low>
**Category**: <one category from the list above>
**Location**: <file path>:<line or "general">
**Suggestion**: <specific fix, preferrred>
```

5. Maximum 10 findings. Prioritise Critical > High > Medium. Skip style nits unless clearly misleading.
6. Use code block fences in suggestions when showing replacement code.
"""


def parse_review(response: str) -> dict:
    """
    Parse LLM review into structured findings.
    Adapted from Octopus review-dedup.ts parseFindingsFromJson.

    Handles two formats:
    1. Structured format (preferred): ```...``` blocks with **Finding**: markers
    2. Plain text: splits on lines starting with **Finding** or numbered bullets
    """
    import re

    findings = []
    current = {}

    # Split response into potential finding blocks
    # First try structured format
    lines = response.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("**Finding**"):
            if current:
                _finalise_finding(current, findings)
            current = {"raw": line.split("**Finding**", 1)[1].strip().lstrip(": ").rstrip(":"), "severity": "Medium", "category": "OTHER"}
        elif line.startswith("**Severity**"):
            sev = line.split("**Severity**", 1)[1].strip().lstrip(": ").rstrip(":").lower()
            if "critical" in sev: current["severity"] = "Critical"
            elif "high" in sev: current["severity"] = "High"
            elif "medium" in sev: current["severity"] = "Medium"
            elif "low" in sev: current["severity"] = "Low"
            else: current["severity"] = "Medium"
        elif line.startswith("**Category**"):
            current["category"] = line.split("**Category**", 1)[1].strip().lstrip(": ").rstrip(":").upper()
        elif line.startswith("**Location**"):
            current["location"] = line.split("**Location**", 1)[1].strip().lstrip(": ")
        elif line.startswith("**Suggestion**"):
            current["suggestion"] = line.split("**Suggestion**", 1)[1].strip().lstrip(": ")
            # Collect continuation lines for suggestion
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("**"):
                current["suggestion"] += "\n" + lines[i].strip()
                i += 1
            continue
        i += 1

    if current:
        _finalise_finding(current, findings)

    # Fallback: if no findings parsed, check for LGTM
    if not findings:
        if "lgtm" in response.lower() and "no issues" in response.lower():
            return {"raw": response, "findings": [], "summary": "LGTM — no issues found ✅"}
        # Last resort: return entire response as a single medium finding
        if len(response.strip()) > 50:
            return {
                "raw": response,
                "findings": [{
                    "finding": response[:200],
                    "severity": "Medium",
                    "category": "OTHER",
                    "location": "general",
                    "suggestion": "",
                }],
                "summary": f"1 finding (unstructured output)",
            }

    count = len(findings)
    high = sum(1 for f in findings if f["severity"] in ("Critical", "High"))

    return {
        "raw": response,
        "findings": findings,
        "summary": f"{count} finding{'s' if count != 1 else ''} " +
                   (f"({high} critical/high)" if high else "— looks good!"),
    }


def _finalise_finding(current: dict, findings: list):
    """Add a validated finding to the list, skip if too short."""
    text = current.get("raw", "").strip()
    if len(text) < 5:
        return
    finding = {
        "finding": text,
        "severity": current.get("severity", "Medium"),
        "category": current.get("category", "OTHER"),
        "location": current.get("location", "general"),
        "suggestion": current.get("suggestion", "").strip(),
    }
    findings.append(finding)


def format_comment(result: dict, repo: str, pr_num: int) -> str:
    """Format review result as a GitHub-flavoured Markdown comment."""
    findings = result.get("findings", [])
    if not findings:
        return f"## ✅ Riptide Review — {repo}#{pr_num}\n\nLooks good to me! No issues detected.\n"
    high = sum(1 for f in findings if f["severity"] in ("Critical", "High"))
    medium = sum(1 for f in findings if f["severity"] == "Medium")
    lines = [
        f"## 🔍 Riptide Code Review — {repo}#{pr_num}",
        f"**{len(findings)} finding{'s' if len(findings) != 1 else ''}**",
    ]
    if high:
        lines.append(f"- 🔴 Critical/High: **{high}**")
    if medium:
        lines.append(f"- 🟡 Medium: **{medium}**")
    lines.append("")
    for i, f in enumerate(findings[:10], 1):
        emoji = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🔵"}.get(f.get("severity", "Medium"), "🟡")
        cat = f.get("category", "OTHER")
        lines.append(f"{emoji} **{i}.** [{cat}] {f.get('finding', '')}")
        loc = f.get("location", "")
        if loc:
            lines.append(f"   📍 {loc}")
        sugg = f.get("suggestion", "")
        if sugg:
            lines.append(f"   💡 {sugg[:200]}")
    if len(findings) > 10:
        lines.append(f"_…and {len(findings) - 10} more_")
    lines.extend([
        "",
        "---",
        "_🤖 Riptide · local Ollama review_",
    ])
    return "\n".join(lines)
