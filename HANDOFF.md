# Riptide Development — Session Handoff

**Date:** 2026-08-06  
**Compiled for:** Fresh session continuation  
**Repo:** `/home/sc/workspace/riptide`  
**Current branch:** `main` (`c896ec6`)

---

## 1. Immediate State

### Git Status
```text
On branch main
Your branch is up to date with 'origin/main'.
```

### Main Branch
- HEAD: `c896ec6` (Merge pull request #60)
- All CI green, 418 tests pass

### Key Files

| File | LOC | Purpose |
|------|-----|---------|
| `riptide/companion.py` | ~1030 | Bot 1: Deterministic analysis + TL;DR + ProofShot flagger |
| `riptide/diff_analyzer.py` | ~330 | Deterministic diff analysis (security, complexity, error handling) |
| `riptide/deepthink.py` | 709 | Bot 2: Cron polling + Hermes deep-think spawner |
| `riptide/proofshotter.py` | 778 | Bot 3: Visual verification |
| `riptide/fixer.py` | ~250 | Bot 2b: Autonomous fix |
| `riptide/webhook.py` | ~340 | FastAPI server |
| `riptide/state.py` | ~300 | StateStore (SHA dedup, migrations) |
| `riptide/labeler.py` | 381 | Label engine |
| `riptide/assemble_review.py` | 228 | Structured findings assembly |
| `riptide/orchestrator.py` | ~180 | Orchestrator (polling, deep-think spawn, diagram pre-generation) |

---

## 2. What Happened This Session

### Completed
- PR #60 (consolidation) merged — 390 tests, all CI green
- PR #57 closed (superseded by #60)
- PR #35, #36, #37 commented (already implemented in #60)
- PR #46, #47, #48, #49, #50, #52, #54, #55 commented (already implemented)
- GitHub Projects v2 created for Riptide development
- 5 draft issues added to project
- Critical assessment of Riptide vs CodeRabbit/Greptile completed
- Companion (T0) identified as the biggest gap — echoes PR titles

### Key Findings from Assessment
1. Companion produces no actionable findings — just rephrases PR titles
2. Excalidraw generates but doesn't post reliably
3. Deepthink is unreliable (3 attempts, variable output)
4. Graphify data exists but isn't fully utilized in reviews
5. No security analysis capability
6. No cross-file analysis in reviews

---

## 3. Storage Strategy (Decision)

**Dual-store approach:**
- Local SQLite (`riptide/state.py`) → fast SHA dedup, job tracking (<1ms)
- GitHub Projects v2 → canonical source of truth, collaboration, roadmap

**Project:** Riptide Development  
**Project ID:** `PVT_kwHOBRbF9s4Bfg5M`  
**URL:** https://github.com/users/ChonSong/projects/3  
**Fields:** Status, Priority (P0/P1/P2), Review Depth (Trivial/Inline/Standard/Arch), Sprint

---

## 4. Development Plan (Updated)

### Phase 1: Fix Foundation (Companion) ✅ COMPLETE
- [x] 1.1 Deterministic analysis (security, complexity, error handling) — `riptide/diff_analyzer.py`
- [x] 1.2 Graphify integration — blast radius cited in findings
- [x] 1.3 Structured comment template — verdict + findings + impact
- [x] 1.4 Only post when actionable findings exist — skips clean PRs

### Phase 2: Excalidraw Delivery
- [ ] 2.1 T0 includes diagram URL when generated
- [ ] 2.2 Deepthink attaches diagram deterministically

### Phase 3: Deepthink Reliability
- [ ] 3.1 Reduce to 1 attempt with better error handling
- [ ] 3.2 Calibration: track false-positive rate

### Phase 4: Review Pipeline (original plan, now valid)
- [ ] 4.1 Review-agnostic parsing
- [ ] 4.2 Scope classification
- [ ] 4.3 Blast radius enforcement
- [ ] 4.4 Incremental fix loop

---

## 5. Cron Job Prompt Sizes

*[Deferred — user wants to manually review potential savings first]*

| Component | Size | Notes |
|-----------|------|-------|
| Orchestrator prompt (medium PR) | ~12,700 chars | ~3,150 tokens |
| Orchestrator prompt (large PR) | ~17,400 chars | ~4,350 tokens |
| `deep-think` skill | ~20,000 chars | Loaded by cron jobs |
| `github-pr-lifecycle` skill | ~53,000 chars | Loaded by cron jobs |
| `excalidraw` skill | ~7,000 chars | If loaded |
| **Total per cron job** | **~90,000 chars** | **~22,000 tokens** |

User mentioned 12k target — currently at ~22k tokens total. *Review deferred to future session.*

---

## 6. Graphify Quick Reference

**Location:** `/home/sc/workspace/riptide/graphify-out/`  
**Graph:** 1612 nodes, 2300 edges, 117 communities  
**Freshness:** Built from commit `c896ec63` — run `graphify update .` after code changes

**Commands:**
```bash
graphify query "<question>"          # Scoped subgraph search
graphify explain "<concept>"         # Focused node explanation
graphify path "<A>" "<B>"            # Relationship between nodes
graphify affected <filename>          # What touches this file
graphify update .                    # AST-only refresh (no API cost)
```

**Top communities:** StateStore (55 connections), T0Orchestrator, Companion, proofshotter, webhook, handle_fix_command, excalidraw_renderer

---

## 7. User Preferences (DO NOT VIOLATE)

- NEVER merge any PR without explicit "merge it" from user
- NEVER close another user's PR without asking
- NEVER delete deterministic Python and replace with LLM
- NEVER force-push without explicit permission
- ALWAYS create feature branches, never commit to main directly
- ALWAYS run tests after changes: `python -m pytest riptide/tests/ -q`
- ALWAYS compile check: `python -m py_compile riptide/*.py`
- User is 'ChonSong' on GitHub — owns ALL GitHub communication
- CI passing is NOT implied approval to merge

---

## 8. Test & CI

```bash
# Full test suite
cd /home/sc/workspace/riptide && python -m pytest riptide/tests/ -q

# Compile check
python -m py_compile riptide/companion.py riptide/deepthink.py riptide/proofshotter.py riptide/webhook.py riptide/github_app.py riptide/fixer.py riptide/diff_analyzer.py

# PR status
gh pr view <number> --json statusCheckRollup

# CI run status
gh run list --branch <branch> --status failure
```

---

## 9. Open Issues / Concerns

1. **Companion SHA degradation duplicate bug:** If TL;DR generation fails, `_handle_degradation()` spawns self-heal but doesn't record SHA → potential duplicate comments
2. **Excalidraw not attached to PR comments reliably** — generated but not delivered
3. **Cron job prompts are 22k tokens** vs 12k target — need to discuss cost/latency tradeoff
4. **grafiphy/ directory was deleted** — functionality merged into `riptide/orchestrator.py` (deterministic renderer is superior to LLM layout)
5. **grafiphy/labeler.py was deleted** — 142-line duplicate of `riptide/labeler.py` (381 lines)

---

## 10. PR Recovery (Closed but Not Lost)

Closed PRs that retain origin branches can be reopened. PR #57's branch was deleted — its feature is preserved in #60:

| PR | Branch | Content |
|----|--------|---------|
| #57 | ❌ deleted | Auto-deploy revert — superseded by #60 (do not reopen) |
| #44 | `coderabbitai/autofix/f3c8c93` | CodeRabbit auto-fixes for #38 |

All other closed PRs were either merged or their features were incorporated into #60.

---

## 11. Next Session Starting Point

1. **Phase 1 is complete** — deterministic Companion analysis merged via PR #61
2. **Next: Phase 2** — Excalidraw delivery (T0 includes diagram URL, deterministic attachment)
3. Use `session_search` to find detailed context from this session if needed
4. Project board: https://github.com/users/ChonSong/projects/3

---

## 12. Key Session History References

Search these topics in session_search for full context:
- "duplicate StateStore orchestrator" — the critical finding from PR #60
- "companion echo TLDR" — the assessment findings
- "cron prompt sizes" — token usage analysis
- "closed PRs" — audit and recovery plan
- "github projects" — project setup
