# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Fixed
- Simplified `riptide-review-required` CI gate to single rule: if review has findings (🔴/🟡 in table row), require at least one commit after review timestamp.

## [0.14.0] - 2026-08-13

### Added
- Deterministic timing metrics for all 3 bots (⏱️ Review posted in Xm Ys)
  - Bot 1 (Companion): webhook received → comment posted
  - Bot 2 (Riptide Review): PR opened → review posted
  - Bot 3 (ProofShot): PR opened → proofshot posted

### Fixed
- CI gate: require full deep-think review (## 🎯 Summary / ## 🔍 Findings) — TL;DR no longer satisfies
- CI gate: require follow-up commit if review has findings (🔴/🟡)
- CI gate: robust date handling with `.commit.committer.date // .commit.author.date`
- CI gate: shell injection prevention via env block for user-controlled values
- Poller: restrict fix search to comments + raise limit + trim fields
- State DB stale pending jobs blocking review spawns (manual cleanup documented)

### Removed
- Redundant `riptide/grafiphy/` directory (functionality merged into `riptide/orchestrator.py`)
- Duplicate `grafiphy/labeler.py` (142-line duplicate of `riptide/labeler.py`)

## [0.13.0] - 2026-08-12

### Added
- AgentLint AGENTS.md compliance checks on PRs.
- Health endpoint, file logging, gunicorn production mode.
- Graphify data freshness refresh before companion analysis.
- Proofshot poll cron job for Bot 3.
- `@riptide-bot fix` autonomous fix command (edit/commit/push).

### Fixed
- Thread crash safety in companion PR handler.
- Docker compose standalone network creation.
- Personal path leak in graphify-out cache artifacts.
