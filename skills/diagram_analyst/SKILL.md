---
name: diagram_analyst
description: Generate annotated Excalidraw diagrams from PR review findings.
tags:
  - excalidraw
  - diagram
  - review
  - visualization
---

# Diagram Analyst

Generates an annotated Excalidraw diagram from PR review findings. This module
wraps the pipeline `DiagramBuilder` to produce a visual summary of what the PR
changes and the key findings from the review.

## Usage

```bash
python -m riptide.diagram_analyst \
  --findings /tmp/findings.json \
  --owner ChonSong \
  --repo riptide \
  --pr 42 \
  --title "feat: add new feature" \
  --author "ChonSong" \
  --loc 150 \
  --output /tmp/diagram_insights.json
```

## Input

A JSON file containing an array of findings:

```json
[
  {
    "severity": "critical|warning|suggestion|info",
    "title": "Short description of the issue",
    "file": "path/to/file.py",
    "line": 42,
    "detail": "Detailed explanation and suggested fix"
  }
]
```

## Output

JSON with the following structure:

```json
{
  "diagram_url": "https://excalidraw.com/#json=... or file:///tmp/...",
  "narrative": {
    "summary": "PR #42 by author: 1 critical, 2 warning in 3 file(s)",
    "title": "feat: add new feature",
    "author": "ChonSong",
    "repo": "ChonSong/riptide",
    "total_loc": 150,
    "findings_count": 3,
    "severity_breakdown": {"critical": 1, "warning": 2},
    "files_affected": ["path/to/file.py", "..."]
  },
  "confidence": 0.85,
  "gaps": ["No line numbers in findings — precise location unknown"],
  "annotations": [
    {
      "index": 0,
      "severity": "critical",
      "title": "SQL injection risk",
      "file": "app.py",
      "line": 42,
      "detail": "Use parameterized queries",
      "element_id": "finding_0"
    }
  ]
}
```

## Integration

Called by the Conductor pipeline AFTER the Judge produces findings.
If diagram generation fails, the review proceeds without a diagram.

## Return Values

- **Success**: dict with diagram_url, narrative, confidence, gaps, annotations
- **Failure**: `None` (empty findings, import error, or diagram build failure)

The module is defensive — it logs warnings and returns None on any failure,
allowing the review to proceed without a diagram attachment.