"""
Review pipeline: data collection, template rendering, and post-generation validation.

Hybrid design:
- Templates ensure required sections are always present
- DeepThink provides reasoning about the data (not data gathering)
- Validation catches missing/empty sections before posting
"""

import subprocess
import re
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


# ── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class CodeChunk:
    """A code block from the PR diff with context."""
    file: str
    line_start: int
    line_end: int
    content: str
    change_type: str  # "added", "removed", "context"
    why: str = ""  # filled by deepthink


@dataclass
class ReviewContext:
    """All data needed for a PR review — gathered by T0, consumed by template + deepthink."""
    # PR identity
    pr_number: int
    title: str
    author: str
    owner: str
    repo: str
    head_sha: str
    base_sha: str = ""
    
    # PR scope
    files_changed: list = field(default_factory=list)
    total_loc: int = 0
    total_additions: int = 0
    total_deletions: int = 0
    
    # Repository context
    repo_tree: list = field(default_factory=list)
    
    # Code analysis
    code_chunks: list = field(default_factory=list)
    diff_raw: str = ""
    
    # Graphify context
    graph_context: dict = field(default_factory=dict)
    god_nodes: list = field(default_factory=list)
    communities: list = field(default_factory=list)
    
    # Classification
    mood_emoji: str = "✨"
    gif_url: str = ""


# ── Review Classification ────────────────────────────────────────────────────


class ReviewDepth(Enum):
    """Determines how much LLM analysis a PR needs."""
    TRIVIAL = "trivial"         # <10 LOC, no logic changes → auto-approve
    INLINE_ONLY = "inline_only" # Single file, <50 LOC → minimal review
    STANDARD = "standard"       # Normal PR → full review
    ARCH = "arch"              # Multi-file, >200 LOC, high graphify impact → +brooks-lint


def classify_review_depth(data: dict) -> ReviewDepth:
    """
    Rule-based classification of PR depth from pre-gathered data.

    Args:
        data: Output from _gather_review_data() with god_nodes, communities, files_changed, etc.

    Returns:
        ReviewDepth enum value
    """
    total_loc = sum(
        f.get("additions", 0) + f.get("deletions", 0) for f in data.get("files_changed", [])
    )
    files_changed = data.get("files_changed", [])
    god_nodes = data.get("god_nodes", [])

    # TRIVIAL: tiny change, no logic files
    logic_extensions = ('.py', '.js', '.ts', '.go', '.rs', '.java', '.c', '.cpp', '.h')
    has_logic = any(
        any(f.get("filename", "").endswith(ext) for ext in logic_extensions)
        for f in files_changed
    )
    if total_loc < 10 and not has_logic:
        return ReviewDepth.TRIVIAL

    # INLINE_ONLY: single file, small change
    if len(files_changed) == 1 and total_loc < 50:
        return ReviewDepth.INLINE_ONLY

    # ARCH: multi-file OR large OR touches high-impact god nodes
    if len(files_changed) > 5 or total_loc > 200:
        if any(g.get("edges", 0) > 20 for g in god_nodes):
            return ReviewDepth.ARCH

    return ReviewDepth.STANDARD


def select_skills(depth: ReviewDepth) -> list[str]:
    """
    Select which skills to load based on review depth.

    Args:
        depth: ReviewDepth classification

    Returns:
        List of skill names to pass as --skill flags
    """
    if depth == ReviewDepth.TRIVIAL:
        return []
    elif depth == ReviewDepth.INLINE_ONLY:
        return ["deep-think", "github-pr-lifecycle"]
    elif depth == ReviewDepth.STANDARD:
        return ["deep-think", "github-pr-lifecycle", "excalidraw"]
    elif depth == ReviewDepth.ARCH:
        return ["deep-think", "github-pr-lifecycle", "excalidraw", "brooks-lint"]
    return ["deep-think"]


# ── Data Collection ─────────────────────────────────────────────────────────

def collect_repo_tree(owner: str, repo: str, ref: str = "HEAD") -> list[str]:
    """
    Get a directory tree of the repo at a given ref.
    Uses git ls-files for speed (no checkout needed).
    """
    try:
        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", ref],
            capture_output=True, text=True, timeout=30,
            cwd=f"/home/sc/workspace/{repo}",
        )
        if result.returncode == 0:
            return result.stdout.strip().split("\n")
    except Exception:
        pass
    
    # Fallback: use GitHub API
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/git/trees/{ref}?recursive=1",
             "--jq", ".tree[].path"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip().split("\n")
    except Exception:
        pass
    
    return []


def collect_code_chunks(diff_text: str) -> list[CodeChunk]:
    """
    Parse a PR diff into CodeChunks with file, line range, and change type.
    
    Each hunk header @@ -old_start,old_count +new_start,new_count @@ gives us
    the line numbers. We track added/removed/context lines per file.
    """
    chunks = []
    current_file = ""
    old_line = 0
    new_line = 0
    
    for line in diff_text.split("\n"):
        # File header
        if line.startswith("diff --git"):
            # Extract file path from: diff --git a/path b/path
            match = re.match(r"diff --git a/(.+?) b/(.+)", line)
            if match:
                current_file = match.group(2)
            continue
        
        # Hunk header
        if line.startswith("@@"):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if match:
                old_line = int(match.group(1))
                new_line = int(match.group(2))
            continue
        
        if not current_file:
            continue
        
        # Classify line
        if line.startswith("+") and not line.startswith("+++"):
            chunks.append(CodeChunk(
                file=current_file,
                line_start=new_line,
                line_end=new_line,
                content=line[1:],  # strip leading +
                change_type="added",
            ))
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            chunks.append(CodeChunk(
                file=current_file,
                line_start=old_line,
                line_end=old_line,
                content=line[1:],  # strip leading -
                change_type="removed",
            ))
            old_line += 1
        elif not line.startswith("\\"):
            # Context line
            old_line += 1
            new_line += 1
    
    return chunks


def collect_review_context(
    pr_number: int, title: str, author: str, owner: str, repo: str,
    head_sha: str, files_changed: list, total_loc: int,
    diff_text: str = "", graph_context: Optional[dict] = None,
    mood_emoji: str = "✨", gif_url: str = "",
) -> ReviewContext:
    """
    Collect all data needed for a review.
    
    This is called by T0 BEFORE dispatching deepthink.
    The data is passed to deepthink as a structured context block,
    so deepthink reasons about real data instead of gathering it.
    """
    # Collect repo tree (cheap, from git ls-files)
    repo_tree = collect_repo_tree(owner, repo, head_sha)
    
    # Parse diff into code chunks
    code_chunks = collect_code_chunks(diff_text) if diff_text else []
    
    # Compute PR scope metrics
    total_additions = sum(f.get("additions", 0) for f in files_changed)
    total_deletions = sum(f.get("deletions", 0) for f in files_changed)
    
    return ReviewContext(
        pr_number=pr_number,
        title=title,
        author=author,
        owner=owner,
        repo=repo,
        head_sha=head_sha,
        files_changed=files_changed,
        total_loc=total_loc,
        total_additions=total_additions,
        total_deletions=total_deletions,
        repo_tree=repo_tree,
        code_chunks=code_chunks,
        diff_raw=diff_text[:50000],  # cap at 50k chars
        graph_context=graph_context or {},
        mood_emoji=mood_emoji,
        gif_url=gif_url,
    )


# ── Template Rendering ──────────────────────────────────────────────────────

def render_review_prompt(context: ReviewContext) -> str:
    """
    Build a structured prompt for deepthink that includes all gathered data.
    
    The prompt tells deepthink to reason about the data, not gather it.
    Template ensures all required sections are requested.
    """
    # Format repo tree (cap at 500 entries)
    repo_tree_str = "\n".join(f"  {f}" for f in context.repo_tree[:500])
    if len(context.repo_tree) > 500:
        repo_tree_str += f"\n  ... ({len(context.repo_tree) - 500} more files)"
    
    # Format code chunks (cap at 50 most important)
    chunks_str = ""
    for i, chunk in enumerate(context.code_chunks[:50]):
        chunks_str += f"\n### {chunk.file}:{chunk.line_start}-{chunk.line_end} ({chunk.change_type})\n"
        chunks_str += f"```{chunk.file.split('.')[-1]}\n{chunk.content[:500]}\n```\n"
    
    # Format PR scope
    files_str = "\n".join(
        f"  - {f.get('filename', '?')} (+{f.get('additions', 0)}/-{f.get('deletions', 0)})"
        for f in context.files_changed[:30]
    )
    
    return f"""PR #{context.pr_number} in {context.owner}/{context.repo}

## Your Task
You are a senior engineer performing a **Riptide Review** — an autonomous deep-think code review.
All data is pre-gathered below. Your job is to **reason about it**, not gather it.

## PR Details
- Title: {context.title}
- Author: {context.author}
- HEAD SHA: {context.head_sha[:12]}
- Total LOC changed: {context.total_loc} (+{context.total_additions}/-{context.total_deletions})

## PR Scope (files changed, LOC, status)
{files_str}

## Repository Tree
```
{repo_tree_str}
```

## Code Chunks (from PR diff)
{chunks_str}

## Graphify Analysis
{format_graph_context(context.graph_context if context.graph_context else {})}

## Your Review — Required Structure

### Step 1: Inline Review Comments
For each substantive issue, post an **inline review comment** with a **GitHub suggestion block**:

```
gh api repos/{context.owner}/{context.repo}/pulls/{context.pr_number}/comments \\
  --method POST \\
  -f body='**SEVERITY:** explanation\\n```suggestion\\nproposed code\\n```' \\
  -f commit_id='{context.head_sha}' \\
  -f path='<file_path>' \\
  -F line=<line_number> \\
  -f side='RIGHT'
```

Severity markers:
- `**CRITICAL:**` — definite bug, security issue, data loss risk
- `**WARNING:**` — potential issue, performance concern, code smell
- `**SUGGESTION:**` — style improvement, minor refactor, nitpick

**Rules:** 1-3 inline comments per PR maximum. Focus on real issues.
Parse `@@` hunk headers from the diff for line numbers.

### Step 2: Summary Review
Post a summary comment with ALL of these sections:

```markdown
## 🎯 Summary
(1-2 sentences: what this PR does, no filler)

## 🔍 Findings
| Severity | File | Line | Issue |
|----------|------|------|-------|
| 🟡 Warning | file.py | 42 | Description of the issue |

## 📊 Code Analysis
(For each significant code chunk: WHAT it does, WHY it matters, any concerns)
- `file.py:10-25` — Description of the change and architectural reasoning

## 🔗 Diagram
[Visual Review Diagram](https://excalidraw.com/#json=...)

## 📌 Next Steps
(Specific actionable advice, max 3 items)

## 💭 Explanation
(Trade-offs considered, approach rationale, what was weighed)

---
<sub>Riptide Review via Hermes</sub>
```

### Step 3: Quality Gate
- If you have no critical/warning findings, say so briefly
- Do not invent problems or pad the review
- Reference inline comments in the summary ("see inline comment on file.py:42")

### Step 4: Excalidraw Diagram
Generate a diagram visualizing your findings using the **excalidraw_renderer** module.
If the module is unavailable, include a Mermaid diagram in the summary instead.

```python
import sys
sys.path.insert(0, '/home/sc/workspace')
from riptide.grafiphy.excalidraw_renderer import render_review, upload_excalidraw

findings = [
    dict(severity='warning', title='Issue title', detail='Description', file='file.py', line=42),
]

url = upload_excalidraw(
    render_review(
        pr_data=dict(number={context.pr_number}, title="{context.title[:50]}",
                    repo="{context.owner}/{context.repo}", loc={context.total_loc}),
        findings=findings,
        output_path='/tmp/review.excalidraw',
    )
)
print(f'Excalidraw: {{url}}')
```

REPO PATH: ~/workspace/{context.repo}/
"""


def format_graph_context(graph_context: dict) -> str:
    """Format graphify analysis for the prompt."""
    if not graph_context:
        return "(No graphify analysis available)"
    
    lines = []
    nodes = graph_context.get("god_nodes", [])
    if nodes:
        lines.append("God Nodes:")
        for node in nodes[:10]:
            lines.append(f"  - {node.get('name', '?')} (edges: {node.get('edges', 0)})")
    
    communities = graph_context.get("communities", [])
    if communities:
        lines.append("Communities:")
        for comm in communities[:10]:
            lines.append(f"  - {comm.get('name', '?')} ({len(comm.get('members', []))} members)")
    
    nodes_count = graph_context.get("nodes", 0)
    if nodes_count:
        lines.append(f"Total nodes affected: {nodes_count}")
    
    return "\n".join(lines) if lines else "(No graphify analysis available)"


# ── Post-Generation Validation ──────────────────────────────────────────────

def validate_review(body: str) -> tuple[bool, list[str]]:
    """
    Check that a review contains all required sections.
    
    Returns: (is_valid, list_of_missing_sections)
    """
    required_sections = {
        "Summary": r"## 🎯\s*Summary",
        "Findings": r"## 🔍\s*Findings",
        "Next Steps": r"## 📌\s*Next Steps",
        "Sign-off": r"Riptide Review via Hermes",
    }
    
    recommended_sections = {
        "Code Analysis": r"## 📊\s*Code Analysis",
        "Diagram": r"## 🔗\s*Diagram|```mermaid",
        "Explanation": r"## 💭\s*Explanation",
    }
    
    missing = []
    for name, pattern in required_sections.items():
        if not re.search(pattern, body):
            missing.append(f"REQUIRED: {name}")
    
    missing_recommended = []
    for name, pattern in recommended_sections.items():
        if not re.search(pattern, body):
            missing_recommended.append(f"RECOMMENDED: {name}")
    
    is_valid = len(missing) == 0
    return is_valid, missing + missing_recommended
