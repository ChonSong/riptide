# Grafiphy — ELI5 Pseudocode Diagram Generation

Visual evidence engine for PR reviews. Generates Excalidraw diagrams with simplified pseudocode labels using real graphify data.

## Architecture

```
PR Event → webhook.py → companion.py → grafiphy.orchestrate() → Excalidraw JSON → GitHub Release
```

## Files

| File | Purpose |
|------|---------|
| `orchestrator.py` | Main entry: queries graphify, generates labels, creates Excalidraw, uploads |
| `labeler.py` | Template-based ELI5 label generation (no LLM, strict format rules) |
| `companion.py` | Integration point — calls grafiphy when `COMPANION_ENABLE_DIAGRAM=1` |

## Label Format (Strict)

1. **Every label MUST include exact code names in backticks**: `` `loadCfg()` ``
2. **"(not Z)" ONLY for real decisions visible in code**
3. **Edges**: `"sends X to Y"` (payload + target)
4. **Length**: 5-8 words per label
5. **Format**: `"Does X via \`functionName()\`"`

## Excalidraw JSON Structure

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "hermes-agent",
  "elements": [
    {"type": "text", "id": "title", ...},
    {"type": "rectangle", "id": "node1", "boundElements": [{"id": "t_node1"}]},
    {"type": "text", "id": "t_node1", "containerId": "node1", ...},
    {"type": "arrow", "id": "a1", ...}
  ]
}
```

## ELI5 Pseudocode in Nodes

Each node contains simplified pseudocode explaining WHAT the function does:

```
for each changed file:
  ask graphify: who depends on this?
for each relationship:
  make a label explaining WHY
generate Excalidraw JSON
upload to GitHub release
```

NOT raw code — plain English logic summary.

## Integration

Companion calls `grafiphy.orchestrator.orchestrate()` on PR events.

Environment variable: `COMPANION_ENABLE_DIAGRAM=1`

Returns: List of Excalidraw URLs (uploaded to GitHub release assets).

Comment embeds link: `[Excalidraw diagram](URL)`

## Server Status

```
● riptide.service — active (running)
● COMPANION_ENABLE_DIAGRAM=1
● Webhook: https://riptide.codeovertcp.com/webhook/github
```
