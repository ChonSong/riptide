#!/usr/bin/env python3
"""
Hermes skill: diagram-analyst

Skill definition for the Diagram Analyst worker. This skill enables
a Hermes agent to generate annotated architecture diagrams from PR
findings that communicate agent understanding.

Usage:
    skill_view('diagram-analyst')

This skill provides:
    - Diagram generation from findings.json
    - Annotation overlay with callout boxes
    - Narrative generation from findings structure
    - Confidence scoring and gap identification
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SKILL_NAME = "diagram-analyst"
SKILL_VERSION = "1"


def run(
    findings_path: str,
    owner: str,
    repo: str,
    pr_number: int,
    title: str = "",
    author: str = "",
    loc: int = 0,
    output_path: str | None = None,
) -> dict | None:
    """
    Run the Diagram Analyst skill.

    Args:
        findings_path: Path to findings.json from Bot 2
        owner: Repository owner
        repo: Repository name
        pr_number: Pull request number
        title: PR title
        author: PR author
        loc: Lines of code changed
        output_path: Where to write diagram_insights.json

    Returns:
        dict with diagram_url, annotations, narrative (or None on failure)
    """
    cmd = [
        sys.executable, "-m", "riptide.diagram_analyst",
        "--findings", findings_path,
        "--owner", owner,
        "--repo", repo,
        "--pr", str(pr_number),
        "--title", title,
        "--author", author,
        "--loc", str(loc),
    ]

    if output_path:
        cmd.extend(["--output", output_path])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    if result.returncode != 0:
        print(f"Diagram Analyst failed: {result.stderr}", file=sys.stderr)
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"Invalid JSON output: {result.stdout[:200]}", file=sys.stderr)
        return None


def get_prompt_template() -> str:
    """
    Returns the prompt template for the Diagram Analyst skill.
    This is used by Hermes agents to understand how to invoke the skill.
    """
    return """## Diagram Analyst Skill

Generate an annotated architecture diagram from PR findings.

### When to Use
- After Bot 2 (Deepthink) produces findings.json
- When the user requests `@riptide-bot diagram`
- As part of a comprehensive review workflow

### Invocation
```python
result = run(
    findings_path="/tmp/findings.json",
    owner="ChonSong",
    repo="riptide",
    pr_number=42,
    title="fix: remove duplicate auto-deploy",
    author="ChonSong",
    loc=150,
    output_path="/tmp/diagram_insights.json"
)
```

### Output
```json
{
  "diagram_url": "https://excalidraw.com/#json=...",
  "annotations": [
    {
      "element_id": "callout_0",
      "type": "finding",
      "finding_idx": 0,
      "file": "webhook.py",
      "line": 344,
      "severity": "warning",
      "message": "Race condition detected"
    }
  ],
  "narrative": "PR #42 by @ChonSong changes 150 LOC across 3 file(s)...",
  "confidence": 0.85,
  "gaps": ["Test coverage impact unknown"]
}
```

### Integration
After generating the diagram, pass the output to `assemble_review.py`:

```bash
python -m riptide.assemble_review \
  --findings /tmp/findings.json \
  --diagram-url "$(jq -r .diagram_url /tmp/diagram_insights.json)" \
  --diagram-insights /tmp/diagram_insights.json \
  --owner ChonSong --repo riptide --pr 42 \
  --model "LongCat-2.0" --provider "longcat"
```
"""


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Diagram Analyst Skill")
    parser.add_argument("--findings", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--title", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--loc", default=0, type=int)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    result = run(
        findings_path=args.findings,
        owner=args.owner,
        repo=args.repo,
        pr_number=args.pr,
        title=args.title,
        author=args.author,
        loc=args.loc,
        output_path=args.output,
    )

    if result:
        print(json.dumps(result, indent=2))
    else:
        print("ERROR: Skill failed")
        exit(1)
