---
name: riptide
description: |
  Self-hosted GitHub App with two bots: (1) Companion — posts TL;DR comments on PRs
  with graphify-informed blast radius, triggered by webhook on PR open/sync.
  (2) Riptide Review — autonomous deep-think code review for large settled PRs
  (>100 LOC, unchanged 30+ min), triggered by cron polling.
triggers:
  - pull_request (opened, reopened, synchronize) → Companion TLDR
  - issue_comment (@riptide-bot companion skip/resume) → Companion control
  - installation / installation_repositories → sync repo list
  - cron (every 15 min) → Riptide Review deep-think sessions
entrypoint: server.py
---
# Riptide — Two-Bot GitHub App

## Bot 1: Companion (Webhook-Triggered)

Posts a TL;DR comment on every PR with graphify-informed blast radius analysis.

**Trigger:** `pull_request` opened/reopened/synchronize (via GitHub webhook)
**Model:** `qwen2.5-coder:7b` (via local Ollama)
**Output:** TL;DR comment with ELI5 + Blast Radius + ProofShot (if UI)

**Skip/Resume Per PR:**
```
@riptide-bot companion skip     # Stop commenting on this PR
@riptide-bot companion resume   # Re-enable
```

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

## Architecture

```
GitHub Webhook
  → FastAPI /webhook/github (verify signature, route event)
  → Companion thread (TL;DR + graphify blast radius)
  → GitHub API (post comment)

Cron (every 15 min)
  → riptide/deepthink.py (poll open PRs, filter, spawn)
  → Hermes cron session (deep-think + graphify analysis)
  → GitHub API (post review comment)
```

## Files

```
riptide/
├── github_app.py      # JWT auth, GitHub API client
├── companion.py       # Bot 1: TL;DR + ELI5 + ProofShot comment generator
├── deepthink.py       # Bot 2: Cron polling + Hermes session spawner
├── webhook.py         # FastAPI server (companion trigger, installation sync)
├── __init__.py
server.py              # Uvicorn entry point
requirements.txt       # fastapi, uvicorn, pydantic, cryptography, requests, graphifyy
Dockerfile
docker-compose.yml
SKILL.md
```

## Setup

### 1. Configure environment

```bash
# /home/sc/workspace/riptide/.env
GITHUB_APP_ID=4262983
GITHUB_PRIVATE_KEY_PATH=/app/github-private-key.pem
GITHUB_WEBHOOK_SECRET=<your-webhook-secret>
GITHUB_APP_SLUG=octopus-selfhost
RIPTIDE_DATA_DIR=/data

# Companion (Bot 1)
RIPTIDE_COMPANION_REPOS=ChonSong/hermes-webui,ChonSong/riptide
RIPTIDE_COMPANION_MODEL=qwen2.5-coder:7b
COMPANION_ENABLE_GRAPHIFY=1
OLLAMA_BASE_URL=http://host.docker.internal:43311

# Riptide Review (Bot 2)
RIPTIDE_WATCHED_REPOS=ChonSong/riptide,ChonSong/hermes-webui,ChonSong/hermes-webui-extensions,ChonSong/seans-reporepo,ChonSong/pr-review,ChonSong/everything-claude-code,codeovertcp/gto-wizard-clone-v2
RIPTIDE_STALENESS_MINUTES=30
RIPTIDE_MIN_LOC_CHANGED=100
```

### 2. Start the server

```bash
cd /home/sc/workspace/riptide
docker compose up -d --build
```

### 3. Set up the cron for Bot 2

```bash
# Add to Hermes cron (every 15 minutes)
hermes cron create "*/15 * * * *" \
  --name "riptide-review-poll" \
  --script /home/sc/workspace/riptide/riptide/deepthink.py
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
| Self-Hosted | ✅ | ✅ |
