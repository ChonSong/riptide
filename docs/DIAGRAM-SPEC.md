# Review Diagram — Specification

Status: **draft for approval** (authored 2026-09-17). This file exists because the diagram's
*section list* is specified in code but its *requirements* are not specified anywhere, which is
why the diagram has "never had all sections filled as desired" — completeness was never defined.

## The problem, as measured

`riptide/grafiphy/excalidraw_renderer.py:render_review()` draws a fixed ordered set of sections,
but **every content section is conditional on an input being supplied** (`if distance_map:`,
`if repo_tree:`, `if god_nodes:`, `if communities:`, `if code_chunks:`, `if human_narrative:`,
`if findings:`). An unsupplied input renders as *nothing* — no placeholder, no warning. So a
missing section is indistinguishable from a section that had no content.

Additionally, verified by grep across the repo:

- **Two divergent renderer copies exist**: `riptide/grafiphy/excalidraw_renderer.py` and
  `riptide/graphify_ingest/excalidraw_renderer.py`. Callers import different ones
  (`grafiphy/orchestrator.py:21`, `graphify_ingest/orchestrator.py:21`, and
  `pipeline/artisan.py:61` which imports the `graphify_ingest` copy) and supply different
  subsets of inputs, so which pipeline ran changes which sections appear.
- **`code_chunks` is supplied by no caller**: it appears only inside the two renderers, never in
  any orchestrator. The "Code Chunks with WHY" section therefore cannot fill on either path.
- **`repo_tree` is supplied by the `grafiphy` path only**, so the directory-tree section
  silently disappears when the `graphify_ingest` path runs.
- **`frontend_components` is both unsupplied and never drawn**: present in the signature (`:332`)
  and docstring (`:353`), normalised at `:371`, with no `make_rect`/`make_text` using it.

## Section contract

Order is the draw order. "Required input" is the exact parameter name read by the renderer.

| # | Section | Required input | Class | Notes |
|---|---------|----------------|-------|-------|
| 1 | Title / subtitle | PR metadata | **mandatory** | Always drawn. |
| 2 | Distance-Radius Network Map | `distance_map` | optional | Graph distance from repo god nodes to changed files. |
| 3 | Codebase Directory Tree | `repo_tree` | optional | Supplied by the `grafiphy` path only — a defect today. |
| 4 | PR Scope (changed files) | `pr_data` | **mandatory** | The changed-file list; must never be blank. |
| 5 | Graphify Analysis — god nodes | `god_nodes` | optional | Blast radius. |
| 6 | Graphify Analysis — communities | `communities` | optional | Blast radius. |
| 7 | Code Chunks with WHY | `code_chunks` | optional | **Unsupplied today.** Supply it or mark it unavailable. |
| 8 | Human-Readable Narrative | `human_narrative` | optional | May legitimately come from the review session. |
| 9 | Findings with Severity | `findings` | conditional | Mandatory whenever a review produced findings. |
| 10 | Suggested Changes | `suggestions` | optional | |
| 11 | Legend | — | **mandatory** | Always drawn. |
| — | `frontend_components` | — | **remove or implement** | Declared, never drawn, never supplied. Recommend removing the parameter. |

## Rules

1. **Mandatory sections always render**, even with no data, showing an explicit
   `no data` marker. A section must never vanish silently — absence is information.
2. **Optional sections render a header with either content or a marker naming the missing input**
   (e.g. `no data — input 'code_chunks' not supplied`). This makes completeness self-reporting:
   the diagram states what is missing rather than quietly being smaller.
3. **The diagram is a deterministic function of pipeline state.** Structure comes from code, not
   from a model's discretion. `human_narrative` is the one section that may originate from the
   review session, and its absence must be visible like any other.
4. **One renderer.** Collapse the two copies into one canonical module; all callers import it.
   The survivor keeps the union of current behaviour. No caller may supply a subset that
   silently changes the section set.
5. **Provenance footer.** The diagram carries: repo, PR number, `head_sha`, the version of the
   renderer that produced it, and the list of which optional inputs were supplied vs absent.
   This is the same "state what produced it" rule the review comments follow.
6. **Verify `upload_excalidraw` reaches the user.** `pipeline/conductor.py:462/537/628` pass
   `inputs={"command": "upload_excalidraw /tmp/review.excalidraw"}` — a shell command handed to a
   session, not a direct call. If that command does not resolve in the spawned session, the
   diagram is generated and discarded. Confirm or replace with a direct call.

## Open decisions (need a human)

1. **Mandatory vs optional** for sections 2, 3, 5, 6, 7, 8, 10 — which are genuinely required for
   a review diagram to be useful, and which are nice-to-have?
2. **`frontend_components`** — remove the parameter, or implement it as a drawn section
   (requiring a supplier)?
3. **Unavailable inputs** — when no caller can supply an input (e.g. `code_chunks` today), should
   the section be marked `no data`, or removed from the contract until it has a supplier?
