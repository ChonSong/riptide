# Riptide — Vision-Aligned Long-Horizon Roadmap

**Date:** 2026-08-08
**Status:** Pillars 1–2 shipped to production (WS-1, WS-2 merged); WS-3 unified pipeline next; Phase 5 visual verification adopted (WS-6 pre-flight next)
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
| **3. Multi-Pass LLM** | ✅ **SHIPPED** — Deepthink splits into aspect-specific passes (security, complexity, arch, tests) via `@riptide-bot review` | None blocking. Multi-pass LLM strategy supported via deep-think review command. |
| **4. Latency Tolerance** | ✅ **SHIPPED** — Tier 1 comment + "enrichment in progress…" marker (WS-2) + command-only code writing locked in (WS-4) | None blocking. |
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

### WS-3: Unified Riptide pipeline (Vision 3+4+5) — NEXT
- **Goal (per user):** ONE pipeline covering the entire Riptide process (proofshotter excluded) — best of both worlds: Companion's fast webhook Tier-1 + Deepthink's depth-gated deep review. `@riptide-bot` commands guide users through the pipeline; **only processes that write code (fix) are command-only**.
- **Stages:**
  - **Stage 0 — Gate (process heuristics, ONE store):** StateStore SQLite is the single authority for *in-flight job?*, *already reviewed this SHA?*, *skipped?*, *stale?*. `classify_review_depth()` runs here for **every** entry path (webhook + cron + command) — TRIVIAL → Tier 1 only; STANDARD/ARCH → full enrich.
  - **Stage 1 — Deterministic core (runs ONCE, feeds everything):** `build_context_bundle()` (DiffAnalyzer + concepts + graph_context) + `pre_generate_diagram()` (Excalidraw). Every downstream consumer reads the bundle — nothing re-fetches.
  - **Stage 2 — Tier 1 comment (auto on PR open/sync):** `_build_tier1_body()` verdict + 🔍 progress marker; POST once, becomes the canonical thread.
  - **Stage 3 — Enrichment (auto for STANDARD/ARCH, or `@riptide-bot review`):** multi-pass LLM PATCHes the SAME comment: Pass A security → Pass B complexity → Pass C architecture → Pass D tests, each with a progress footer (WS-4 absorbed).
  - **Stage 4 — Command-only (writes code):** `@riptide-bot fix [desc]` (authz-gated, existing fixer flow) and `@riptide-bot relabel`. Nothing else in the pipeline writes code (WS-5 absorbed: Tier 1 verdict-first + `@riptide-bot review` full mode = progressive disclosure).
- **Heuristics centralization (so we don't attempt the same thing twice):**
  - Retire `deepthink._gather_review_data()` re-fetch (diff/files/graphify duplicated by the bundle) — bundle is the single data path.
  - Migrate companion `last_sha` + skip JSON and deepthink SHA+24h cooldown JSON into StateStore SQLite — one answer to "already reviewed?", "in-flight?", "skipped?", enforced by webhook AND cron paths.
  - `classify_review_depth()`/`select_skills()` reused as-is for depth routing (both paths).
  - One comment owner: Tier-1 comment is the canonical thread; `assemble_review` new-comment path is retired in favor of the Tier-2 PATCH slot.
- **Model routing:** Ollama only for TRIVIAL depth (per user: "restricted to the trivial"); STANDARD/ARCH use LongCat.
- **Delegation:** One subagent to deepthink implementation.

### WS-4: Latency tolerance / progress UX — ABSORBED into WS-3 Stage 3
- Per-pass PATCH footers ("⚙️ deterministic pass done · 🧠 LLM enrichment in progress") are the WS-2 PATCH pattern applied per pass. No separate workstream.

### WS-5: High-level clarity — ABSORBED into WS-3 Stages 2/4
- Tier 1 is verdict-first; `@riptide-bot review` full mode is the deep-disclosure path; `@riptide-bot fix` is the only command that writes code. Progressive disclosure is the pipeline's shape, not a separate WS.

---

## Visual Verification Pipeline (Phase 5 — ADOPTED 2026-08-08)

**Context:** Research complete → `visual-verification-research.md` at workspace root (landscape matrix, empirical determinism spike, architecture). Goal: prove ONE integrated visual-verification pipeline on **hermes-webui-tests first** (stable single target), then roll to riptide, hermes-webui-extensions, gto-wizard-clone-v2. Deterministic pixel diffing in CI — robust, not flaky.

**Stack (compose, don't reinvent):**

| Layer | Tool | Role |
|-------|------|------|
| 1. Deterministic diff | **Playwright `toHaveScreenshot`** (built-in pixelmatch) | Baselines, platform-tagged on `ubuntu-latest` |
| 2. Self-healing | **refqa** (Python, restored 2026-08-08) | YAML smoke + selector self-healing; NOT pixel assertions |
| 3. Evidence | **proofshot** | Screenshots/GIF → PR comment |
| 4. Triage (DEFERRED) | **VRT** + local Ollama VLM | Commit-baseline review server — only if review volume demands |

**Key decisions (from research):**
- CI runner: **GitHub-hosted `ubuntu-latest`** — standardized fonts/OS kill visual flake; baselines platform-tagged (`chromium-linux`), matching Playwright's per-platform snapshot behavior.
- hermes-webui is Python + vanilla JS (no build step); **reuse `browser-smoke.yml` agent-free boot pattern** (`server.py` on ephemeral port) — no new serving invention.
- **`HERMES_WEBUI_SKIP_ONBOARDING=1`** in CI — fresh state lands on the chat shell (empirically verified: without it, all pages render the onboarding screen).
- **Flake strategy is data-backed:** the only nondeterminism found (spike, 1440×900) is the "Filter conversations…" search input placeholder AA — 1,804px / 0.14%, localized at (93,96). Absorb with `maxDiffPixels: 2000` or mask that region (masking = principled fix).
- agent-qa (npm) **dropped** — FSL-1.1 constraint; refqa covers the same role (smoke-only mode, reference comparison dormant). Autonoma (BUSL), Lost Pixel (sunsetting), Argos (heavy self-host) eliminated.
- riptide's proofshotter stays the evidence layer; its `localhost:8788` default already matches the hermes-webui dev target.

### WS-6: Pre-flight (hermes-webui-tests) — NEXT in visual track
- **Goal:** make the test repo CI-ready. It is **not a git repo yet**; `lib/auth-fixture.ts` hardcodes a session cookie + `:8788`; extensions need `HERMES_WEBUI_EXTENSION_MANIFEST` wiring.
- **Scope:** `git init` + push as `ChonSong/hermes-webui-tests`; replace hardcoded cookie with CI-safe auth (env-provided or in-process boot); add `.github/workflows/visual.yml` skeleton reusing the browser-smoke boot pattern.
- **Delegation:** one subagent, branch-based, one PR.

### WS-7: Deterministic visual baselines (CI)
- **Goal:** first committed baseline suite — Playwright `toHaveScreenshot` on key pages (chat shell, settings, sessions) under `HERMES_WEBUI_SKIP_ONBOARDING=1`.
- **CI:** checkout → boot `server.py` agent-free → `npx playwright test` with snapshots on `ubuntu-latest`; baselines committed and platform-tagged.
- **Controls:** `animations: 'disabled'` (default), `caret: 'hide'` (default), `maxDiffPixels: 2000` (or mask) on the search input; `--update-snapshots` on a manual trigger for intentional changes.
- **Delegation:** one subagent, one PR.

### WS-8: refqa smoke layer + proofshot evidence
- **Goal:** LLM self-healing smoke tests + visual evidence in the same pipeline.
- **Scope:** refqa YAML smoke tests for hermes-webui (new — existing shipped YAMLs target archived gto/polytopia apps); proofshot captures wired to the PR comment flow (existing `cli.py`).
- **Note:** refqa runs at ~30s/step on the free model — gate it to PR-triggered jobs or nightly, not every commit.
- **Delegation:** one subagent, one PR.

### WS-9: VRT self-hosted review server (DEFERRED / OPTIONAL)
- **Trigger:** only if commit-baseline review becomes insufficient (large teams, many baselines).
- **Scope:** VRT docker-compose + local Ollama VLM hybrid compare; reuses existing local AI infra (no GPU needed).
- **Status:** not scheduled — revisit after WS-7/8 prove out on hermes-webui-tests.

---

## Fix Pipeline Reliability (PR #174 — 2026-08-25)

### WS-F1: CI Verifier (Worker 9)
- **Problem:** Fixer declares success after local tests pass — doesn't check GitHub CI.
- **Solution:** New Conductor stage polls `gh pr checks` after push, classifies failures (fixable vs non-fixable), retries once for fixable.
- **Output:** `ci_result.json` with status, failed checks, fixable/non-fixable breakdown.
- **Pipeline:** Fix pipeline extended to 6 stages (probe → judge → artisan → engine → **ci_verifier** → scribe).

### WS-F2: Cleanliness (Worker 10)
- **Problem:** Review only checks code quality, not PR hygiene (conflicts, related PRs, test coverage, description).
- **Solution:** New Conductor stage evaluates 7 cleanliness signals from Probe output.
- **Output:** `cleanliness.json` with findings (severity-rated), score (0-100), summary.
- **Pipeline:** Review pipeline extended to 6 stages (probe → judge → artisan → engine → scribe → **cleanliness**).

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
WS-3 (NEXT)            → UNIFIED pipeline (Vision 3+4+5):
                         Stage 0 heuristics → Stage 1 bundle →
                         Stage 2 Tier 1 → Stage 3 enrich → Stage 4 commands
                         (needs WS-1, WS-2; absorbs former WS-4/WS-5)
   ↓
WS-6 → WS-7 → WS-8     → VISUAL VERIFICATION (Phase 5, adopted 2026-08-08):
                         pre-flight → Playwright baselines →
                         refqa smoke + proofshot evidence
                         (target: hermes-webui-tests first)
WS-9                    → deferred: VRT review server (only if needed)
```

WS-1 → WS-2 → WS-3 have hard dependencies. WS-4/WS-5 are absorbed into WS-3 (no separate PRs).
Visual track (WS-6→9) is independent of WS-3; WS-6 → WS-7 → WS-8 are sequential, WS-9 gated on demand.

---

## Open Questions for User

1. ~~WS-3 pass orchestration~~ **RESOLVED** — unified pipeline: multi-pass runs inside Companion's Tier-2 slot, editing the Tier-1 comment. Deepthink spawn path (`_spawn_deepthink`/`assemble_review` new-comment path) is retired, not run alongside.
2. ~~Comment editing~~ **RESOLVED** — confirmed in production for ELI5; extended to per-pass PATCHes on the same comment (Stage 3 footers).
3. **Test ingestion** — do we have access to CI test results via GitHub API for the context bundle (checks API), or only local tests? (WS-3 Pass D depends on this.)
4. **Ollama availability** — is Ollama always running on the host (localhost:43311)? Needed for WS-3 prep-pass.
5. ~~Progress UX~~ **RESOLVED** — edit the same comment per stage (per-pass footers), not separate status comments.
6. **State migration scope** — migrate companion skip/last_sha + deepthink cooldown into StateStore SQLite in WS-3, or as a small standalone PR first? (Heuristics centralization; touches both bots.)
