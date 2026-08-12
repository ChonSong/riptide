# Editable Excalidraw Diagrams for Review Findings

**Status**: Draft / Design discussion  
**Related**: PR #89 (rule-based classification + conditional skill loading)

## Problem

PR #89 introduces a review pipeline that pre-generates Excalidraw diagrams in Python before spawning a Hermes session. The current flow:

1. Python code calls `grafiphy/excalidraw_renderer.py` to render a diagram
2. Diagram is uploaded → returns a URL (e.g., `https://excalidraw.com/#json=...`)
3. URL is embedded in the review template: `[Visual Review Diagram](https://excalidraw.com/#json=...)`
4. Hermes agent receives the URL in its prompt

**The agent cannot edit the diagram.** It receives a static link. If the agent wants to:
- Add a finding to the diagram
- Modify an existing element
- Reflect a new architectural insight visually

...it cannot. It would need to regenerate the entire diagram from scratch, but the Python renderer is not callable from the Hermes agent.

## Impact

- Diagrams are decorative, not functional — they don't evolve with the review
- Findings referenced in text may not match the diagram (agent adds findings post-generation)
- Architectural insights discovered during review have no visual outlet
- Token savings from pre-generation are partially wasted (agent re-describes in text what the diagram shows)

## Proposed Solutions

### Option A: Agent-Generated Diagrams (Recommended)

Pass the `excalidraw` skill to the Hermes agent and let it build the diagram natively.

**How**:
- Include `excalidraw` in the skill list for STANDARD and ARCH depth PRs
- Agent uses Excalidraw MCP tools to create, edit, and finalize the diagram
- Agent can iterate: start with a skeleton, add findings as it discovers them
- Final diagram URL is posted with the review

**Pros**:
- Agent has full control — diagrams evolve with the review
- Findings and diagram stay in sync
- No wasted pre-generation work

**Cons**:
- Higher token cost (agent spends tokens on diagram generation)
- Slightly longer review time

### Option B: Pre-Generate + Agent Editable

Keep Python pre-generation but make the output editable by the agent.

**How**:
- Python renderer outputs `.excalidraw` JSON (not just a URL)
- Pass the JSON file as context to the Hermes agent
- Agent edits the JSON directly (text-based, no rendering needed)
- Agent uploads the modified JSON to get a new URL

**Pros**:
- Preserves token savings of pre-generation
- Agent can modify structure
- JSON is human-readable text (agent can manipulate it)

**Cons**:
- Excalidraw JSON is verbose — consumes context window
- Agent still can't *see* the diagram (blind editing)
- JSON editing is error-prone without visual feedback

### Option C: Two-Phase (Current + Retrofit)

Generate a baseline diagram pre-spawn, then let the agent add an "addendum" section.

**How**:
- Pre-generate the architecture baseline (Option B's JSON)
- Agent reviews code, identifies findings
- Agent generates a Mermaid diagram for the *delta* (findings not in baseline)
- Final review includes both: baseline Excalidraw + delta Mermaid

**Pros**:
- Minimal changes to existing architecture
- Mermaid is text-friendly for agents
- Fallback already exists in current code

**Cons**:
- Two diagram formats in one review (inconsistent UX)
- Agent still can't modify the baseline
- More complex pipeline

## Decision Matrix

| | Agent-Generated | Pre-Gen + Editable | Two-Phase |
|---|---|---|---|
| Agent can edit | ✅ Full | ⚠️ JSON only | ❌ Mermaid only |
| Token cost | Higher | Lower | Medium |
| Complexity | Low | Medium | High |
| Diagram/review sync | ✅ Perfect | ⚠️ Partial | ⚠️ Partial |
| Context window cost | Medium | High (JSON) | Low |

## Recommendation

**Option A (Agent-Generated)** — simplest, most flexible, eliminates the synchronization problem entirely. Token cost is acceptable because:
- Only STANDARD and ARCH PRs generate diagrams (not TRIVIAL/INLINE)
- The excalidraw skill is efficient (MCP-based, not full LLM generation)
- Findings and diagram are produced in one pass (no re-description)

## Open Questions

1. Should TRIVIAL PRs get a simple auto-generated badge instead of a full diagram?
2. Do we keep the `grafiphy/excalidraw_renderer.py` module as a fallback if the agent fails?
3. Should diagrams be posted inline (URL) or attached as files (for offline review)?
