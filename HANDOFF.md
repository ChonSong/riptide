# Riptide — Session Continuity Handoff

> **Last Updated:** 2026-08-27
> **Main HEAD:** `e81dfd9`
> **Production:** Running `e81dfd9`
> **Open PRs:** #184 (stratified sessions), #182 (startup recovery poller)

---

## 1. Project Overview

Riptide is a self-hosted GitHub App with stratified Hermes sessions for PR review.

### Architecture

```
GitHub Webhook → FastAPI /webhook (server.py)
  ├─ pull_request → Companion.run_for_pr() (semaphore-guarded)
  ├─ issue_comment (@riptide-bot review) → AsyncConductor → stratified pipeline
  └─ issue_comment (@riptide-bot fix) → handle_fix_command()

Stratified Pipeline:
  AsyncConductor.run() → dispatch("probe") → Hermes session A
                        → /conductor/resume → dispatch("judge") → Hermes session B
                        → /conductor/resume → dispatch("artisan") → Hermes session C
                        → ... → track complete

Cron (Hermes) → riptide/poller.py → poll() → AsyncConductor
```

### Worker Roles

| Worker | Type | What it does | Session tools |
|--------|------|--------------|---------------|
| **Probe** | Stratified | Gather deterministic context (diff, graphify, context bundle) | terminal, read_file |
| **Judge** | Stratified | Evaluate code quality, dedup findings | read_file, write_file |
| **Artisan** | Stratified | Generate Excalidraw diagram from findings | read_file, write_file, patch, terminal |
| **Engine** | Stratified | Execute shell commands, capture results | terminal |
| **Warden** | Stratified | Verify outputs meet acceptance criteria | read_file, terminal |
| **Scribe** | Stratified | Assemble and post final review comment | read_file, write_file, terminal |
| **CI Verifier** | Stratified | Poll GitHub CI checks, classify failures | terminal |
| **Test Oracle** | Stratified | Run targeted tests from PR diff | terminal, read_file |
| **Review Memory** | Stratified | Store outcomes, retrieve historical context | terminal, read_file, write_file |
| **Documentarian** | Stratified | Update graphify + changelog on merge | terminal |

### State

- SQLite at `~/.local/share/riptide/state.db`
- Tables: `deliveries` (webhook dedup), `pr_heuristics` (SHA + timestamp), `jobs` (spawn queue), `work_queue` (durable queue), `review_memory` (historical outcomes), `review_profiles` (repo aggregates)
- Work-state JSON at `~/.hermes/state/riptide-work-state.json`
- Tracks pipeline progress per PR
- Enables resume on completion callback

---

## 2. What Was Done This Session

### Merged to Main

| Commit | Description |
|--------|-------------|
| `e81dfd9` | Revert "feat(recovery): startup poller with PID protection + tunable thresholds" |
| `34d4e23` | feat(recovery): startup poller with PID protection + tunable thresholds |
| `de074af` | fix(ci-verifier): handle uppercase state values from gh pr checks |
| `64612b2` | feat: add ollama-heal systemd service for auto-restart (#159) |
| `fa99b25` | feat(companion): interactive checkbox buttons for PR review commands (#156) |
| `572f8bb` | fix(webhook): durable work queue with startup recovery, SQLite fixes, and tests (#171) |
| `5aee067` | feat(review-memory): SQLite persistence and historical context injection (#167) |
| `797f288` | feat(interaction-handler): unified @riptide-bot command router (#165) |
| `af08090` | feat(conductor): wire Conductor pipeline into deepthink and webhook (#164) |
| `0c40293` | fix(fixer/state): DB lock retry, escape wildcards, fix queue position (#160) |
| `d44b7f0` | feat(arch-documentarian): graphify update and changelog on merge (#169) |
| `3ff713c` | feat(test-oracle): targeted test execution from PR diff (#168) |
| `270aec0` | feat(diagram-analyst): annotated Excalidraw from review findings (#166) |
| `0034821` | feat(observability): Huey task queue, state machine, Prometheus metrics, and CI verifier (#172) |
| `df31b40` | feat(companion): integrate ollama_heal for self-healing on Ollama failure (#150) |
| `a597b79` | feat(assemble): ADHD-friendly review output with findings cap and time estimates |

### Key Changes

1. **Stratified Hermes sessions** — Each worker gets its own focused session (PR #184)
2. **Pipeline watchdog** — Stuck-pipeline detection and auto-cleanup
3. **Monitoring endpoints** — `/conductor/status/{track_id}`, `/conductor/stuck`, `/conductor/cleanup`
4. **Idempotent pipeline creation** — Re-triggering returns existing track
5. **Output validation** — Each worker's output validated before next dispatch
6. **Retry logic** — Failed workstreams retry up to 3 times
7. **SQLite WAL mode** — Concurrent cron jobs no longer deadlock
8. **Startup recovery** — Pending work recovered on process restart
9. **review_memory table** — Schema ownership in `_init_db`, v9 migration
10. **CodeRabbit review fixes** — SQL rollback, schema ownership, flaky test removal

### Open PRs

| # | Title | Branch | Status | Notes |
|---|-------|--------|--------|-------|
| **184** | feat: stratified Hermes sessions for worker roles | `refactor/stratified-sessions` | OPEN | 6 commits, 677 tests pass |
| **182** | feat(recovery): startup poller with PID protection + tunable thresholds | `feat/startup-recovery-poller` | OPEN | 2 commits, 51 tests pass |

---

## 3. Deployment

### Auto-Deploy Flow

1. PR merges into `main` → GitHub sends `pull_request` webhook (`action: closed, merged: true`)
2. `webhook.py` detects merge → triggers `scripts/deploy.sh`
3. `deploy.sh` → `git pull origin main --ff-only` → clean `__pycache__` → `systemctl --user restart riptide.service` → smoke test
4. Service runs the new code

### Manual Deploy

```bash
cd /home/sc/workspace/riptide
git checkout main
git pull --ff-only
systemctl --user restart riptide.service
# Verify
curl -s http://localhost:8477/webhook/github -X POST -H "Content-Type: application/json" -d '{"test":1}'
# Expected: 401 (service running, sig verification blocks test payload)
```

### Production Server

- **Process:** `python server.py --prod` (gunicorn)
- **Port:** 8477
- **Service:** `riptide.service` (systemd --user)
- **Logs:** `/home/sc/.local/share/riptide/riptide.log`
- **Deploy log:** `/tmp/riptide-deploy.log`

---

## 4. Testing

### Run All Tests

```bash
cd /home/sc/workspace/riptide
python -m pytest riptide/tests/ -q
```

**Expected:** ~677 passed, ~4 pre-existing failures (test_pipeline.py — unrelated to changes)

### Key Test Files

| File | Coverage |
|------|----------|
| `test_stratified_sessions.py` | Stratified session architecture (22 tests) |
| `test_e2e_pipeline.py` | End-to-end pipeline chain (5 tests) |
| `test_watchdog.py` | Stuck-pipeline detection and cleanup (10 tests) |
| `test_state_transactions.py` | SQLite WAL mode, atomic claims (31 tests) |
| `test_wal_concurrency.py` | Concurrent reservation stress test (9 tests) |
| `test_work_queue_recovery.py` | Startup recovery (11 tests) |
| `test_companion.py` | Companion flow, two-tier response (97 tests) |
| `test_deepthink.py` | Stratified pipeline creation (43 tests) |
| `test_bot_autonomy.py` | Spawn retry/backoff (15 tests) |

### Manual Testing

```bash
# Trigger review (from PR comment)
@riptide-bot review

# Trigger fix (from PR comment)
@riptide-bot fix

# Check Hermes cron
hermes cron list

# Check specific job output
cat ~/.hermes/cron/output/<JOB_ID>/*.md

# Check pipeline status
curl http://localhost:8477/conductor/status/riptide-review-ChonSong-riptide-42

# Check stuck pipelines
curl http://localhost:8477/conductor/stuck

# Clean up stuck pipelines
curl -X POST http://localhost:8477/conductor/cleanup
```

---

## 5. Known Issues & Blockers

### ⚠️ Hermes Scheduler Instability (CRITICAL)

**Symptom:** Deep-think reviews are spawned but never complete. Hermes output shows:
```
# Cron job removed without producing output
- dispatch claimed: 1/1
- run claimed at: <time> by <host>:<pid>
- removed at: <time>
This one-shot job's dispatch was claimed, but the run never completed
```

**Impact:** Reviews are not posted to GitHub.

**Status:** Hermes infrastructure issue — Riptide code is correct. Stratified sessions mitigate this by isolating workers.

**Workaround:** Retry `@riptide-bot review` multiple times. Eventually one may complete.

### ⚠️ Pre-existing Test Failures

- `test_pipeline.py` (4 failures) — `test_concurrent_writes` and `test_create_workstream_*` — fail on main too, unrelated to changes

### ⚠️ Diagram URLs Don't Embed in GitHub

Excalidraw URLs render as clickable links, not embedded images. GitHub markdown can't embed interactive whiteboards.

---

## 6. Key Files Reference

### Production Code

| File | Purpose |
|------|---------|
| `server.py` | FastAPI/uvicorn entry point |
| `riptide/webhook.py` | Webhook handler, deploy trigger, AsyncConductor integration |
| `riptide/companion.py` | Bot 1: TL;DR + ELI5 + timing footer |
| `riptide/deepthink.py` | Bot 2: Cron polling + stratified pipeline spawner |
| `riptide/fixer.py` | Bot 2b: Autonomous fix |
| `riptide/poller.py` | Cron entry point for Bot 2/3 discovery |
| `riptide/assemble_review.py` | Post-process LLM findings into review comment |
| `riptide/state.py` | SQLite-backed state (dedup, jobs, heuristics, review_memory) |
| `riptide/labeler.py` | GitHub label engine |
| `riptide/depth.py` | ReviewDepth enum + classifier |
| `riptide/diagram_analyst.py` | Worker 4: Excalidraw diagram generation |
| `riptide/interaction_handler.py` | Worker 7: Unified @riptide-bot command router |
| `riptide/test_oracle.py` | Worker 5: Targeted test execution |
| `riptide/documentarian.py` | Worker 8: Post-merge graphify + changelog |
| `riptide/review_memory.py` | Worker 6: Historical review context |
| `riptide/metrics.py` | Prometheus instrumentation |
| `riptide/pipeline/async_conductor.py` | State-machine conductor (chains stratified sessions) |
| `riptide/pipeline/session_spawner.py` | Spawns stratified Hermes sessions |
| `riptide/pipeline/work_state.py` | Work-state JSON management |
| `riptide/pipeline/conductor.py` | Synchronous conductor (legacy) |
| `riptide/pipeline/roles.py` | Worker role definitions |

### Config & Deploy

| File | Purpose |
|------|---------|
| `scripts/deploy.sh` | Auto-deploy on merge |
| `scripts/ephemeral-test.sh` | Container-based testing |
| `Dockerfile` | Container image |
| `docker-compose.yml` | Local dev environment |
| `requirements.txt` | Python dependencies |
| `proofshot.config.json` | Example proofshot config schema |

---

## 7. Architecture Decision Records

### ADR-001: Stratified Hermes Sessions (2026-08-27)

**Context:** Workers previously ran as Python classes in a single Hermes session. This caused context bloat and instability.

**Decision:** Each worker gets its own focused Hermes session with role-specific context, skills, and memory.

**Consequences:**
- (+) Isolated context windows prevent bloat
- (+) Per-worker retry and recovery
- (+) Role-specific tool restrictions
- (-) More Hermes sessions to manage
- (-) Completion callback chain adds complexity

### ADR-002: SQLite WAL Mode (2026-08-26)

**Context:** Concurrent cron jobs deadlocked on SQLite writes.

**Decision:** Use WAL mode with busy_timeout=30000 and synchronous=NORMAL.

**Consequences:**
- (+) Concurrent reads and writes
- (+) Better performance under load
- (-) Requires SQLite 3.7.0+

### ADR-003: Idempotent Pipeline Creation (2026-08-27)

**Context:** Re-triggering `@riptide-bot review` created duplicate workstreams.

**Decision:** `create_stratified_*_pipeline` returns existing track if one exists.

**Consequences:**
- (+) No duplicate workstreams on re-trigger
- (+) Safe to retry without side effects
- (-) Must explicitly cancel to restart a pipeline

---

*Generated from analysis of Riptide codebase + competitor research + i-have-adhd principles*
*Last updated: 2026-08-27*
