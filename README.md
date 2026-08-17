<img width="600" height="300" alt="Riptide Banner" src="https://github.com/user-attachments/assets/a57103c0-a98a-41f1-b7b1-fc9b4a23e27e" />

# Riptide Review Pipeline

Automated code review for GitHub PRs using a three-bot system.

## Quick Start

```bash
# Run locally
python3 server.py

# Run with production config
python3 server.py --prod

# Run tests
python3 -m pytest riptide/tests/ -q
```

## Architecture

### Three-Bot System

| Bot | Trigger | What it does |
|-----|---------|--------------|
| **Companion** | PR opened/updated | Posts instant TL;DR + ELI5 with blast-radius analysis |
| **Deepthink** | Cron (15 min) or `@riptide-bot review` | Full deep-think review with findings |
| **Proofshotter** | Cron (10 min) | Posts visual evidence (GIF/screenshots) for UI changes |

### Data Flow

```text
GitHub Webhook → FastAPI /webhook → verify_signature()
                                          │
                                          ▼
                              StateStore.reserve_delivery() (dedup)
                                          │
                          ┌───────────────┼───────────────┐
                          ▼               ▼               ▼
                   handle_pull_request()  handle_issue_comment()  cron poll
                          │               │               │
                          ▼               ▼               ▼
                   Companion          Hermes cron      Hermes cron
                   posts TL;DR        deep-think       deep-think
```

### Review Command

Comment `@riptide-bot review` on any PR to trigger an on-demand deep-think session.

**Dedup logic:** Same commit SHA within 24h → blocked. New commit → always allowed.

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GITHUB_APP_ID` | — | GitHub App ID |
| `GITHUB_PRIVATE_KEY_PATH` | — | App private key |
| `RIPTIDE_POLLER_REPOS` | — | Comma-separated repos to poll |
| `RIPTIDE_DEPLOY_BRANCH` | `main` | Branch that triggers auto-deploy |
| `HOST` | `0.0.0.0` | Webhook server host |
| `PORT` | `8477` | Webhook server port |

## Auto-Deploy

When a PR merges into the configured deployment branch (`main` by default):
1. Webhook triggers `scripts/deploy.sh`
2. Script: `git pull` → clean `__pycache__` → `systemctl restart riptide.service` → smoke test
3. Service runs the new code automatically

## State

SQLite at `~/.local/share/riptide/state.db`:
- `deliveries` — webhook dedup
- `pr_heuristics` — SHA + timestamp for review cooldown
- `jobs` — spawn queue for deep-think sessions

## File Layout

```text
riptide/
├── webhook.py         # FastAPI server, GitHub webhook handler
├── companion.py       # Bot 1: TL;DR + ELI5 + timing footer
├── deepthink.py       # Bot 2: Cron + @riptide-bot review spawner
├── proofshotter.py    # Bot 3: Visual verification (GIF/screenshots)
├── fixer.py           # Bot 2b: Autonomous fix via @riptide-bot fix
├── poller.py          # Cron entry point for Bot 2/3 discovery
├── state.py           # SQLite-backed state (dedup, jobs, heuristics)
├── labeler.py         # GitHub label engine
├── assemble_review.py # Structured findings assembly
├── depth.py           # ReviewDepth enum + classifier
└── grafiphy/          # Excalidraw diagram pre-generation
```

## Docs

- [HANDOFF.md](HANDOFF.md) — Session continuity, current state, next steps
- [VISION-ROADMAP.md](VISION-ROADMAP.md) — Long-term roadmap and pillars
- [COMPETITOR-PATTERNS.md](COMPETITOR-PATTERNS.md) — Analysis of CodeRabbit/Greptile patterns
- [AGENTS.md](AGENTS.md) — Rules for AI agents editing this codebase
- [CHANGELOG.md](CHANGELOG.md) — Recent changes

## Security

- Deep-think prompts are written to temp files with `0o600` permissions (owner-only) and cleaned up after Hermes reads them
- Prompt contents are sanitized to redact secrets (GitHub tokens, private keys) before writing to disk
- The `@riptide-bot review` command always spawns when the 24h same-commit cooldown allows — review data (findings, file paths, code) is treated as untrusted

