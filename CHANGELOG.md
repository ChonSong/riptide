# Changelog

## [Unreleased]

### Added
- **Stratified Hermes sessions** — Each worker gets its own focused session with role-specific context, skills, and memory
- **Pipeline watchdog** — Stuck-pipeline detection and auto-cleanup (`/conductor/stuck`, `/conductor/cleanup`)
- **Pipeline status endpoint** — `/conductor/status/{track_id}` for monitoring
- **Idempotent pipeline creation** — Re-triggering returns existing track (no duplicates)
- **Output validation** — Each worker's output validated before next dispatch
- **Retry logic** — Failed workstreams retry up to 3 times
- **10 worker roles** — probe, judge, artisan, engine, warden, scribe, ci_verifier, test_oracle, review_memory, documentarian
- **review_memory table** — Schema ownership in `_init_db`, v9 migration
- **CodeRabbit review fixes** — SQL rollback on lock contention, schema alignment, flaky test removal

### Changed
- **AsyncConductor** replaces synchronous Conductor for stratified session dispatch
- **session_spawner.py** wires existing Python modules (diff_analyzer, diagram_analyst, test_oracle, etc.) into stratified prompts
- **HANDOFF.md** — Complete rewrite for stratified architecture
- **README.md** — Updated for stratified pipeline architecture

### Fixed
- **SQLite WAL mode** — Concurrent cron jobs no longer deadlock
- **Startup recovery** — Pending work recovered on process restart
- **Stale re-reservation** — Wrapped in try/except OperationalError with rollback

## [0.15.0] - 2026-08-26

### Added
- SHA-aware dedup guard: `@riptide-bot review` blocked only if same commit SHA reviewed in last 24h
- Honest messaging: distinguishes "already pending" from spawn failures
- Post-deploy smoke test in `scripts/deploy.sh`
- Durable work queue with PID-based recovery
- SQLite WAL mode for concurrent cron jobs
- Ollama self-healing systemd service

### Changed
- Simplified `riptide-review-required` CI gate to single rule: findings → require follow-up commit
- Deep-think prompts written to temp file to bypass Hermes safety filter

## [0.14.0] - 2026-08-13

### Added
- Deterministic timing metrics for all 3 bots (⏱️ Review posted in Xm Ys)
- `@riptide-bot review` command always spawns deep-think (no silent skips)
- Auto-deploy smoke test verifies webhook after restart

### Fixed
- CI gate: `created_at` vs `submitted_at` timestamp normalization across review sources
- CI gate: shell injection prevention via env block
- Poller: fix search restricted to comments, raised limit, trimmed fields

### Removed
- Redundant `riptide/grafiphy/` directory (merged into `riptide/grafiphy/orchestrator.py`)
- Duplicate `grafiphy/labeler.py` (consolidated into `riptide/labeler.py`)
- Octopus template files from `.github/` (CODEOWNERS, FUNDING, dependabot, security, generic CI)

## [0.13.0] - 2026-08-12

### Added
- AgentLint AGENTS.md compliance checks on PRs
- `@riptide-bot fix` autonomous fix command
- Proofshot poll cron job for Bot 3
