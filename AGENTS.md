# AGENTS.md

Repo-scoped rules for AI agents reviewing or editing this codebase.

## Before submitting a change

```bash
python -m py_compile riptide/companion.py riptide/deepthink.py riptide/proofshotter.py riptide/webhook.py riptide/github_app.py riptide/fixer.py
```

## Repository layout

```
riptide/
├── riptide/
│   ├── github_app.py      # JWT auth, GitHub API client
│   ├── companion.py       # Bot 1: TL;DR + ELI5 + ProofShot flagger
│   ├── deepthink.py       # Bot 2: Cron polling + Hermes deep-think spawner
│   ├── fixer.py           # Bot 2: Autonomous fix (edit/commit/push)
│   ├── proofshotter.py    # Bot 3: Cron-polled proofshot visual verification
│   ├── webhook.py         # FastAPI server (companion trigger, installation sync)
│   └── __init__.py
├── server.py              # Uvicorn entry point
├── requirements.txt       # fastapi, uvicorn, pydantic, cryptography, requests, graphifyy
├── Dockerfile
├── docker-compose.yml
├── proofshot.config.json  # Example proofshot config schema for PR authors
├── SKILL.md
└── start.sh
```

## Three-Bot Architecture

### Bot 1: Companion (Webhook-Triggered)
- Triggered by `pull_request` opened/reopened/synchronize
- Posts TL;DR comment with graphify-informed blast radius
- Flags "📸 ProofShot Required" when UI files change
- Uses local Ollama (`qwen2.5-coder:7b`)
- Skip/resume per PR via `@riptide-bot companion skip/resume`
- On-demand deep-think review via `@riptide-bot review` (alias: `deepthink`, `full review`)

### Bot 2: Riptide Review (Cron-Triggered)
- Polls open PRs every 15 min via `riptide/deepthink.py`
- Spawns Hermes deep-think sessions for PRs with >100 LOC + unchanged 30+ min
- Uses graphify + deep-think skill for analysis
- Retries spawn up to 3 times with exponential backoff (5s/10s/20s)
- Does NOT record dedup state on failed spawn (allows retry on next poll)
- Uses `riptide` Hermes profile (LongCat-2.0 model) for spawned sessions
- Posts review comment with findings
- Notes missing proofshot evidence in review comment
- Dedup: SHA-based + 24h cooldown (prevents re-review of same revision)

### Bot 2b: Autonomous Fix (On-Demand)
- Triggered by `@riptide-bot fix` or `@riptide-bot fix <description>` on a PR
- Handled by `riptide/fixer.py`, routed in `webhook.py` Route 2b
- Parses findings from the latest `@riptide-bot review` comment (or triggers one first)
- **Authorization gate:** only the PR author, repo owner, or ChonSong can trigger
- Verifies each finding against current code (valid/skip-already-addressed/skip-stale)
- Pushes directly to the PR branch when same-repo and author-eligible (via `gh` CLI as ChonSong)
- Fork/foreign PRs get a comment-only patch with a "cannot push" note
- Safety: no force-push, no secret edits, no push on red tests, Conventional Commits
- Instant ack comment ("🛠 Riptide Fix triggered"), then summary with verdicts

### Bot 1: Companion State Reporting
- Companion TL;DR footer includes Bot 2 status when state file is present:
  - "🤖 Bot 2: reviewed Xh ago · `@riptide-bot review` for re-review" (<24h)
  - "🤖 Bot 2: last reviewed Xh+ ago · will auto-review after 30min staleness" (>24h)
- Sign-off includes `@riptide-bot review` command hint for on-demand deepthink

### Bot 3: Proofshotter (Cron-Triggered)
- Polls open PRs every 10 min via `riptide/proofshotter.py`
- Checks for UI file changes; runs proofshot Playwright captures on the dev instance (localhost:8788)
- `proofshot.config.json` is optional — defaults to `localhost:8788` if absent; include for custom captures/seed
- Posts visual evidence (GIF/screenshots) as PR comment
- Dedup: SHA-based only — new commits with UI changes automatically retrigger (no 24h cooldown)

## Conventions

### GitHub App Auth
- All GitHub API calls go through `github_app.py`'s `GitHubAppClient`
- JWT auth via App private key (RS256)
- Installation tokens cached with 55-min refresh

### No Template Fallbacks
- If the LLM model is down, Companion stays silent (no comment)
- Riptide Review only spawns when all filter conditions are met

### Dependencies
- Adding a new pip dependency needs clear justification
- Prefer transitive deps that already exist

## Commits and PRs

- Conventional Commits: `feat(scope): …`, `fix(scope): …`, `chore(deps): …`
- One change per PR

## What not to do

- Do not commit secrets
- Do not bypass Git hooks without authorisation
- Do not force-push to shared branches
- Do not add template fallbacks to Companion (by design)
- Do not add vector store / numpy / scipy (removed — graphify handles blast radius)
- Do not remove or bypass proofshot staleness check (5 min minimum before capture)

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
