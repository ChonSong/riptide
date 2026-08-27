# Riptide Review Pipeline

Automated code review for GitHub PRs using stratified Hermes sessions.

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

### Stratified Pipeline

Riptide uses a **stratified session architecture** where each worker gets its own focused Hermes session with role-specific context, skills, and memory.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         @riptide-bot review                             │
└─────────────────────────────────────┬───────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  AsyncConductor (state machine)                                        │
│  - Creates track with stratified workstreams                           │
│  - Dispatches ONE worker at a time                                     │
│  - Resumes on completion callback                                      │
└─────────────────────────────────────┬───────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
│  PROBE               │ │  JUDGE               │ │  ARTISAN             │
│  - terminal, file    │ │  - file only         │ │  - file, terminal    │
│  - Fetches diff      │ │  - deep-think skill  │ │  - excalidraw skill  │
│  - Runs graphify     │ │  - Evaluates code    │ │  - Creates diagram   │
│  → context.json      │ │  → findings.json     │ │  → diagram.json      │
└──────────┬───────────┘ └──────────┬───────────┘ └──────────┬───────────┘
           │                        │                        │
           └────────────────────────┼────────────────────────┘
                                    ▼
┌──────────────────────┐ ┌──────────────────────┐
│  WARDEN              │ │  SCRIBE              │
│  - file, terminal    │ │  - file, terminal    │
│  - code-review skill │ │  - github-pr skill   │
│  - Verifies outputs  │ │  - Posts review      │
│  → verification.json │ │  → posted comment    │
└──────────────────────┘ └──────────────────────┘
```

### Worker Roles

| Worker | Tools | Skills | Output |
|--------|-------|--------|--------|
| **Probe** | terminal, read_file | terminal, file | context.json |
| **Judge** | read_file, write_file | deep-think, code-review, coding-standards | findings.json |
| **Artisan** | read_file, write_file, patch, terminal | excalidraw, diagram-generation | diagram.json |
| **Engine** | terminal | terminal | result.json |
| **Warden** | read_file, terminal | code-review | verification.json |
| **Scribe** | read_file, write_file, terminal | github-pr-lifecycle | posted comment |
| **CI Verifier** | terminal | github-pr-lifecycle | ci_result.json |

### Data Flow

```text
GitHub Webhook → FastAPI /webhook/github → verify_signature()
                                          │
                                          ▼
                              StateStore.reserve_delivery() (dedup)
                                          │
                          ┌───────────────┼───────────────┐
                          ▼               ▼               ▼
                   handle_pull_request()  handle_issue_comment()  cron poll
                          │               │               │
                          ▼               ▼               ▼
                   Companion          AsyncConductor    AsyncConductor
                   posts TL;DR        creates track     creates track
                                       dispatches probe  dispatches probe
                                              │
                                              ▼
                                       Hermes cron jobs
                                       (one per worker)
                                              │
                                              ▼
                                       /conductor/resume
                                       (callback chain)
```

### Review Command

Comment `@riptide-bot review` on any PR to trigger an on-demand stratified review pipeline.

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
| `RIPTIDE_FIX_MODEL` | `LongCat-2.0` | Model for fix sessions |
| `RIPTIDE_FIX_PROVIDER` | `longcat` | Provider for fix |
| `RIPTIDE_WORKSPACE_ROOT` | `/home/sc/workspace` | Root path inserted into spawned session PYTHONPATH |
| `RIPTIDE_OUR_USERNAME` | `ChonSong` | GitHub username for push eligibility / auth gate |
| `RIPTIDE_OUR_ORG` | `ChonSong` | GitHub org for ownership checks |
| `RIPTIDE_WORK_STATE` | `~/.hermes/state/riptide-work-state.json` | Path to work-state JSON file |
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

Work-state JSON at `~/.hermes/state/riptide-work-state.json`:
- Tracks pipeline progress per PR
- Stores key facts between workers
- Enables resume on completion callback

## File Layout

```text
riptide/
├── riptide/
│   ├── github_app.py      # JWT auth, GitHub API client
│   ├── companion.py       # Bot 1: TL;DR + ELI5 + ProofShot flagger
│   ├── deepthink.py       # Bot 2: Cron polling + stratified pipeline spawner
│   ├── fixer.py           # Bot 2b: Autonomous fix (edit/commit/push)
│   ├── proofshotter.py    # Bot 3: Cron-polled proofshot visual verification
│   ├── webhook.py         # FastAPI server (companion trigger, installation sync)
│   ├── poller.py          # Cron entry point for Bot 2/3 discovery
│   ├── state.py           # SQLite-backed state (dedup, jobs, heuristics)
│   ├── labeler.py         # GitHub label engine
│   ├── assemble_review.py # Structured findings assembly
│   ├── depth.py           # ReviewDepth enum + classifier
│   ├── pipeline/
│   │   ├── async_conductor.py  # State-machine conductor (chains sessions)
│   │   ├── session_spawner.py  # Spawns stratified Hermes sessions
│   │   ├── conductor.py        # Synchronous conductor (legacy)
│   │   ├── roles.py            # Worker role definitions
│   │   ├── work_state.py       # Work-state JSON management
│   │   └── recovery.py         # Stall detection + recovery
│   └── grafiphy/          # Excalidraw diagram pre-generation
├── server.py              # Uvicorn entry point
├── requirements.txt       # fastapi, uvicorn, pydantic, cryptography, requests, graphifyy
├── Dockerfile
├── docker-compose.yml
├── proofshot.config.json  # Example proofshot config schema for PR authors
├── SKILL.md
└── start.sh
```

## Docs

- [HANDOFF.md](HANDOFF.md) — Session continuity, current state, next steps
- [AGENTS.md](AGENTS.md) — Rules for AI agents editing this codebase
- [CHANGELOG.md](CHANGELOG.md) — Recent changes
- [SECURITY.md](SECURITY.md) — Security policy and vulnerability reporting
