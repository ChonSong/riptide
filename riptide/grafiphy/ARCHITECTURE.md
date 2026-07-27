# Grafiphy — Visual Evidence Engine for PR Companions

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         GRAFIPHY ORCHESTRATOR                               │
│                                                                             │
│  Input: PR metadata + diff + graphify context                               │
│  Output: PNG diagrams embedded in TL;DR comments                            │
│                                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │
│  │   Sankey    │  │  Mermaid    │  │ Architecture│  │ Graphify    │       │
│  │  Renderer   │  │  Renderer   │  │  Renderer   │  │ Tree        │       │
│  │  (matplotlib│  │  (D3.js)    │  │  (SVG→PNG)  │  │ (screenshot)│       │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘       │
│         │                │                │                │               │
│         └────────────────┴────────────────┴────────────────┘               │
│                                   │                                         │
│                           ┌───────▼───────┐                                 │
│                           │  PNG Upload   │                                 │
│                           │  (GitHub      │                                 │
│                           │   release)    │                                 │
│                           └───────┬───────┘                                 │
│                                   │                                         │
│                           ┌───────▼───────┐                                 │
│                           │  Embed in     │                                 │
│                           │  TL;DR        │                                 │
│                           └───────────────┘                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Component Map

| Component | File | Input | Output |
|-----------|------|-------|--------|
| **Proofshot CLI** | `proofshot/cli.py` | URL, selectors, seed data | WebM, PNG, GIF |
| **Grafiphy Orchestrator** | `grafiphy/orchestrator.py` | PR metadata + diff | List of PNG URLs |
| **Sankey Renderer** | `grafiphy/renderers/sankey.py` | Flow data (JSON) | PNG |
| **Mermaid Renderer** | `grafiphy/renderers/mermaid.py` | Mermaid spec (mmd) | PNG |
| **Architecture Renderer** | `grafiphy/renderers/architecture.py` | System topology (JSON) | PNG |
| **Graphify Tree Renderer** | `grafiphy/renderers/graphify_tree.py` | graphify-out/ path | PNG |
| **Companion Bridge** | `riptide/companion.py` (modified) | PR event | TL;DR + PNGs |

## Data Flow

```
PR Event (webhook)
  │
  ├─→ companion.py:_execute()
  │     │
  │     ├─→ graphify affected → blast radius text (existing)
  │     │
  │     ├─→ grafiphy.orchestrate(pr_metadata, diff)
  │     │     │
  │     │     ├─→ sankey.render(flow_data) → blast-radius.png
  │     │     ├─→ mermaid.render(call_sequence) → call-flow.png
  │     │     ├─→ architecture.render(topology) → system-map.png
  │     │     └─→ graphify_tree.render(graphify-out/) → dependency-tree.png
  │     │           │
  │     │           └─→ upload_pngs_to_release() → [urls]
  │     │
  │     ├─→ proofshot.capture(ui_files) → proofshot.gif
  │     │     └─→ upload to release → url
  │     │
  │     └─→ format_comment(tldr, png_urls, proofshot_url) → post
  │
  └─→ GitHub comment with embedded PNGs
```

## Renderer Specifications

### 1. Sankey Renderer (matplotlib)
- **Use for**: Blast radius flow, pipeline stages, data flow
- **Input**: `{nodes: [{name, color}], links: [{source, target, value}]}`
- **Output**: 1600x900 PNG, dark theme (#020617 bg)
- **Library**: matplotlib 3.11+ (already installed)

### 2. Mermaid Renderer (D3.js + Playwright)
- **Use for**: Sequence diagrams, call flows, state machines
- **Input**: Mermaid syntax string
- **Output**: 1200x800 PNG, transparent bg
- **Library**: Playwright headless → render mermaid.live or local mermaid.js

### 3. Architecture Renderer (SVG → PNG)
- **Use for**: System topology, component maps, deployment diagrams
- **Input**: `{components: [{name, type, x, y}], connections: [{from, to, label}]}`
- **Output**: 1920x1080 PNG, dark theme
- **Library**: SVG write → Playwright screenshot

### 4. Graphify Tree Renderer (Playwright screenshot)
- **Use for**: Dependency trees, community maps, god-node visualization
- **Input**: Path to graphify-out/ directory
- **Output**: Full-page PNG of interactive tree
- **Library**: Playwright screenshot of graphify tree HTML

## Proofshot CLI Commands

```bash
# Start a proofshot session
proofshot start --url http://localhost:8788 --seed seed.js --output ./proofshot-out/

# Capture a specific state
proofshot capture --selector "#app" --output step.png

# Stop session and generate GIF
proofshot stop --output final.gif

# Full PR workflow: start → capture → stop → upload → comment
proofshot pr 73 --url http://localhost:8788 --seed seed.js --comment "UI verification"
```

## File Structure

```
proofshot/
├── cli.py                    # New: CLI entry point
├── record-demo-*.py          # Existing: demo scripts
├── demo-*/                   # Existing: demo artifacts
└── artifacts/                # Existing: session recordings

grafiphy/
├── __init__.py
├── orchestrator.py           # Main orchestrator
├── upload.py                 # GitHub release upload
└── renderers/
    ├── __init__.py
    ├── sankey.py             # Sankey diagrams
    ├── mermaid.py            # Mermaid diagrams
    ├── architecture.py       # Architecture diagrams
    └── graphify_tree.py      # Graphify tree screenshots

riptide/
├── riptide/
│   ├── companion.py          # Modified: grafiphy + proofshot bridge
│   └── webhook.py            # Modified: auto-proofshot on UI changes
```

## Integration Points

### Companion Bridge (companion.py modifications)
1. Import grafiphy orchestrator
2. After graphify context fetched, call `grafiphy.orchestrate()`
3. Upload returned PNGs to GitHub release
4. Embed PNG URLs in TL;DR comment body
5. If UI files changed, spawn proofshot thread

### Webhook Modifications (webhook.py)
1. On `pull_request` sync/opened, check for UI file changes
2. If UI files present, spawn proofshot thread alongside companion
3. Proofshot thread: start → capture → stop → upload → store URL in shared state
4. Companion reads proofshot URL from shared state

## Upload Strategy

PNGs are uploaded to GitHub release assets on a dedicated "grafiphy" release:
- Release tag: `grafiphy-assets` (pre-created, reused)
- Assets named: `{pr_number}-{diagram_type}-{timestamp}.png`
- URLs: `https://github.com/{owner}/{repo}/releases/download/grafiphy-assets/{name}.png`
- Embedded in comments as Markdown images

## Phased Rollout

| Phase | Component | Dependency | ETA |
|-------|-----------|------------|-----|
| 1 | Proofshot CLI | None | 1h |
| 2 | Sankey Renderer | None | 1h |
| 3 | Mermaid Renderer | None | 1h |
| 4 | Architecture Renderer | None | 1h |
| 5 | Graphify Tree Renderer | None | 1h |
| 6 | Orchestrator | 1-5 | 1h |
| 7 | Companion Bridge | 6 | 1h |
| 8 | Webhook Auto-Proofshot | 1 | 30m |
| 9 | End-to-End Test | 7,8 | 30m |
