---
name: riptide
description: |
  Self-hosted GitHub App PR reviewer — local Ollama (qwen2.5-coder:7b), numpy vector store,
  webhook-driven (no polling). Forked from Octopus (2026-07-12) and stripped of Next.js/Docker/Qdrant
  overhead. Handles: PR open/reopen/sync → full review + inline comments + check run;
  @mention → review with reference to triggering comment; PR merge → incremental index update.
  All credentials use GitHub App JWT (octopus-selfhost ID 4262983).
triggers:
  - pull_request (opened, reopened, synchronize)
  - issue_comment (@mention: @riptide or @octopus)
  - installation / installation_repositories
entrypoint: server.py
---
# Riptide — Self-Hosted GitHub App PR Reviewer

## What it is

A lightweight Python/FastAPI fork of Octopus that provides AI-powered code review
via a GitHub App webhook, without heavy infrastructure (no Next.js, no Qdrant, no Docker
sidecar for every review). Runs as a single container or bare Python process.

- **GitHub App**: `octopus-selfhost` (ID 4262983)
- **Auth**: JWT via App private key (no user `gh` token)
- **LLM**: Local Ollama — `qwen2.5-coder:7b` (review) + `nomic-embed-text` (embeddings)
- **Vector store**: NumPy float32 blobs + SQLite metadata (no sqlite-vec extension needed)
- **Output**: PR summary comment + inline diff comments + GitHub Check Run annotation

## Architecture

```
GitHub webhook (pull_request / issue_comment / installation)
  → FastAPI /webhook/github  (verify sig, enqueue job)
  → review_worker.py         (background daemon thread, serialised queue)
      → github_app.py        (JWT → installation token → GitHub API)
      → github_app.py        (get_pr_diff, get_pr_files, post_*_comment, check_run CRUD)
      → embed.py             (Ollama /api/embed, chunking with 1500-char cap)
      → store.py             (NumPy/SQLite vector store, cosine similarity search)
      → review.py            (prompt builder, LLM call, finding parser)
  → GitHub API               (inline comments, PR comment, check run)
```

## Setup

### 1. Configure environment

```bash
# /home/sc/workspace/riptide/.env
GITHUB_APP_ID=4262983
GITHUB_PRIVATE_KEY_PATH=/app/github-private-key.pem
GITHUB_WEBHOOK_SECRET=<your-webhook-secret>   # generate: python -c "import secrets; print(secrets.token_hex(32))"
GITHUB_APP_SLUG=octopus-selfhost
OLLAMA_BASE_URL=http://host.docker.internal:43311
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_REVIEW_MODEL=qwen2.5-coder:7b
RIPTIDE_DATA_DIR=/data
RETRIEVE_TOP_K=8
PORT=8477
```

### 2. Point webhook at this server

GitHub App webhook URL must be publicly reachable. Options:

**Option A — Cloudflare Tunnel** (preferred, already configured):
```
# Add to ~/cloudflared/config.yml (or via cfut):
- service: https://localhost:8477
  hostname: riptide.codeovertcp.com
```

**Option B — Ngrok**:
```bash
ngrok http 8477 --domain=your-domain.ngrok-free.app
```

### 3. Install the GitHub App webhook

The `octopus-selfhost` GitHub App (ID 4262983) is already installed on ChonSong repos.
Update its webhook URL in GitHub Settings → GitHub Apps → octopus-selfhost → Webhook.
Set the webhook secret to match `GITHUB_WEBHOOK_SECRET`.

Permissions required:
- Repository: Read & write for pull request comments, check runs, issue comments
- Repository: Read for contents, pull request metadata

### 4. Start the server

**Bare Python** (development):
```bash
cd /home/sc/workspace/riptide
pip install -r requirements.txt
python server.py
```

**Docker** (production):
```bash
cd /home/sc/workspace/riptide
docker compose up -d --build
```

**Via Hermes cron** (self-healing):
```yaml
# ~/.hermes/profiles/default/cron/riptide.yml
command: cd /home/sc/workspace/riptide && docker compose up -d --build
when: "0 3 * * *"   # daily at 03:00 UTC
watchdog: true
health_url: http://localhost:8477/health
```

## Usage

### Automatic review

GitHub App events trigger reviews automatically:

| Event | Action |
|-------|--------|
| `pull_request` opened/reopened/synchronize | Full diff review + inline comments + check run |
| `issue_comment` with `@riptide` or `@octopus` | Review referencing the triggering comment |
| `pull_request` closed + merged | Incremental vector store update |
| `installation` created/deleted | Sync repo list to local metadata DB |

### @mention

```markdown
<!-- On any PR, post this comment: -->
@riptide review this please
```

The bot adds 👀 reaction and starts the review pipeline.

### Manual trigger (via Hermes CLI)

```bash
# Enqueue a manual review (e.g., via cron or manual trigger)
python -c "
from riptide.review_worker import job_queue, enqueue_review
job = {
    'type': 'review',
    'installation_id': <id>,
    'owner': 'ChonSong',
    'repo': 'some-repo',
    'repo_full': 'ChonSong/some-repo',
    'pr_number': 42,
    'pr_title': 'Fix bug',
    'pr_author': 'someuser',
    'head_sha': '<sha>',
    'delivery_id': 'manual',
}
enqueue_review(job_queue, job)
"
```

## Files

```
riptide/
├── github_app.py      # JWT auth, GitHub API client (app-level, no user token)
├── embed.py           # Ollama /api/embed with 1500-char chunking cap
├── store.py           # NumPy/SQLite vector store
├── review.py          # Prompt builder, LLM call, finding parser
├── webhook.py         # FastAPI webhook server (signature verify, event routing)
├── review_worker.py   # Background queue worker (serialised, daemon thread)
server.py              # Uvicorn entry point
requirements.txt
Dockerfile
docker-compose.yml
SKILL.md
```

## Key differences from Octopus

| | Riptide | Octopus |
|--|---------|---------|
| Stack | Python/FastAPI | Next.js + tRPC |
| Vector store | NumPy + SQLite | Qdrant (Docker) |
| Deployment | Single container | Docker Compose + separate services |
| Auth | GitHub App JWT only | GitHub App JWT + Prisma (org keys) |
| Check runs | ✅ | ✅ |
| Inline comments | ✅ | ✅ |
| @mention | ✅ | ✅ |
| Review deduplication | ✅ (finding dedup) | ✅ |
| Incremental index | ✅ | ✅ |

## Key differences from pr-review (scripts-only)

| | Riptide | pr-review (scripts) |
|--|---------|---------------------|
| Trigger | Webhook (push) | Cron polling |
| @mention | ✅ | ❌ |
| Inline comments | ✅ | ❌ (PR-level only) |
| Check runs | ✅ | ❌ |
| GitHub App auth | ✅ JWT | ❌ (gh CLI token) |
| Hermes skill | ✅ | ❌ (not compliant) |
