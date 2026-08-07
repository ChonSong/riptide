# Riptide — Vision-Aligned Long-Horizon Roadmap

**Date:** 2026-08-06
**Status:** Planning
**Repo:** `github.com/ChonSong/riptide`
**Related:** GitHub Projects v2 → "Riptide Development" (PVT_kwHOBRbF9s4Bfg5M)

---

## The Vision

1. **Deterministic Data Inputs** — directly ingest preprocessed, deterministic data (tests passing, diffs, concept conversion with example variables and pseudocode).
2. **Two-Tiered Response Flow** — fast, immediate response from raw deterministic data, subsequently edited/enriched with LLM annotations for personality and context.
3. **Multi-Pass LLM Strategy** — small LLM: multiple deterministic calls, each analyzing a different aspect.
4. **Latency Tolerance** — complete LLM-enriched response may take minutes, *provided* there is an indicator the system is actively working.
5. **High-Level Clarity** — explanations and plans stay high-level and clear, no excessive detail.

---

## Model Tiering (Decision)

| Model | Role | Trust |
|-------|------|-------|
| **Ollama (qwen2.5-coder:7b)** | Enricher / prep worker — paraphrase findings, draft ELI5, GIF selection, tone | Free but weak. **Never** trusted to reduce, decide, or implement. Restricted to trivial/supportive tasks. |
| **LongCat-2.0 (via `longcat` provider)** | Judgment, decisions, implementation, final review | The trusted model. Deterministic data drives what it sees; its output is post-hoc enrichment, not the foundation. |
| **Deterministic Python** | Diff analysis, classification, graphify, proofshot, labeler rules, state/dedup | Source of truth. Everything flows from this. |

---

## Current State vs Vision (2026-08-06)

| Vision Pillar | Current State | Gap |
|---|---|---|
| **1. Deterministic Data Inputs** | diff_analyzer.py (security/complexity/error), graphify blast radius, review depth classification, labeler rules, proofshot | No diff→concept conversion, no test-status ingestion, no structured deterministic "context bundle" |
| **2. Two-Tiered Response** | Companion posts TL;DR; T0 orchestrator dispatches T1/T3 async | No explicit "fast now, enriched later" pattern with comment editing |
| **3. Multi-Pass LLM** | Deepthink spawns one big Hermes session per PR | No split into aspect-specific passes (security pass, complexity pass, arch pass) |
| **4. Latency Tolerance** | Async deepthink is silent | No progress indicator on the PR ("Riptide is reviewing…" comment, status footer) |
| **5. High-Level Clarity** | diff_analyzer has structured findings; review comments can be verbose | No high-level executive summary tier; detail is not progressive |

---

## Workstreams (each = one PR)

### WS-0: Merge #61 (companion deterministic) — FOUNDATION
- **Status:** Branch ready, mergeable, BLOCKED (waiting review)
- **Why first:** diff_analyzer.py IS the deterministic data input layer. Everything else builds on it.
- **Action:** Rebase onto main (post-#60), merge.

### WS-R: Research — learn from CodeRabbit/Greptile (delegate, 1 subagent)
- **Goal:** Study how CodeRabbit/Greptile structure multi-pass review, progress indicators, comment editing, severity tuning.
- **Output:** `COMPETITOR-PATTERNS.md` (repo root — `docs/` is gitignored) — concrete patterns to adopt.
- **Why:** User's prior assessment exists (HANDOFF §2), but not deep pattern extraction on multi-pass/progress UX.

### WS-1: Deterministic context bundle (Vision 1)
- **Goal:** A single deterministic pipeline that pre-gathers: diff stats, security findings, complexity findings, graphify blast radius, changed-file taxonomy (UI/core/tests), test status.
- **Output:** `riptide/context_bundle.py` — `build_context_bundle(files, graphify, tests) -> dict`.
- **Innovation:** Diff→concept mapping — "this PR touches auth + payments + adds a test helper" using deterministic heuristics + example variables/pseudocode annotations.
- **Delegation:** One subagent to deepthink implementation.

### WS-2: Two-tier response flow (Vision 2)
- **Goal:** On PR event:
  1. **Tier 1 (instant, deterministic):** post comment from context bundle — verdict, findings, "enrichment in progress…"
  2. **Tier 2 (async, LLM):** Hermes/LongCat enriches THE SAME COMMENT (edit) with personality, ELI5, deeper context.
- **Innovation:** Comment editing — canonical resource is the **PR top-level (issue) comment**: `POST /repos/{o}/{r}/issues/{n}/comments` to create, `PATCH /repos/{o}/{r}/issues/comments/{id}` to enrich in place. One thread, progressive enrichment (matches vision: "subsequently edited and enriched"). Companion's `post_pr_comment()` already uses the POST endpoint; WS-2 adds PATCH.
- **Delegation:** One subagent to deepthink implementation.

### WS-3: Multi-pass LLM (Vision 3)
- **Goal:** Replace one-big-session with deterministic passes, each scoped:
  - Pass A: Security findings → verify/explain (Ollama prep → LongCat verdict)
  - Pass B: Complexity → refactor suggestions (Ollama prep)
  - Pass C: Architecture/blast radius → graphify-informed analysis (LongCat)
  - Pass D: Test coverage suggestions (deterministic)
- **Model routing:** Ollama only for TRIVIAL depth (per user: "restricted to the trivial"); STANDARD/ARCH use LongCat.
- **Delegation:** One subagent to deepthink implementation.

### WS-4: Latency tolerance / progress UX (Vision 4)
- **Goal:** Every async review shows progress:
  - "🔄 Riptide is analyzing…" comment on dispatch
  - Status footer updates: "⚙️ deterministic pass done · 🧠 LLM enrichment in progress"
  - React emoji or comment edit on completion
- **Delegation:** One subagent to deepthink implementation.

### WS-5: High-level clarity (Vision 5)
- **Goal:** Progressive disclosure in comments:
  1. One-line verdict + emoji
  2. Top 3 findings (expandable)
  3. Deep detail only via `@riptide-bot review` full mode
- **Innovation:** "Summary-first, detail-on-demand" — the default comment is short; full analysis in a collapsible/details block.
- **Delegation:** One subagent to deepthink implementation.

---

## Execution Rules (per user)

- **One subagent at a time** (no parallel fan-out for implementation).
- Subagents **deepthink the implementation** — each WS gets a dedicated subagent with full repo context.
- **Ollama restricted to trivial** — enricher/prep only, never decision/implementation.
- Deterministic Python remains the source of truth (memory: "NEVER delete deterministic Python and replace with LLM").
- Branch-based work; PR per workstream; user reviews/merges ("merge it").
- High-level clarity in explanations — no excessive detail in plans.

---

## Suggested Sequencing

```text
WS-0 (#61 merge)        → foundation (deterministic data input exists)
   ↓
WS-R (research)         → learn from CodeRabbit/Greptile
   ↓
WS-1 (context bundle)   → Vision 1 complete
   ↓
WS-2 (two-tier flow)    → Vision 2 complete (needs WS-1)
   ↓
WS-3 (multi-pass)       → Vision 3 complete (needs WS-1, WS-2)
   ↓
WS-4 (progress UX)      → Vision 4 complete (needs WS-2)
   ↓
WS-5 (clarity)          → Vision 5 complete (can be anytime after WS-2)
```

WS-1 → WS-2 → WS-3 have hard dependencies. WS-4/WS-5 can run after WS-2.

---

## Open Questions for User

1. **#61 merge** — merge as-is now (rebase to post-#60), or fold into WS-1?
2. **Comment editing** — OK to edit comments (PATCH API) for progressive enrichment, or prefer separate follow-up comments?
3. **Test ingestion** — do we have access to CI test results via GitHub API for the context bundle (checks API), or only local tests?
4. **Ollama availability** — is Ollama always running on the host (localhost:43311)? Needed for WS-3 prep-pass.
5. **Progress UX** — preferred: edit the same comment, or new "status" comments per stage?
