# Riptide Development — Session Handoff

**Date:** 2026-08-13
**Compiled for:** Fresh session continuation
**Repo:** `/home/sc/workspace/riptide`
**Current branch:** `main` (`8055122`)

---

## 1. Immediate State

### Git Status
```text
On branch main
Your branch is up to date with 'origin/main'.
```

### Main Branch
- HEAD: `8055122` (fix(ci): simplify review-required gate to single rule — PR #119)
- All CI green, 15 review-required tests + 18 timing tests pass

### Key Files

| File | LOC | Purpose |
|------|-----|---------|
| `riptide/companion.py` | ~1207 | Bot 1: TL;DR + ELI5 + timing footer |
| `riptide/deepthink.py` | ~756 | Bot 2: Cron polling + Hermes deep-think spawner |
| `riptide/proofshotter.py` | ~797 | Bot 3: Visual verification + timing footer |
| `riptide/fixer.py` | ~370 | Bot 2b: Autonomous fix |
| `riptide/webhook.py` | ~480 | FastAPI server |
| `riptide/state.py` | ~472 | StateStore (SHA dedup, job tracking) |
| `riptide/labeler.py` | ~381 | Label engine |
| `riptide/assemble_review.py` | ~245 | Structured findings assembly + timing footer |
| `riptide/depth.py` | ~62 | ReviewDepth enum + classify_review_depth() |

---

## 2. What Happened This Session

### Merged PRs
- **#115** — `fix(poller)`: restrict fix search to comments + raise limit + trim fields
- **#118** — `fix(ci)`: require full deep-think review + follow-up commit for PRs with findings
- **#116** — `feat`: deterministic timing metrics for all 3 bots
- **#119** — `fix(ci)`: simplify review-required gate to single rule

### PR #119 Changes (now in main)

**Simplified CI gate to one rule:** if a review comment has `## 🔍 Findings` AND 🔴/🟡 in a table row, require at least one commit after that review's timestamp.

**Removed complexity:**
- Deep-think header detection (🎯 Summary / 🔍 Findings)
- PR review vs comment source confusion (`created_at` vs `submitted_at`)
- Bot vs human review parsing
- `issue_comment` trigger (only `pull_request` now)

**Before:** 125 lines workflow + 351 lines tests (125 + 351 = 476)
**After:** 146 lines workflow + 103 lines tests (146 + 103 = 249)

### Superseded / Closed PRs
- #88, #89, #90, #91, #95, #96, #97, #100, #107 — all superseded or stale
- #103, #109, #111, #112 — docs/design PRs, closed as stale
- #117 — superseded by #118

### Still Open
- None currently

---

## 3. Architecture Snapshot

### Three-Bot System

| Bot | Trigger | Output |
|-----|---------|--------|
| **Companion** | Webhook (PR open/sync) | Instant TL;DR + ELI5 + timing |
| **Deepthink** | Cron (15 min) + `@riptide-bot review` | Deep-think review with findings |
| **Proofshotter** | Cron (10 min) + `@riptide-bot proofshot` | Visual evidence (GIF/screenshots) |

### CI/CD Workflows

| Workflow | Purpose |
|----------|---------|
| `riptide-review-required.yml` | If review has findings, require follow-up commit |
| `test-required.yml` | Require paired test changes for feat/fix commits |
| `agentlint.yml` | AGENTS.md compliance checks |

### Cron Jobs

| Job | Schedule | Script |
|-----|----------|--------|
| `riptide-review-poll` | */15 * * * * | `riptide-review-poll.sh` |
| `riptide-proofshot-poll` | */10 * * * * | `riptide-proofshot-poll.sh` |

---

## 4. User Preferences (DO NOT VIOLATE)

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

## 5. Test & CI

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

## 6. Open Issues / Concerns

1. **Cron job prompts are ~22k tokens** vs 12k target — deferred discussion
2. **Excalidraw not attached to PR comments reliably** — generated but delivery inconsistent
3. **State DB stale pending jobs** — can block review spawns (manual cleanup may be needed)

---

## 7. Next Session Starting Point

1. **PR #119 is merged** — CI gate is simplified to single rule
2. **All docs updated** — HANDOFF.md, CHANGELOG.md current
3. **Possible follow-up** — token optimization for cron prompts (deferred from earlier)
4. Use `session_search` to find detailed context from this session if needed

---

## 8. Key Session History References

Search these topics in session_search for full context:
- "riptide-review-required" — the CI gate implementation
- "deterministic timing metrics" — bot output footer work
- "poller fix" — PR #115 changes
- "superseded PRs" — which PRs were closed and why
- "stale pending job" — state DB blocking review spawns
