# Riptide — Vision-Aligned Long-Horizon Roadmap

**Date:** 2026-08-07
**Status:** Pillars 1–2 shipped to production (WS-1, WS-2 merged)
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

## Current State vs Vision (2026-08-07)

| Vision Pillar | Current State | Gap |
|---|---|---|
| **1. Deterministic Data Inputs** | ✅ **SHIPPED** — `context_bundle.py`: `build_context_bundle()` gathers diff stats, security/complexity findings (via bundled DiffAnalyzer), graphify blast radius, concept taxonomy (`CONCEPT_RULES` + `classify_concept`, UI/core/tests), DiffReport. Reused by Companion to avoid double analysis. | None blocking. Diff→concept + example annotations exist (WS-1). |
| **2. Two-Tiered Response** | ✅ **SHIPPED** — Companion posts Tier 1 (deterministic verdict + "🔍 enrichment in progress…"), then PATCHes the same comment with ELI5 enrichment. Comment-id guarded; Tier 1 persists if enrichment fails. | Tier 2 is currently a single ELI5 pass — full multi-pass enrichment is WS-3. |
| **3. Multi-Pass LLM** | Deepthink spawns one big Hermes session per PR (cron bot) | **WS-3 (NEXT)** — split into aspect-specific passes (security, complexity, arch, tests) |
| **4. Latency Tolerance** | Tier 1 comment + "enrichment in progress…" marker shipped with WS-2 | Per-pass progress footers ("⚙️ deterministic done · 🧠 LLM pass 2/4…") — WS-4 |
| **5. High-Level Clarity** | `_build_tier1_body()` ships verdict + top findings first, enrichment second | Progressive disclosure tiers — WS-5 |

---

## Workstreams (each = one PR)

### WS-0: #61 (companion deterministic) — FOUNDATION ✅ MERGED (2026-08-06)
- **Status:** Merged — diff_analyzer.py IS the deterministic data input layer.
- **Result:** `feat(companion): replace LLM echo-TL;DR with deterministic diff analysis`.

### WS-R: Research — learn from CodeRabbit/Greptile ✅ MERGED (2026-08-06)
- **Status:** Merged via #62 (with roadmap) — `COMPETITOR-PATTERNS.md` at repo root.
- **Output:** Concrete patterns for multi-pass review, progress indicators, comment editing, severity tuning.

### WS-1: Deterministic context bundle (Vision 1) ✅ MERGED (2026-08-07, #63)
- **Goal:** A single deterministic pipeline that pre-gathers: diff stats, security findings, complexity findings, graphify blast radius, changed-file taxonomy (UI/core/tests), test status.
- **Output:** `riptide/context_bundle.py` — `build_context_bundle(files, graph_context, pr_details=None) -> dict`.
- **Innovation:** Diff→concept mapping — `CONCEPT_RULES` + `classify_concept()` (webhook under `api/` → core via negative lookahead). Bundle exposes `bundle["report"]` (DiffReport) so Companion reuses it instead of running `analyze()` twice.
- **Result:** Companion routes `_execute()` through `self.build_context_bundle()`; bundle-build failure falls back to LLM path.

### WS-2: Two-tier response flow (Vision 2) ✅ MERGED (2026-08-07, #64)
- **Goal:** On PR event:
  1. **Tier 1 (instant, deterministic):** post comment from context bundle — verdict, findings, "enrichment in progress…"
  2. **Tier 2 (async, LLM):** Hermes/LongCat enriches THE SAME COMMENT (edit) with personality, ELI5, deeper context.
- **Innovation:** Comment editing — canonical resource is the **PR top-level (issue) comment**: `POST /repos/{o}/{r}/issues/{n}/comments` to create, `PATCH /repos/{o}/{r}/issues/comments/{id}` to enrich in place. One thread, progressive enrichment (matches vision: "subsequently edited and enriched").
- **Implementation:** `_build_tier1_body()` posts verdict + 🔍 progress marker; comment-id guard (no id → no enrichment); Tier 2 ELI5 PATCHes same comment; Tier 1 survives enrichment failure by design.
- **Delegation:** One subagent to deepthink implementation.

### WS-3: Multi-pass LLM (Vision 3) — NEXT
- **Goal:** Replace one-big-session with deterministic passes, each scoped:
  - Pass A: Security findings → verify/explain (Ollama prep → LongCat verdict)
  - Pass B: Complexity → refactor suggestions (Ollama prep)
  - Pass C: Architecture/blast radius → graphify-informed analysis (LongCat)
  - Pass D: Test coverage suggestions (deterministic)
- **Integration points (existing code):** WS-2's Tier-2 slot (`_execute()` → ELI5 → `update_pr_comment()`) is the hook; passes consume `self._context_bundle` (report.findings per severity + `aggregate` concepts + graph_context) and each PATCHes the same comment. Deepthink's one-session spawn (`_spawn_deepthink`) is the thing being replaced; `classify_review_depth()`/`select_skills()` already exist to route depth.
- **Model routing:** Ollama only for TRIVIAL depth (per user: "restricted to the trivial"); STANDARD/ARCH use LongCat.
- **Delegation:** One subagent to deepthink implementation.

### WS-4: Latency tolerance / progress UX (Vision 4)
- **Goal:** Every async review shows progress:
  - "🔄 Riptide is analyzing…" comment on dispatch
  - Status footer updates: "⚙️ deterministic pass done · 🧠 LLM enrichment in progress"
  - React emoji or comment edit on completion
- **Leverages:** WS-2's existing PATCH-same-comment pattern — footers are just per-pass body updates.
- **Delegation:** One subagent to deepthink implementation.

### WS-5: High-level clarity (Vision 5)
- **Goal:** Progressive disclosure in comments:
  1. One-line verdict + emoji
  2. Top 3 findings (expandable)
  3. Deep detail only via `@riptide-bot review` full mode
- **Leverages:** `_build_tier1_body()` already ships verdict-first; WS-5 formalizes disclosure tiers.
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
WS-0 (#61)  ✅ merged   → foundation (deterministic data input exists)
   ↓
WS-R (#62)  ✅ merged   → learn from CodeRabbit/Greptile
   ↓
WS-1 (#63)  ✅ merged   → Vision 1 complete (context bundle)
   ↓
WS-2 (#64)  ✅ merged   → Vision 2 complete (two-tier response)
   ↓
WS-3 (NEXT)            → Vision 3: multi-pass LLM (needs WS-1, WS-2)
   ↓
WS-4                   → Vision 4: progress UX (needs WS-2)
   ↓
WS-5                   → Vision 5: clarity (anytime after WS-2)
```

WS-1 → WS-2 → WS-3 have hard dependencies. WS-4/WS-5 can run after WS-2.

---

## Open Questions for User

1. **WS-3 pass orchestration** — should the multi-pass LLM run inside Companion's Tier-2 slot (same comment, sequential PATCHes), or as a new Deepthink-style cron bot that edits the Tier-1 comment? (Affects latency and model budget.)
2. **Comment editing** — confirmed in production for ELI5. OK to extend to per-pass edits (each pass PATCHes the same comment)?
3. **Test ingestion** — do we have access to CI test results via GitHub API for the context bundle (checks API), or only local tests? (WS-3 Pass D depends on this.)
4. **Ollama availability** — is Ollama always running on the host (localhost:43311)? Needed for WS-3 prep-pass.
5. **Progress UX** — preferred: edit the same comment per stage, or separate "status" comments per stage?
