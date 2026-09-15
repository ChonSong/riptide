# AGENTS.md

Repo-scoped rules for AI agents reviewing or editing this codebase.

## Before submitting a change

```bash
# Compile everything the service runs (cheap; catches the NameError class of bug
# that reached production through the spawn path)
python -m compileall -q riptide

# There is NO pytest workflow in CI — run the suite locally before pushing.
# Measured baseline for that exact command (28 failed / 1096 passed, ~7 min):
#   test_fixer.py 12, test_review_state_migration.py 4, test_webhook_endpoint.py 3,
#   test_fixer_ephemeral.py 3, then one each in test_trace_context, test_review_timing,
#   test_pipeline, test_entrypoints, test_companion, test_ci_verifier.
# All 28 are pre-existing. Compare against that list rather than a failure count,
# and re-measure before changing this comment: an inflated baseline hides
# regressions (a change adding 20 failures still sits under a loose "~50").
/home/sc/.hermes/hermes-agent/venv/bin/python3 -m pytest riptide/tests -q
```

Then confirm the service is healthy: `systemctl --user is-active riptide.service`
and `curl -s localhost:8477/health`.

## Repository layout

```
riptide/
├── riptide/
│   ├── github_app.py      # JWT auth, GitHub API client
│   ├── companion.py       # Bot 1: TL;DR + ELI5 + ProofShot flagger
│   ├── deepthink.py       # Bot 2: Cron polling + Hermes deep-think spawner
│   ├── fixer.py           # Bot 2b: Autonomous fix (edit/commit/push)
│   ├── proofshotter.py    # Bot 3: Cron-polled proofshot visual verification
│   ├── webhook.py         # FastAPI server (companion trigger, installation sync)
│   ├── assemble_review.py # Structures findings into the posted review body
│   ├── state.py           # SQLite state: dedup, jobs, reservations, heuristics
│   ├── diff_analyzer.py   # Deterministic complexity/defect scan
│   ├── pipeline/          # Conductor review pipeline (one module per role)
│   │   ├── conductor.py   #   orchestrates workstreams, canonical output paths
│   │   ├── probe.py       #   ws-1: builds PR context
│   │   ├── judge.py       #   ws-2: findings (must stamp `judged: true`)
│   │   ├── artisan.py     #   ws-3: diagram
│   │   ├── engine.py      #   ws-4: artifact upload
│   │   ├── scribe.py      #   ws-5: posts the review (--model/--provider)
│   │   └── warden.py      #   verification
│   ├── grafiphy/          # Excalidraw diagram rendering (imported by deepthink)
│   └── graphify_ingest/   # Graph ingestion (imported by pipeline/artisan)
├── server.py              # Uvicorn entry point
├── scripts/deploy.sh      # Auto-deploy (invoked by webhook on merge)
├── docs/REVIEW-CONTRACT.md # Review markers, gate, reservations, attribution
├── docs/archive/          # Superseded planning docs (historical, not current)
├── requirements.txt       # fastapi, uvicorn, pydantic, cryptography, requests, graphifyy
├── Dockerfile
├── docker-compose.yml
├── proofshot.config.json  # Example proofshot config schema for PR authors
└── start.sh
```

`grafiphy/` and `graphify_ingest/` are near-duplicates and both are live (different
importers). Consolidating them is welcome; deleting either without checking imports
is not.

## Three-Bot Architecture

### Bot 1: Companion (Webhook-Triggered)
- Triggered by `pull_request` opened/reopened/synchronize
- Posts TL;DR comment with graphify-informed blast radius
- Flags "📸 ProofShot Required" when UI files change
- Uses local Ollama (`qwen2.5-coder:7b`) at `http://localhost:11434`
- Skip/resume per PR via `@riptide-bot companion skip/resume`
- On-demand deep-think review via `@riptide-bot review` (alias: `deepthink`, `full review`)

### Bot 2: Riptide Review (Cron-Triggered)
- Polls open PRs every 15 min via `riptide/deepthink.py`
- Spawns Hermes deep-think sessions for PRs with >100 LOC + unchanged 30+ min
- Uses graphify + deep-think skill for analysis
- Retries spawn up to 3 times with exponential backoff (5s/10s/20s)
- Does NOT record dedup state on failed spawn (allows retry on next poll)
- Spawned sessions use the `riptide` Hermes profile, pinned by `.env`
  (`RIPTIDE_DEEPTHINK_MODEL`/`_PROVIDER`). **Check `.env`, don't assume** — the code
  defaults (`LongCat-2.0`/`longcat`) are fallbacks only and are not what prod runs
- Posts review comment with findings
- Notes missing proofshot evidence in review comment
- Dedup: same SHA + 24h cooldown, **plus a check that a review comment was actually
  delivered** — SHA-only dedup skipped PRs whose review never landed
- Reservations are released when the job completes/vanishes or the review is
  delivered (stale reservations used to block every later trigger with "Already pending")

### Bot 2b: Autonomous Fix (On-Demand)
- Triggered by `@riptide-bot fix` or `@riptide-bot fix <description>` on a PR
- Handled by `riptide/fixer.py`, routed in `webhook.py` Route 2b
- Parses findings from the latest `@riptide-bot review` comment (or triggers one first)
- Reads the current review format (`## Review:` + severity table); a
  `## Riptide Pass:` comment is not a review and must not be treated as findings
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
- **Prerequisite:** `RIPTIDE_PROOFSHOT_CLI` must point at an existing proofshot CLI;
  if the dev instance is down, captures are skipped (never faked)
- Posts visual evidence (GIF/screenshots) as PR comment
- Dedup: SHA-based only — new commits with UI changes automatically retrigger (no 24h cooldown)

## Review Contract

`docs/REVIEW-CONTRACT.md` is the spec for what a review is and what CI accepts.
The load-bearing rules:

- A findings-bearing review must lead with `## Review:` and include the 🔴/🟡
  severity table, or the `riptide-review-required` gate cannot block the merge.
- `## Riptide Pass: ✅ No findings` is the Companion's deterministic pass — **not**
  a review; code that looks for reviews must not match it.
- Concurrent reviews share `/tmp`: every workstream writes to its own canonical
  path (`conductor._canonical_output_path`). Never reintroduce `/tmp/output.json`,
  `/tmp/findings.json` or a shared `/tmp/pr-<n>-context.json`.
- The scribe posts only a payload stamped `judged: true` with findings; an empty
  result must never render as a clean pass.
- The reviewing model/provider travels with the pipeline
  (`create_*_review_pipeline(model=…, provider=…)` → scribe `--model/--provider`).
  Spawned sessions do not inherit `.env`; a sign-off must never name a model that
  did not run.

## Conventions

### GitHub App Auth
- All GitHub API calls go through `github_app.py`'s `GitHubAppClient`
- JWT auth via App private key (RS256)
- Installation tokens cached with 55-min refresh

### No Template Fallbacks
- If the LLM model is down, Companion stays silent (no comment)
- Riptide Review only spawns when all filter conditions are met

### Cron poller scripts
- `~/.hermes/scripts/riptide-{review,proofshot}-poll.sh` must source the repo
  `.env` before invoking the poller, otherwise the poller pins the code defaults
  instead of the configured provider and every run fails on the fallback chain

### Dependencies
- Adding a new pip dependency needs clear justification
- Prefer transitive deps that already exist

## Docs

Live: `README.md`, `AGENTS.md`, `CHANGELOG.md`, `SECURITY.md`,
`docs/REVIEW-CONTRACT.md`, `skills/` (symlinked into `~/.hermes/skills` — editing
it changes agent behaviour, so it needs review like code).

Historical (do not treat as current): everything in `docs/archive/`. When a
planning doc is superseded, move it there with a dated banner rather than leaving
it at the root to rot.

## Commits and PRs

- Conventional Commits: `feat(scope): …`, `fix(scope): …`, `chore(deps): …`
- One change per PR
- `fix:`/`feat:` commits must carry tests — the `test-required` gate enforces it

## What not to do

- Do not commit secrets
- Do not bypass Git hooks without authorisation
- Do not force-push to shared branches
- Do not add template fallbacks to Companion (by design)
- Do not add vector store / numpy / scipy (removed — graphify handles blast radius)
- Do not remove or bypass proofshot staleness check (5 min minimum before capture)
- Do not hardcode a model/provider in code paths that a spawned session reads
- Do not document a subsystem that does not exist (see `docs/archive/`)

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
