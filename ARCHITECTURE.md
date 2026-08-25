# Riptide — Architecture

**Date:** 2026-08-25
**Status:** Active — Conductor pipeline with CI Verifier + Cleanliness stages
**Source:** Consolidated from `PLAN.md`, `skills/riptide/riptide-bot-architecture/SKILL.md`, `skills/riptide/riptide-review-pipeline/SKILL.md`

---

## 1. System Overview

Riptide is a self-hosted GitHub App with a **Conductor-orchestrated multi-stage pipeline** that automates PR review, fixing, and visual verification.

### Design Principles

1. **Deterministic Data Inputs** — All decisions flow from deterministic Python (diff analysis, graphify, context bundles), not LLM echo.
2. **Two-Tiered Response** — Fast deterministic Tier 1 (verdict + findings), then async LLM enrichment (ELI5, personality).
3. **Multi-Pass LLM** — Small LLM calls, each analyzing a different aspect (security, complexity, architecture, tests).
4. **Latency Tolerance** — Enrichment may take minutes; progress indicators keep users informed.
5. **High-Level Clarity** — Verdict-first output, capped at 5 findings, with time estimates.

### Model Tiering

| Model | Role | Trust |
|-------|------|-------|
| **Ollama (qwen2.5-coder:7b)** | Enricher / prep worker — paraphrase findings, draft ELI5, GIF selection | Free but weak. Never trusted to reduce, decide, or implement. |
| **LongCat-2.0 (via `longcat` provider)** | Judgment, decisions, implementation, final review | The trusted model. Deterministic data drives what it sees. |
| **Deterministic Python** | Diff analysis, classification, graphify, proofshot, labeler rules, state/dedup | Source of truth. Everything flows from this. |

---

## 2. Pipeline Architecture

### 2.1 High-Level Flow

```
GitHub Webhook → FastAPI /webhook (server.py)
  ├─ pull_request → Companion.run_for_pr() (semaphore-guarded)
  ├─ issue_comment (@riptide-bot review) → handle_review_command() → Conductor review pipeline
  ├─ issue_comment (@riptide-bot fix) → handle_fix_command() → Conductor fix pipeline
  └─ issue_comment (@riptide-bot proofshot) → handle_manual_command()

Cron (Hermes) → riptide/poller.py → poll() → Conductor review pipeline
                                              → Companion.run_for_pr() (no webhook_received_at)
```

### 2.2 Conductor Pipeline Stages

| Stage | Role | File | What it does |
|-------|------|------|--------------|
| 1 | probe | `pipeline/probe.py` | Gathers diff, context bundle, graphify, cleanliness signals |
| 2 | judge | `pipeline/judge.py` | Evaluates diff, dedups findings, produces structured findings |
| 3 | artisan | `pipeline/artisan.py` | Generates Excalidraw diagram |
| 4 | engine | `pipeline/engine.py` | Executes shell commands (upload diagram, run tests, push) |
| 5 | ci_verifier | `pipeline/ci_verifier.py` | Polls GitHub CI, classifies failures, retries once if fixable |
| 6 | scribe | `pipeline/scribe.py` | Posts review/fix summary comment, updates state |
| 7 | cleanliness | `pipeline/cleanliness.py` | Evaluates PR hygiene: conflicts, related PRs, test coverage, description |

### 2.3 Review Pipeline (6 stages)

```
probe → judge → artisan → engine → scribe → cleanliness
```

### 2.4 Fix Pipeline (6 stages)

```
probe → judge → artisan → engine → ci_verifier → scribe
```

---

## 3. Worker Specifications

### 3.1 Probe (`pipeline/probe.py`)

**Purpose:** Gather all deterministic context for a PR.

**Signals gathered:**
- PR metadata (title, author, body, timestamps)
- Changed files with patches
- Diff analysis (security, complexity, error handling)
- Context bundle (concepts, blast radius, taxonomy)
- Graphify analysis (god nodes, communities)
- Previous findings (StateStore dedup)
- **Cleanliness signals** (7 checks):
  1. Merge conflicts (`gh pr view --json mergeable`)
  2. Related open PRs touching same files
  3. Test coverage (source-only changes without tests)
  4. PR description quality (body length, issue links)
  5. Commit hygiene (Conventional Commits compliance)
  6. PR staleness (age > 14/30 days)
  7. CI pre-check (existing failures before review)

**Output:** `context.json` with all signals.

### 3.2 Judge (`pipeline/judge.py`)

**Purpose:** Evaluate diff, dedup findings, produce max 3 NEW findings.

**Focus areas:** dead code, redundant imports, edge cases.

**Dedup:** Against previous findings from StateStore (line-based).

**Output:** `findings.json` with max 3 findings.

### 3.3 Artisan (`pipeline/artisan.py`)

**Purpose:** Create/modify files with exact content (Excalidraw diagrams).

**Input:** `findings.json` from Judge.

**Output:** Diagram artifacts.

### 3.4 Engine (`pipeline/engine.py`)

**Purpose:** Execute exact shell commands, capture exit code.

**Security:** `shell=True` but only trusted callers (Conductor). No unsanitized user input.

**Commands:** Upload diagram, run tests, push commits.

### 3.5 CI Verifier (`pipeline/ci_verifier.py`)

**Purpose:** Poll GitHub CI checks after a fix push, classify failures.

**Polling:** `gh pr checks` every 30s, 10-minute timeout.

**Classification:**
- **FIXABLE:** test-required, agentlint — code/test addressable
- **NON-FIXABLE:** CodeRabbit, riptide-review-required, GitGuardian — needs human

**Retry:** Max 1 retry for fixable failures. Escalate non-fixable to human.

**Output:** `ci_result.json` with status, failed checks, fixable/non-fixable breakdown.

### 3.6 Cleanliness (`pipeline/cleanliness.py`)

**Purpose:** Evaluate PR hygiene signals from Probe output.

**Findings:** Severity-rated (critical/warning/info) with actionable suggestions.

**Score:** 0-100 cleanliness score.

**Output:** `cleanliness.json` with findings, score, summary.

### 3.7 Scribe (`pipeline/scribe.py`)

**Purpose:** Update work-state.json and post GitHub comments.

**Actions:**
- `update_workstream()` — mark workstream status
- `post_review_with_assembler()` — post review via assemble_review.py
- `post_pr_comment()` — post comment via gh CLI
- `record_review_complete()` — store findings in StateStore

### 3.8 Warden (`pipeline/warden.py`)

**Purpose:** Verify outputs meet acceptance criteria.

**Checks:** File exists, file size, JSON valid, JSON schema, Python syntax, contains text.

### 3.9 Diagram Pipeline (`grafiphy/`)

**Entry points:**
- `pre_generate_diagram()` — before LLM spawn (Python thread, 30s timeout)
- `orchestrate()` — after review (Hermes cron, 120s+ timeout)

**Renderer sections (9):**
1. Title (PR info + risk level)
2. Distance-Radius Network Map
3. Codebase Directory Tree
4. PR Scope
5. Graphify Analysis
6. Code Chunks + WHY (post-LLM only)
7. Human-Readable Narrative
8. Findings (post-LLM only)
9. Suggested Changes (post-LLM only)

**Critical pitfall:** `render_review()` accepts 12 parameters but `pre_generate_diagram()` originally passed only 2. Must pass `file_tree`, `repo_tree`, `human_narrative`, `distance_map` to avoid blank sections.

---

## 4. Data Flow

### 4.1 Webhook Path

```
GitHub → POST /webhook/github
  │
  ├─ verify_webhook_signature()
  ├─ reserve_delivery(delivery_id)     # idempotency dedup
  ├─ bind_trace_context(delivery_id)   # structlog contextvars
  │
  ├─ handle_pull_request()
  │   ├─ installation_id present → GitHubAppClient (JWT auth)
  │   ├─ installation_id None + repo in WATCHED_REPOS → GhCliClient fallback (PAT auth)
  │   ├─ installation_id None + repo NOT in WATCHED_REPOS → skip with log
  │   │
  │   ├─ Companion thread (daemon)
  │   │   └─ companion.run_for_pr(client=gh_cli_if_fallback)
  │   │       ├─ depth_decision()
  │   │       ├─ build_context_bundle()
  │   │       ├─ _generate_tldr_with_retry()
  │   │       ├─ _generate_eli5()
  │   │       └─ _format_comment() + post_pr_comment()
  │   │
  │   ├─ Labeler thread (daemon)
  │   └─ auto-deploy if merged into default_branch
  │
  └─ handle_issue_comment()
      ├─ Route 1: companion skip/resume
      ├─ Route 2: @riptide-bot review → handle_review_command()
      ├─ Route 2b: @riptide-bot fix → handle_fix_command()
      ├─ Route 2c: @riptide-bot relabel
      └─ Route 3: @riptide-bot visual
```

### 4.2 Cron Path

```
Cron (Hermes) → riptide/poller.py → poll()
  ├─ _spawn_deepthink() → Conductor review pipeline
  └─ Companion.run_for_pr() (no webhook_received_at)
```

### 4.3 Fix Path

```
@riptide-bot fix → webhook.py Route 2b
  → fixer.handle_fix_command() — auth gate, eligibility check
    → Conductor fix pipeline (6 stages)
      → Hermes session: verify findings → edit → test → push → poll CI → post summary
```

---

## 5. Observability Stack

### 5.1 Structured Metrics (`prometheus_client`)

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `SPAWNS_TOTAL` | Counter | worker, status | Track spawn success/failure |
| `SPAWN_FAILURES_TOTAL` | Counter | worker, reason | Track failure reasons |
| `FIX_DURATION` | Histogram | — | Fix session duration |
| `REVIEW_DURATION` | Histogram | — | Review session duration |
| `DB_LOCK_WAITS_TOTAL` | Counter | — | SQLite lock contention |
| `API_CALL_DURATION` | Histogram | endpoint, status | GitHub API latency |
| `ACTIVE_WORKERS` | Gauge | — | Concurrent workers |
| `DEPLOY_TOTAL` | Counter | — | Deploy count |

Served via `/metrics` endpoint on FastAPI (same port).

### 5.2 Trace Propagation (`contextvars` + `structlog`)

```python
# webhook.py — bind once at entry
bind_trace_context(delivery_id, event=event)

# All downstream log lines automatically include delivery_id
logger.info("Companion flow spawned")
# → {"event": "Companion flow spawned", "delivery_id": "xxx", ...}
```

**Cross-process boundary:** `contextvars` does NOT propagate to subprocess-spawned Hermes cron jobs. Solution: embed `delivery_id` in the cron prompt header (`## Trace: delivery_id=xxx job_id=yyy`).

### 5.3 DB Lock Resilience (`tenacity`)

Two retry configs in `riptide/state.py`:

```python
# Fast path: webhook handler (3x, 0.2-1.0s backoff)
retry_db_fast = retry(
    retry=retry_if_exception_type(sqlite3.OperationalError),
    wait=wait_exponential(multiplier=0.2, min=0.2, max=1.0),
    stop=stop_after_attempt(3),
    reraise=True,
)

# Slow path: cron jobs / cleanup (5x, 2-10s backoff)
retry_db_background = retry(
    retry=retry_if_exception_type(sqlite3.OperationalError),
    wait=wait_exponential(multiplier=1.0, min=2.0, max=10.0),
    stop=stop_after_attempt(5),
    reraise=True,
)
```

Applied to: `reserve_delivery()`, `create_job()`, `mark_complete()`, `mark_failed()`, `reserve_job()`.

---

## 6. Companion Spawn Fallback

### Problem
When the GitHub App is not installed on a repo, webhook events have `installation_id: None`. The handler returns early with "No installation ID, skipping" — no companion TLDR fires.

### Solution
In `handle_pull_request()`, when `installation_id` is None AND `repo_full` is in `RIPTIDE_WATCHED_REPOS`, fall back to `GhCliClient` (PAT-authenticated gh CLI):

```python
# webhook.py
WATCHED_REPOS = [
    r.strip()
    for r in os.environ.get("RIPTIDE_WATCHED_REPOS", "...").split(",")
    if r.strip()
]

# In handle_pull_request():
using_gh_cli_fallback = False
if not installation_id:
    if repo_full in WATCHED_REPOS:
        gh_cli = get_gh_cli_client()
        if gh_cli:
            installation_id = None  # gh CLI ignores this param
            using_gh_cli_fallback = True
        else:
            log.info("gh CLI unavailable, skipping")
            return
    else:
        log.info("Not in WATCHED_REPOS, skipping")
        return

# Pass to companion via client override
companion.run_for_pr(
    installation_id, owner, repo, pr_number,
    title, author, files,
    client=gh_cli if using_gh_cli_fallback else None,
)
```

### Companion `client` Override Pattern
`Companion.run_for_pr()` accepts an optional `client` parameter. When provided, it uses this client instead of `self.client` for all API calls.

### Diagnostic Log Lines
- `"No installation ID for {repo} — using gh CLI fallback"` → fallback active
- `"No installation ID for {repo} and gh CLI unavailable, skipping"` → fallback failed
- `"No installation ID for {repo} (not in WATCHED_REPOS), skipping"` → intentionally skipped

---

## 7. Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `GITHUB_APP_ID` | `4262983` | App identifier |
| `GITHUB_PRIVATE_KEY_PATH` | `""` | Path to .pem JWT key |
| `GITHUB_WEBHOOK_SECRET` | `""` | Webhook signature verification |
| `GITHUB_APP_SLUG` | `octopus-selfhost` | Bot login slug |
| `RIPTIDE_DATA_DIR` | `/tmp/riptide` | metadata.db location |
| `RIPTIDE_STATE_DB` | `~/.local/share/riptide/state.db` | jobs, deliveries, heuristics |
| `RIPTIDE_WATCHED_REPOS` | (8 repos) | Repos to act on even without App install |
| `RIPTIDE_COMPANION_REPOS` | `""` | Repos companion TLDR is active for |
| `RIPTIDE_T0_FALLBACK` | `""` | Set to `1` to use legacy T0 orchestrator |
| `RIPTIDE_DEPLOY_BRANCH` | `main` | Auto-deploy trigger branch |
| `RIPTIDE_DEPLOY_SCRIPT` | `/home/sc/workspace/riptide/scripts/deploy.sh` | Deploy entry point |
| `RIPTIDE_FIX_MODEL` | `LongCat-2.0` | Fixer model override |
| `RIPTIDE_FIX_PROVIDER` | `longcat` | Fixer provider override |
| `RIPTIDE_DEEPTHINK_MODEL` | `LongCat-2.0` | Deepthink model override |
| `RIPTIDE_DEEPTHINK_PROVIDER` | `longcat` | Deepthink provider override |
| `RIPTIDE_WORKSPACE_ROOT` | `/home/sc/workspace` | Spawned session PYTHONPATH |
| `RIPTIDE_OUR_USERNAME` | `ChonSong` | GitHub username for auth |
| `RIPTIDE_OUR_ORG` | `ChonSong` | GitHub org for ownership |
| `RIPTIDE_PROOFSHOT_TIMEOUT` | `180` | Proofshot watchdog timeout (s) |
| `RIPTIDE_INTERACTION_COOLDOWN` | `300` | Command cooldown (s) |

---

## 8. Database Schema

```sql
-- Existing tables
CREATE TABLE deliveries (
  delivery_id TEXT PRIMARY KEY,
  pr_number INTEGER,
  action TEXT,
  processed_at TEXT
);

CREATE TABLE pr_heuristics (
  pr_key TEXT PRIMARY KEY,
  head_sha TEXT,
  reviewed_at TEXT,
  skip INTEGER DEFAULT 0
);

CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  pr_number INTEGER,
  tier TEXT,
  status TEXT,
  created_at TEXT,
  completed_at TEXT
);

CREATE TABLE work_queue (
  id TEXT PRIMARY KEY,
  pr_number INTEGER,
  kind TEXT,
  status TEXT DEFAULT 'pending',
  created_at TEXT,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_work_queue_kind_status ON work_queue (kind, status);
CREATE INDEX IF NOT EXISTS idx_work_queue_status_created_at ON work_queue (status, created_at);

-- Review memory (Worker 6)
CREATE TABLE review_memory (
  id TEXT PRIMARY KEY,
  pr_key TEXT NOT NULL,
  pr_number INTEGER,
  owner TEXT,
  repo TEXT,
  head_sha TEXT,
  findings_count INTEGER,
  critical_count INTEGER,
  warning_count INTEGER,
  verdict TEXT,
  user_feedback INTEGER,
  created_at TEXT,
  metadata TEXT
);

CREATE TABLE review_profiles (
  repo TEXT PRIMARY KEY,
  total_reviews INTEGER DEFAULT 0,
  common_findings TEXT DEFAULT '[]',
  last_review_at TEXT,
  updated_at TEXT
);

-- Diagram insights (Worker 4)
CREATE TABLE diagram_insights (
  id TEXT PRIMARY KEY,
  pr_number INTEGER,
  owner TEXT,
  repo TEXT,
  diagram_url TEXT,
  narrative TEXT,
  confidence REAL,
  gaps TEXT,  -- JSON array
  annotations TEXT,  -- JSON array
  created_at TEXT
);
```

---

## 9. File Structure

```
riptide/
├── server.py                          # FastAPI/uvicorn entry point
├── webhook.py                         # Webhook handler, deploy trigger, routing
├── companion.py                       # Bot 1: TL;DR + ELI5 + timing footer
├── deepthink.py                       # Bot 2: Cron + @riptide-bot review spawner
├── fixer.py                           # Bot 2b: Autonomous fix (Conductor pipeline)
├── poller.py                          # Cron entry point for Bot 2/3 discovery
├── proofshotter.py                    # Bot 3: Visual regression capture
├── assemble_review.py                 # Post-process LLM findings into review comment
├── state.py                           # SQLite store (jobs, deliveries, heuristics, work_queue)
├── labeler.py                         # GitHub label engine
├── depth.py                           # ReviewDepth enum + classifier
├── metrics.py                         # Prometheus counters/histograms/gauges
├── gh_cli_client.py                   # PAT-based gh CLI client (fallback path)
├── github_app.py                      # JWT-based GitHub App client
├── ollama_heal.py                     # Ollama self-healing (systemd/Docker)
│
├── pipeline/                          # Conductor pipeline stages
│   ├── conductor.py                   # Orchestrator — dispatches workers, manages tracks
│   ├── probe.py                       # Stage 1: Context gathering + cleanliness signals
│   ├── judge.py                       # Stage 2: Findings evaluation + dedup
│   ├── artisan.py                     # Stage 3: Diagram generation
│   ├── engine.py                      # Stage 4: Shell command execution
│   ├── ci_verifier.py                 # Stage 5: CI status polling + classification
│   ├── cleanliness.py                 # Stage 6: PR hygiene evaluation
│   ├── scribe.py                      # Stage 7: Comment posting + state updates
│   ├── warden.py                      # Output verification
│   ├── roles.py                       # Worker role definitions
│   ├── work_state.py                  # Track/workstream state management
│   └── recovery.py                    # Stall detection + recovery
│
├── grafiphy/                          # Diagram generation
│   ├── orchestrator.py                # pre_generate_diagram() + orchestrate()
│   ├── excalidraw_renderer.py         # render_review() — Excalidraw JSON builder
│   └── diagram_enricher.py            # Annotation overlay
│
├── skills/                            # Hermes skills
│   ├── riptide/SKILL.md               # Main Riptide skill
│   ├── github-pr-lifecycle/SKILL.md   # PR lifecycle management
│   └── deep-think/SKILL.md            # Deep-think review skill
│
├── scripts/
│   ├── deploy.sh                      # Auto-deploy: pull, clean, restart, smoke test
│   ├── upload_excalidraw.py           # Fallback Excalidraw upload
│   └── ephemeral-test.sh              # Container-based testing
│
├── .github/workflows/
│   ├── riptide-review-required.yml    # CI gate: fail-closed on no review
│   └── test-required.yml              # CI gate: feat/fix commits need tests
│
└── tests/
    ├── test_ci_verifier.py            # CI polling, classification, timeouts (27 tests)
    ├── test_cleanliness.py            # Cleanliness evaluation, Probe signals (12 tests)
    ├── test_companion.py              # Companion flow, two-tier response (97 tests)
    ├── test_deepthink.py              # Spawn flow, temp file detection (43 tests)
    ├── test_assemble_review.py        # Timing assembly (12 tests)
    ├── test_review_required.py        # CI gate logic (14 tests)
    └── ...                            # 754 total tests
```

---

## 10. Implementation Phases

### Phase 1: Foundation (Days 1-2) — ✅ COMPLETE
- Interaction Handler + Diagram Analyst wiring

### Phase 2: Intelligence (Days 3-5) — ✅ COMPLETE
- Test Oracle + Review Memory

### Phase 3: Evolution (Days 6-8) — ✅ COMPLETE
- Architecture Documentarian + Proofshotter integration

### Phase 4: Polish (Days 9-10) — ✅ COMPLETE
- ADHD-friendly formatting + metrics

### Phase 5: Fix Pipeline Reliability (Day 11) — ✅ PR #174
- CI Verifier (Worker 9) — CI polling + classification
- Cleanliness (Worker 10) — PR hygiene evaluation

---

## 11. Success Metrics

### Review Quality

| Metric | Current | Target |
|--------|---------|--------|
| Reviews with diagrams | 80% | 95% |
| Reviews with test results | 60% | 80% |
| Reviews referencing history | 40% | 60% |
| Manual command success rate | 95% | 99% |
| Findings per review (avg) | ≤5 | ≤5 |
| Time estimates included | 90% | 95% |

### System Health

| Metric | Current | Target |
|--------|---------|--------|
| Proofshotter success rate | 68% | 90% |
| Bot state isolation | All in SQLite | All in SQLite |
| Cross-bot dedup | Full | Full |
| Deploy race condition | Single | Single |

---

## 12. References

- `VISION-ROADMAP.md` — Strategic vision, model tiering, workstream history
- `HANDOFF.md` — Operational runbook (deploy, test, known issues)
- `AGENTS.md` — AI agent repo rules
- `CLAUDE.md` — Claude Code project rules
- `COMPETITOR-PATTERNS.md` — CodeRabbit/Greptile research
- `skills/github-pr-lifecycle/references/` — PR lifecycle patterns (merge inflation, inline comments, scope reduction, etc.)
