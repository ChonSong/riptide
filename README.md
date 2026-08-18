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

## Fix Command

Comment `@riptide-bot fix [description]` on any PR to trigger an on-demand fix session that edits, commits, and pushes to the PR branch.

The optional `description` narrows scope — e.g. `@riptide-bot fix the auth race condition in session.py`. Without one, the session addresses all outstanding findings from the latest `@riptide-bot review`.

**Authorization gate:** Only the PR author, the repo owner, or `@ChonSong` can trigger fix. Others get a `🚫 Not authorized` reply.

**Push eligibility:**
- **Same-repo, author-eligible** → Hermes edits, commits, and pushes directly to the PR branch (Conventional Commits, `gh` CLI as ChonSong).
- **Fork / foreign repo** → Comment-only patch with a "cannot push" note. Never pushes to forks.

**Safety constraints (hard):**
- Only touches files in this PR's diff — scope isolation
- Verifies each finding against current HEAD before editing (skips already-addressed or stale findings)
- Runs repo tests before pushing — no push on red
- Never force-pushes, never rewrites pushed history
- Never edits credential/secret files

The session always posts a summary comment with per-finding verdicts, test results, and commit SHA (or patch).

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GITHUB_APP_ID` | — | GitHub App ID |
| `GITHUB_PRIVATE_KEY_PATH` | — | App private key |
| `RIPTIDE_POLLER_REPOS` | — | Comma-separated repos to poll |
| `RIPTIDE_DEPLOY_BRANCH` | `main` | Branch that triggers auto-deploy |
| `RIPTIDE_DEEPTHINK_MODEL` | `LongCat-2.0` | Model for deep-think sessions |
| `RIPTIDE_DEEPTHINK_PROVIDER` | `longcat` | Provider for deep-think |
| `RIPTIDE_FIX_MODEL` | `custom:LongCat-2.0` | Model for fix sessions |
| `RIPTIDE_FIX_PROVIDER` | `custom` | Provider for fix |
| `RIPTIDE_WORKSPACE_ROOT` | `/home/sc/workspace` | Root path inserted into spawned session PYTHONPATH |
| `RIPTIDE_OUR_USERNAME` | `ChonSong` | GitHub username for push eligibility / auth gate |
| `RIPTIDE_OUR_ORG` | `ChonSong` | GitHub org for ownership checks |
| `HOST` | `0.0.0.0` | Webhook server host |
| `PORT` | `8477` | Webhook server port |

## Auto-Deploy

When a PR merges into `main`:
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
- [SECURITY.md](SECURITY.md) — Security policy and vulnerability reporting


