# Companion Model Reporting — Work Queue

Draft PR to queue and document the companion model reporting fix and broader model detection audit identified in PR #100 review.

## Issues

### 1. Companion not reporting model (should be qwen2.5-coder:7b)
- Companion's TL;DR footer doesn't include model/provider info
- Affects: `riptide/companion.py` Tier-1 and Tier-2 comments
- Expected: Footer should report actual runtime model (e.g., `qwen2.5-coder:7b via Ollama`)

### 2. Spawned sessions can't report runtime model
- Documented in `ISSUE-model-detection.md`
- Deepthink's `_spawn_deepthink()` uses `--deliver discord` (fixed) but prompt doesn't instruct the agent to report its runtime model
- Affects: All `riptide-review-*` cron sessions
- Root cause: Hermes Agent doesn't export `HERMES_ACTIVE_MODEL`/`HERMES_ACTIVE_PROVIDER` to spawned sessions

### 3. Review footer inconsistencies
- `@riptide-bot review` confirmation doesn't include model info
- Fix session errors use hardcoded strings instead of runtime values

## Scope

- [ ] Audit all bot comment templates for model/provider reporting
- [ ] Add model reporting to companion footer
- [ ] Update `_build_orchestrator_prompt()` to instruct model disclosure
- [ ] Update fixer.py to include runtime model in error messages
- [ ] Test that reviews truthfully report LongCat-2.0, companion reports Ollama qwen2.5-coder:7b

## Related

- PR #100 review findings
- ISSUE-model-detection.md
- PR #96 (Bot 2 state reporting in footer — partial overlap)

Draft — no code yet. Documenting to queue the work.
