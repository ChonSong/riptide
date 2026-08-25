# Riptide — Vision-Aligned Long-Horizon Roadmap

**Date:** 2026-08-25
**Status:** Pillars 1–5 shipped. Fix pipeline reliability (PR #174) in review.
**Repo:** `github.com/ChonSong/riptide`
**Architecture:** See **[`ARCHITECTURE.md`](ARCHITECTURE.md)** for system design.
**Runbook:** See **[`HANDOFF.md`](HANDOFF.md)** for operational details.

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

## Current State vs Vision (2026-08-25)

| Vision Pillar | Current State | Gap |
|---|---|---|
| **1. Deterministic Data Inputs** | ✅ **SHIPPED** — `context_bundle.py`, `diff_analyzer.py`, `pipeline/probe.py` gather all deterministic signals including cleanliness. | None blocking. |
| **2. Two-Tiered Response** | ✅ **SHIPPED** — Companion posts Tier 1 (deterministic verdict + "🔍 enrichment in progress…"), then PATCHes with ELI5 enrichment. | None blocking. |
| **3. Multi-Pass LLM** | ✅ **SHIPPED** — Deepthink splits into aspect-specific passes via Conductor pipeline. | None blocking. |
| **4. Latency Tolerance** | ✅ **SHIPPED** — Tier 1 comment + progress marker + command-only code writing. | None blocking. |
| **5. High-Level Clarity** | ✅ **SHIPPED** — Verdict-first output, capped at 5 findings, with time estimates. | None blocking. |

---

## Workstreams (each = one PR)

### WS-0: #61 (companion deterministic) — FOUNDATION ✅ MERGED (2026-08-06)
- **Status:** Merged — diff_analyzer.py IS the deterministic data input layer.

### WS-R: Research — learn from CodeRabbit/Greptile ✅ MERGED (2026-08-06)
- **Status:** Merged via #62 — `COMPETITOR-PATTERNS.md` at repo root.

### WS-1: Deterministic context bundle (Vision 1) ✅ MERGED (2026-08-07, #63)
- **Output:** `riptide/context_bundle.py` — `build_context_bundle(files, graph_context, pr_details=None) -> dict`.

### WS-2: Two-tier response flow (Vision 2) ✅ MERGED (2026-08-07, #64)
- **Innovation:** Comment editing — canonical resource is the PR top-level comment: POST to create, PATCH to enrich.

### WS-3: Unified Riptide pipeline (Vision 3+4+5) ✅ MERGED (2026-08-15)
- **Goal:** ONE pipeline covering the entire Riptide process — Conductor-orchestrated multi-stage pipeline.
- **Stages:** probe → judge → artisan → engine → scribe (+ ci_verifier for fix, + cleanliness for review).

### WS-4: Latency tolerance / progress UX — ABSORBED into WS-3
### WS-5: High-level clarity — ABSORBED into WS-3

---

## Visual Verification Pipeline (Phase 5 — ADOPTED 2026-08-08)

**Stack:**

| Layer | Tool | Role |
|-------|------|------|
| 1. Deterministic diff | **Playwright `toHaveScreenshot`** | Baselines, platform-tagged on `ubuntu-latest` |
| 2. Self-healing | **refqa** | YAML smoke + selector self-healing |
| 3. Evidence | **proofshot** | Screenshots/GIF → PR comment |
| 4. Triage (DEFERRED) | **VRT** + local Ollama VLM | Commit-baseline review server |

### WS-6: Pre-flight (hermes-webui-tests) — NEXT in visual track
### WS-7: Deterministic visual baselines (CI)
### WS-8: refqa smoke layer + proofshot evidence
### WS-9: VRT self-hosted review server (DEFERRED / OPTIONAL)

---

## Fix Pipeline Reliability (PR #174 — 2026-08-25)

### WS-F1: CI Verifier (Worker 9)
- **Problem:** Fixer declares success after local tests pass — doesn't check GitHub CI.
- **Solution:** New Conductor stage polls `gh pr checks` after push, classifies failures (fixable vs non-fixable), retries once.
- **Pipeline:** Fix pipeline extended to 6 stages (probe → judge → artisan → engine → **ci_verifier** → scribe).

### WS-F2: Cleanliness (Worker 10)
- **Problem:** Review only checks code quality, not PR hygiene.
- **Solution:** New Conductor stage evaluates 7 cleanliness signals from Probe output.
- **Pipeline:** Review pipeline extended to 6 stages (probe → judge → artisan → engine → scribe → **cleanliness**).

---

## Execution Rules (per user)

- **One subagent at a time** (no parallel fan-out for implementation).
- **One PR per workstream** (no bundling).
- **Branch-based development** (no direct pushes to main).
- **Explicit merge authorization** required.
- **Verify CI is FRESH** — force fresh run via close/reopen if needed.

---

## Appendix: Document Map

| Doc | Purpose |
|-----|---------|
| **`VISION-ROADMAP.md`** (this file) — Strategic north star, vision, model tiering, workstream log |
| **`ARCHITECTURE.md`** — System design, pipeline stages, worker specs, data flow, observability, file structure |
| **`HANDOFF.md`** — Operational runbook: deploy, test, known issues, next steps |
| **`AGENTS.md`** — AI agent repo rules |
| **`CLAUDE.md`** — Claude Code project rules |
