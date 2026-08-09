# Excalidraw Diagrams

<!-- Trigger: Generating architecture diagrams for PR reviews -->

## Generation

```bash
# From cron-spawned session (ensure repo root on sys.path)
import sys
sys.path.insert(0, '/home/sc/workspace/riptide')  # REPO ROOT, not workspace parent
from riptide.grafiphy.excalidraw_renderer import render_review, upload_excalidraw

diagram_url = upload_excalidraw(render_review(findings))
```

## Upload

```bash
python3 ~/.hermes/hermes-agent/skills/creative/excalidraw/scripts/upload.py /tmp/review.excalidraw
# Returns: https://excalidraw.com/#json=XXXXX,YYYYY
```

**Pitfall:** GitHub Gist links don't render as viewable diagrams. Always use `excalidraw.com/#json=...`.

## Color Coding

| Severity | Color | Hex |
|----------|-------|-----|
| Critical | Red | `#ef4444` |
| Warning | Amber | `#fbbf24` |
| Suggestion | Cyan | `#22d3ee` |
| Approve | Green | `#34d399` |

## Include in Review Prompt

```
## Pre-generated Architecture Diagram
[View Diagram]({diagram_url})

### Step 4: Architecture Diagram
The architecture diagram is pre-generated and embedded above. Reference it in your Code Analysis section.
```
