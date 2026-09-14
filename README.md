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
riptide/
├── riptide/                    # the application package
│   ├── webhook.py              # FastAPI server, GitHub webhook handler
│   ├── companion.py            # Bot 1: TL;DR + ELI5 + timing footer
│   ├── deepthink.py            # Bot 2: Cron + @riptide-bot review spawner
│   ├── proofshotter.py         # Bot 3: Visual verification (GIF/screenshots)
│   ├── fixer.py                # Bot 2b: Autonomous fix via @riptide-bot fix
│   ├── poller.py               # Cron entry point for Bot 2/3 discovery
│   ├── state.py                # SQLite-backed state (dedup, jobs, reservations)
│   ├── labeler.py              # GitHub label engine
│   ├── assemble_review.py      # Structured findings assembly + sign-off
│   ├── diff_analyzer.py        # Deterministic complexity/defect scan
│   ├── depth.py                # ReviewDepth enum + classifier
│   ├── pipeline/               # Conductor review pipeline, one module per role
│   │   ├── conductor.py        #   workstream orchestration, canonical output paths
│   │   ├── probe.py            #   ws-1 PR context
│   │   ├── judge.py            #   ws-2 findings (stamps `judged: true`)
│   │   ├── artisan.py          #   ws-3 diagram
│   │   ├── engine.py           #   ws-4 artifact upload
│   │   ├── scribe.py           #   ws-5 posts the review
│   │   └── warden.py           #   verification
│   ├── grafiphy/               # Excalidraw diagram rendering (imported by deepthink)
│   └── graphify_ingest/        # Graph ingestion (imported by pipeline/artisan)
├── docs/REVIEW-CONTRACT.md     # What a review is; gate/reservation/attribution rules
├── docs/archive/               # Superseded planning docs (historical)
├── scripts/deploy.sh           # Auto-deploy on merge to the default branch
├── watchdog.sh                 # Restart only when origin/main is genuinely ahead
└── start.sh                    # Entry point (git pull on main, then serve)
```

### Review Command

Comment `@riptide-bot review` on any PR to trigger an on-demand deep-think session.

**Dedup logic:** an on-demand `review` always spawns; the poller skips a PR only when the
same SHA was reviewed in the last 24h **and** a review comment was actually delivered.
A stale reservation is released automatically, so a failed spawn never blocks the next
trigger. See [docs/REVIEW-CONTRACT.md](docs/REVIEW-CONTRACT.md).

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

Authoritative source: [`.env.example`](.env.example) for the key set, and the deployed `.env`
for the live values — check it rather than assuming, since the reviewing model is printed in
each review's sign-off. Code defaults are fallbacks for a missing `.env` only.

| Variable | Default | Purpose |
|----------|---------|---------|
| `GITHUB_APP_ID` | — | GitHub App ID |
| `GITHUB_PRIVATE_KEY_PATH` | — | App private key |
| `RIPTIDE_POLLER_REPOS` | — | Comma-separated repos to poll |
| `RIPTIDE_DEPLOY_BRANCH` | `main` | Branch that triggers auto-deploy |
| `RIPTIDE_DEEPTHINK_MODEL` | deployed `.env` (`deepseek-v4-flash`) | Model for deep-think sessions |
| `RIPTIDE_DEEPTHINK_PROVIDER` | deployed `.env` (`deepseek`) | Provider for deep-think |
| `RIPTIDE_FIX_MODEL` | deployed `.env` (`deepseek-v4-flash`) | Model for fix sessions |
| `RIPTIDE_FIX_PROVIDER` | deployed `.env` (`deepseek`) | Provider for fix |
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

- [docs/REVIEW-CONTRACT.md](docs/REVIEW-CONTRACT.md) — Review markers, CI gate, reservations, model attribution
- [AGENTS.md](AGENTS.md) — Rules for AI agents editing this codebase
- [.env.example](.env.example) — Authoritative environment key set
- [skills/](skills/README.md) — Agent skills driving review/fix behaviour (symlinked into `~/.hermes/skills`)
- [COMPETITOR-PATTERNS.md](COMPETITOR-PATTERNS.md) — Analysis of CodeRabbit/Greptile patterns
- [CHANGELOG.md](CHANGELOG.md) — Recent changes
- [SECURITY.md](SECURITY.md) — Security policy and vulnerability reporting
- [docs/archive/](docs/archive/README.md) — Superseded planning docs (PLAN, HANDOFF, VISION-ROADMAP, …)



