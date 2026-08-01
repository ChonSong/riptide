---
name: riptide
description: |
  Self-hosted GitHub App with three bots: (1) Companion — posts TL;DR comments on PRs
  with graphify-informed blast radius and contextual GIF reactions, triggered by webhook.
  (2) Riptide Review — autonomous deep-think code review for large settled PRs
  (>100 LOC, unchanged 30+ min), triggered by cron polling.
  (3) Proofshotter — visual verification via Playwright captures for UI changes,
  triggered by cron polling.
triggers:
  - pull_request (opened, reopened, synchronize) → Companion TLDR
  - issue_comment (@riptide-bot companion skip/resume) → Companion control
  - issue_comment (@riptide-bot review / full review / deepthink) → Bot 2 deep-think
  - installation / installation_repositories → sync repo list
  - cron (every 15 min) → Riptide Review deep-think sessions
  - cron (every 10 min) → Proofshotter visual verification
entrypoint: server.py
---
# Riptide — Three-Bot GitHub App

## Bot 1: Companion (Webhook-Triggered)

Posts a TL;DR comment on every PR with graphify-informed blast radius analysis and contextual GIF reaction.

**Trigger:** `pull_request` opened/reopened/synchronize (via GitHub webhook)
**Model:** `qwen2.5-coder:7b` (via local Ollama)
**Output:** TL;DR comment with ELI5 + Blast Radius + GIF reaction

**Skip/Resume Per PR:**
```
@riptide-bot companion skip     # Stop commenting on this PR
@riptide-bot companion resume   # Re-enable
```

**On-Demand Deep-Think Review:**
```
@riptide-bot review             # Trigger Bot 2 deep-think review now
@riptide-bot full review        # Same (alias)
@riptide-bot deepthink          # Same (alias)
```

**GIF Selection:**
Companion auto-selects a GIF based on PR mood classification (✨ feature, 🐛 fix, ♻️ refactor, etc.) using keyword relevance scoring. Priority: Giphy API → Tenor API → static fallback.

## Bot 2: Riptide Review (Cron-Triggered)

Autonomous deep-think code review for large, settled PRs.

**Trigger:** Cron polling every 15 min. Spawns Hermes session when ALL of:
  - PR has >100 LOC changes (additions + deletions)
  - PR unchanged for ≥30 minutes
  - PR is in a watched repo AND (owned by ChonSong OR authored by ChonSong)

**Model:** Hermes agent with `deep-think` + `github-pr-lifecycle` skills
**Output:** PR review comment with graphify analysis + deep-think reasoning

**Config (env vars):**
| Variable | Default | Description |
|----------|---------|-------------|
| `RIPTIDE_WATCHED_REPOS` | `ChonSong/riptide,...` | Comma-separated repo list |
| `RIPTIDE_OUR_USERNAME` | `ChonSong` | GitHub username for authorship check |
| `RIPTIDE_OUR_ORG` | `ChonSong` | GitHub org for ownership check |
| `RIPTIDE_STALENESS_MINUTES` | `30` | Minutes since last update to qualify |
| `RIPTIDE_MIN_LOC_CHANGED` | `100` | Minimum LOC changes to trigger |

## Bot 3: Proofshotter (Cron-Triggered)

Visual verification for UI changes.

**Trigger:** Cron polling every 10 min. Runs when:
  - UI files changed in PR
  - PR not draft
  - PR stale for ≥5 minutes

**Output:** PR comment with GIF + screenshots of UI changes
**Config:** `proofshot.config.json` (optional — defaults to `localhost:8788`)

**Config (env vars):**
| Variable | Default | Description |
|----------|---------|-------------|
| `RIPTIDE_PROOFSHOT_STALENESS_MINUTES` | `10` | Minutes since last update to qualify |
| `RIPTIDE_PROOFSHOT_CLI` | `/home/sc/workspace/proofshot/cli.py` | Path to proofshot CLI |

## Architecture

```
GitHub Webhook
  → FastAPI /webhook/github (verify signature, route event)
  → T0 Orchestrator (classify, dispatch to tiers)
      → T1: Companion TL;DR thread
      → T2: Quick summary (small PRs)
      → T3: Proofshot visual capture (UI PRs)
  → GitHub API (post comment)

Cron (every 15 min)
  → riptide/deepthink.py (poll open PRs, filter, spawn)
  → Hermes cron session (deep-think + graphify analysis)
  → GitHub API (post review comment)

Cron (every 10 min)
  → riptide/proofshotter.py (poll open PRs for UI changes)
  → Playwright captures on dev instance
  → GitHub API (post visual evidence comment)
```

## Files

```
riptide/
├── github_app.py      # JWT auth, GitHub API client
├── companion.py       # Bot 1: TL;DR + ELI5 + GIF reaction + ProofShot comment
├── orchestrator.py    # T0: classify PR → dispatch to T1/T2/T3 tiers
├── deepthink.py       # Bot 2: Cron polling + Hermes session spawner
├── review_pipeline.py # Hybrid review: templates + deepthink + validation
├── proofshotter.py    # Bot 3: Cron-polled visual verification
├── webhook.py         # FastAPI server (companion trigger, installation sync)
├── __init__.py
server.py              # Uvicorn/gunicorn entry point
requirements.txt       # fastapi, uvicorn, pydantic, cryptography, requests, graphifyy
Dockerfile
docker-compose.yml
proofshot.config.example.json
SKILL.md
start.sh
```

## Setup

### 1. Configure environment

```bash
# /home/sc/workspace/riptide/.env
GITHUB_APP_ID=4262983
GITHUB_PRIVATE_KEY_PATH=/app/github-private-key.pem
GITHUB_WEBHOOK_SECRET=<your-webhook-secret>
GITHUB_APP_SLUG=riptide-review
RIPTIDE_DATA_DIR=/data

# Server
HOST=0.0.0.0
PORT=8477

# Companion (Bot 1)
RIPTIDE_COMPANION_REPOS=ChonSong/hermes-webui,ChonSong/riptide
RIPTIDE_COMPANION_MODEL=qwen2.5-coder:7b
COMPANION_ENABLE_GRAPHIFY=1
OLLAMA_BASE_URL=http://host.docker.internal:43311
GIPHY_API_KEY=          # optional — enables dynamic GIF lookup
TENOR_API_KEY=          # optional — enables dynamic GIF lookup

# Orchestrator
RIPTIDE_T0_MAX_CONCURRENT=3

# Riptide Review (Bot 2)
RIPTIDE_WATCHED_REPOS=ChonSong/riptide,ChonSong/hermes-webui,ChonSong/hermes-webui-extensions,ChonSong/seans-reporepo,ChonSong/pr-review,ChonSong/everything-claude-code,codeovertcp/gto-wizard-clone-v2
RIPTIDE_STALENESS_MINUTES=30
RIPTIDE_MIN_LOC_CHANGED=100

# Proofshotter (Bot 3)
RIPTIDE_PROOFSHOT_STALENESS_MINUTES=10
RIPTIDE_PROOFSHOT_CLI=/home/sc/workspace/proofshot/cli.py
```

### 2. Start the server

```bash
cd /home/sc/workspace/riptide
docker compose up -d --build
```

### 3. Set up the crons

```bash
# Bot 2 — Riptide Review (every 15 minutes)
hermes cron create "*/15 * * * *" \
  --name "riptide-review-poll" \
  --script /home/sc/workspace/riptide/riptide/deepthink.py

# Bot 3 — Proofshotter (every 10 minutes)
hermes cron create "*/10 * * * *" \
  --name "riptide-proofshot-poll" \
  --script /home/sc/workspace/riptide/riptide/proofshotter.py
```

## Key Differences from Octopus

| | Riptide | Octopus |
|--|---------|---------|
| Stack | Python / FastAPI | Next.js + tRPC |
| Vector store | None (graphify for blast radius) | Qdrant (Docker) |
| Deployment | Single container | Docker Compose + services |
| Auth | GitHub App JWT only | JWT + Prisma (org BYOK) |
| Review | Bot 2: Hermes deep-think | Built-in LLM review |
| TLDR Comments | Bot 1: Companion | ❌ |
| Graphify Blast Radius | ✅ | ❌ |
| Visual Verification | Bot 3: Proofshotter | ❌ |
| Self-Hosted | ✅ | ✅ |
