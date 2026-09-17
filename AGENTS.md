# AGENTS.md

Repo-scoped rules for AI agents reviewing or editing this codebase.

## Before submitting a change

```bash
# Compile everything the service runs (cheap; catches the NameError class of bug
# that reached production through the spawn path)
python -m compileall -q riptide

# CI runs the suite and compares the failures with riptide/tests/baseline_failures.txt
# (.github/workflows/pytest.yml -> scripts/check_test_baseline.py). A NEW failure
# fails the gate; a baseline entry that starts passing is reported as stale but
# does not fail (a passing test is not a regression). The baseline is empty on
# purpose — fix regressions instead of adding to it, and measure CI's answer, not
# your machine's, if the two ever disagree.
#
# The suite is hermetic (riptide/tests/conftest.py keeps every state path inside
# a temp dir), so a run in a dirty home now gives the same answer as CI. Before
# that, a leftover ~/.hermes/state/riptide-work-state.json masked four failures.
/home/sc/.hermes/hermes-agent/venv/bin/python3 -m pytest riptide/tests -q
```

## Testing traps (each one cost real time)

- **The dev venv has `riptide` installed editable.** `import riptide` resolves to
  `/home/sc/workspace/riptide` regardless of cwd, so a git **worktree does not
  exercise its own code**. Before/after comparisons must stash/restore the files
  (`git checkout <base> -- <paths>`, then restore) or copy them aside — a
  worktree run silently tests the main checkout.
- **Get CI's answer with the hermetic conftest in place** (`riptide/tests/`).
  Ambient state used to change which tests failed: the suite wrote the live
  `~/.hermes/cron/jobs.json`, the live SQLite DB, and read the developer's
  `graphify-out/graph.json`.
- **`riptide/tests/test_fixer_ephemeral.py` is opt-in** — `setup_class` builds a
  Docker image and starts a container, so it is skipped unless
  `RIPTIDE_EPHEMERAL_DOCKER=1`. It used to raise `NameError` (no `import pytest`)
  instead of skipping.
- **A full run is ~40s once hermetic** (it was ~7 min while 25 tests contended
  with the running service for the real SQLite DB).

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
- Ack comment names the spawned Hermes job (`riptide-fix-<owner>-<repo>-<n>`,
  from `_fix_job_name`) so it can be chased with `hermes cron list`
- `@riptide-bot fix` never writes `fix_queue`: nothing drains it
  (`process_fix_queue` is unwired), so a row would block that PR permanently — the
  busy check counts it — and silently swallow every later request. When the Hermes
  cron CLI is absent the command says it could not start instead.
- A `queued` row only blocks while it is younger than `QUEUE_BLOCK_MAX_AGE_SECONDS`
  (= `FIX_TTL_SECONDS`, 2h), so a row left behind by an older deployment cannot hold
  the gate.

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

- A findings-bearing review must carry the `Riptide Review ·` sign-off (always
  emitted by `assemble_review.py`) **and** the 🔴/🟡 severity table. The
  `## Review:` header is human-facing — the gate does **not** test for it. The
  table rows are what keep the gate red until a follow-up commit lands, and the
  sign-off is what makes the comment match at all. The gate also ignores the
  Companion's `## ✨ Review Required` complexity pre-pass: it posts *before* the
  review and carries 🟡 rows, so treating it as a review reddens clean PRs.
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
- The gate is `scripts/check_test_required.sh` (tested by
  `riptide/tests/test_test_required_gate.py`); `.github/workflows/test-required.yml`
  only feeds it the PR's commits. A `fix:`/`feat:` commit passes when it touches a
  test file **or** carries a `No-Tests: <reason>` trailer in the commit body. Use
  that trailer only when there is genuinely no test to add, and say what you
  checked in place of one — restoring code a merge dropped, or a CI/config-only
  fix. A `fix:`/`feat:` commit with neither is still red, and an empty reason is
  not an exemption:

  ```text
  fix(state): restore the review_memory schema

  No-Tests: restores a hunk a merge dropped; riptide/tests/test_state.py already
  covers the path, so there is no new behaviour to test.
  ```

  The trailer must be the trailing block of the commit body (a `No-Tests:` line in
  the subject, or one followed by a later paragraph, is not a trailer), the token
  is matched case-insensitively, and the reason must be non-empty.

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
