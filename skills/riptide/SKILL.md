---
name: riptide
description: "Riptide: self-hosted GitHub App system — companion TL;DR, deepthink review, labeling strategy, and Cloudflare Tunnel infrastructure."
tags:
  - github-app
  - code-review
  - labeling
  - webhook
  - cloudflare-tunnel
  - ollama
  - fastapi
---

# Riptide — Self-Hosted GitHub App

Riptide is a self-hosted GitHub App with four autonomous capabilities across two trigger mechanisms:

| Bot | Trigger | What it does |
|-----|---------|-------------|
| **Companion** (Bot 1) | Webhook: `pull_request` opened/reopened/sync | Posts TL;DR + ELI5 comment with graphify-informed blast radius |
| **Riptide Review** (Bot 2) | Cron: every 15 min | Spawns Hermes deep-think sessions for large, settled PRs. See `riptide-review` skill. |
| **Riptide Fix** (Bot 2b) | Webhook Route 2b OR poller (external repos) | Autonomous edit/commit/push fix (auth-gated, push-eligible only) |
| **Proofshotter** (Bot 3) | Cron: every 10 min | Visual verification via Playwright captures |

## Three-Bullet Rule (CRITICAL — All Subagent Dispatches)

Every subagent brief MUST be exactly 3 bullets:

1. **Input** — what the subagent receives (file path, diff, JSON)
2. **Task** — exactly one action (create file, run command, review diff)
3. **Output** — exactly what to produce (path, format, pass/fail criteria)

**Never include:** background context, "read these N files to understand patterns," "explore the architecture," or explanations of how tools work. Long briefs cause context explosions, exploration drift, and timeouts. If a subagent needs to discover something, the brief is too big — tell it exactly where to look.

## Architecture

```
github-webhook → cloudflare-tunnel → localhost:8477 → FastAPI webhook.py
                                                                    │
                                                                    ├── handle_pull_request()
                                                                    │     └── TaskClassifier().classify() → TaskProfile
                                                                    │     └── T0Orchestrator(companion, github).review_pr(profile)
                                                                    │           ├── _dispatch_t2() → classify_pr_mood + select_gif + TL;DR
                                                                    │           ├── _dispatch_t1() → _spawn_deepthink (async, non-blocking)
                                                                    │           ├── _dispatch_t3_visual() → proofshot (thread with timeout)
                                                                    │           └── _post_comment() → unified TL;DR with retry
                                                                    │
                                                                    └── _T0_SEMAPHORE (3 concurrent max)
```

For full architecture details, see `references/webhook-vs-poller-architecture-2026-08.md`.

## Configuration

```env
# GitHub App
GITHUB_APP_ID=4262983
GITHUB_PRIVATE_KEY_PATH=/home/sc/workspace/riptide/github-private-key.pem
GITHUB_WEBHOOK_SECRET=<from .env>
GITHUB_APP_SLUG=riptide-review

# Paths & Services
RIPTIDE_DATA_DIR=/home/sc/.local/share/riptide
RIPTIDE_WATCHED_REPOS=ChonSong/riptide,ChonSong/hermes-webui
RIPTIDE_OUR_USERNAME=ChonSong
RIPTIDE_OUR_ORG=ChonSong
OLLAMA_BASE_URL=http://localhost:43311

# Companion
RIPTIDE_COMPANION_MODEL=qwen2.5-coder:7b
COMPANION_ENABLE_GRAPHIFY=1
COMPANION_ENABLE_DETERMINISTIC=1

# Review (Bot 2)
RIPTIDE_DEEPTHINK_MODEL=LongCat-2.0
RIPTIDE_STALENESS_MINUTES=30
RIPTIDE_MIN_LOC_CHANGED=100

# Poller (external repos)
RIPTIDE_POLLER_LOOKBACK=3
RIPTIDE_POLLER_MAX=20
```

## Workflow Preferences (User Mandate)

- **Proceed autonomously** — fix things and report, don't ask for permission at every step
- **Incremental, sequential work** — one code-change PR at a time; no parallel feature work
- **Draft for unrelated changes** — new unrelated ideas go to a draft PR with a plan only (no code)
- **Keep PRs clean** — rebase onto master after any merge, verify CI stays green
- **One decision point at a time** — proceed until there is a decision to make, then stop

## Review Workflow (Lessons Learned)

- **Investigate before replacing** — trace WHY something is broken before building new infrastructure. Empty diagram? Trace _gather_review_data() → find root cause → fix → verify. Don't build parallel infrastructure.
- **Riptide reviews post as ChonSong, not riptide-review[bot]** — deep-think sessions post findings via `gh pr comment` as the user. The workflow must detect Riptide markers in ANY user's comments, not just the bot.
- **Subagent briefs ≤3 bullets** — exact file paths, exact commands, exact output. No background context, no "explore the architecture".
- **All checks must pass before merge** — `check`, `agentlint`, `Riptide Review Required`, `GitGuardian`. Each must be green individually.
- **CI stale cache** — `gh pr close <n> && gh pr reopen <n>` forces fresh CI run after amended commits. Force push alone doesn't retrigger.

## Key Rules

- **NO template fallbacks**: if the model is down, no comment is posted (by design)
- **Dedup via StateStore** `pr_heuristics` table — legacy JSON files migrated once, not dual-written
- **One code-change PR at a time** — new unrelated changes go to a draft PR with a plan only
- **Production discipline**: NEVER edit workspace files without full PR process first

## References

- `references/pitfalls.md` — Common pitfalls (Ollama down, graphify PATH, ModuleNotFoundError, webhook gotchas, etc.)
- `riptide-development/references/unified-pipeline-design.md` — Two-path architecture
- `riptide-development/references/state-heuristics-centralization.md` — StateStore and dedup
- `riptide-poller/references/session-2026-08-07.md` — Poller implementation
- `riptide-maintenance/references/2026-08-05-consolidation.md` — Bot 2 job stall recovery
