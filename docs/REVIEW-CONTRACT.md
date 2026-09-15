# Riptide Review Contract

What a "Riptide review" *is*, what the CI gate accepts, and how the pieces fit
together. Written because three separate production failures came from these
rules being implicit:

- reviews that could not satisfy the gate (findings posted without a marker),
- a review spawn that silently failed (`database is locked`, provider 401),
- reviews signed with a model name that never ran.

Keep this file accurate when behaviour changes — it is the spec the code and the
gate are held to.

## 1. Comment markers

Riptide writes four kinds of PR comment. Two different consumers care about
them, and they do **not** answer the same question — see the notes below the
table.

| Marker | Written by | Means | Gate? | Poller skip? |
|---|---|---|---|---|
| `## Review: <verdict>` | `assemble_review.py` (Conductor scribe) | Verdict + numbered findings + 🔴/🟡 severity table | **No** | **Yes** |
| `## 🔍 Findings` / `## 🎯 Summary` | legacy `assemble_review` output | Older review formats, still accepted | **Yes** | **Yes** |
| `Riptide Review ·` (sign-off) | `assemble_review.py` sign-off | Emitted on every real review *and* on clean ones | **Yes** | **Yes** |
| `## Riptide Pass: ✅ No findings` | `companion.py` deterministic pass | "The deterministic pass ran and found nothing" — not a review | **Yes** | **No** |

- **The CI gate** matches a body containing `## 🔍 Findings`, `## 🎯 Summary`,
  `Riptide Review ·`, or `## Riptide Pass:`, and ignores the Companion's
  complexity pre-pass (`## ✨ Review Required`)
  (`.github/workflows/riptide-review-required.yml`). It does **not** test for
  `## Review:` — that header is presentational. What carries a real review past
  the gate is the `Riptide Review ·` sign-off, which `assemble_review.py` always
  writes (both `_build_signoff()` and `_build_success_footer()`).
- **The poller's skip decision** uses `deepthink.RIPTIDE_REVIEW_MARKERS`, which
  deliberately excludes `## Riptide Pass:`. A deterministic pass must not make a
  PR look deep-reviewed, or a PR whose review never landed looks reviewed forever.

Never "clean up" the sign-off on the grounds that the `## Review:` header is
enough: dropping it silently un-gates every findings review.

## 2. The CI gate (`riptide-review-required`)

`.github/workflows/riptide-review-required.yml` runs on `pull_request`
opened/synchronize/reopened (it does **not** re-run on comments, so a gate result
can be stale — re-run it or push a commit).

1. Selects the **latest** comment whose body contains `## 🔍 Findings`,
   `## 🎯 Summary`, `Riptide Review ·`, or `## Riptide Pass:`, skipping the
   Companion's `## ✨ Review Required` pre-pass (it posts before the review and
   carries 🟡 rows, so counting it would redden clean PRs).
2. No match → fail: *"No Riptide review found on this PR."*
3. Match with a `| 🔴` or `| 🟡` table row → **fail** until a commit lands after
   the review (the follow-up-commit rule).
4. Match without those rows → pass.

So a findings-bearing review must emit the 🔴/🟡 severity table
(`_build_severity_table` in `assemble_review.py`) **and** carry the
`Riptide Review ·` sign-off: the table rows are what keep the gate red until a
follow-up commit lands, and the sign-off is what makes the comment match at all.
The `## Review:` header is for humans — the gate never looks at it.

## 3. Review pipeline (Conductor)

`@riptide-bot review` (webhook) or the 15-minute poller spawns a Hermes session
that runs `riptide.pipeline.conductor` over the PR's track.

```
ws-1-probe    → writes context        → /tmp/riptide-review-pr-<n>-context.json
ws-2-judge    → writes judged payload → /tmp/riptide-review-pr-<n>-findings.json
ws-3-artisan  → diagram (in-band)
ws-4-engine   → upload (in-band)
ws-5-scribe   → posts the review (in-band)
```

Rules that are load-bearing:

- **One artifact path per (PR, role).** `_canonical_output_path()`; unknown PRs
  fall back to a per-track digest, never a shared `"0"` path. Concurrent reviews
  for different PRs share `/tmp`, so a shared path posts one PR's findings to
  another.
- **Outputs propagate.** `_run_workstream()` merges earlier workstreams' outputs
  into the next brief's inputs (propagated values win over static ones), so the
  judge reads the probe's real path and the scribe reads the judge's.
- **The judge fails loudly** when its context file is missing instead of
  emitting zero findings.
- **The judge stamps `judged: true`.** The scribe refuses to post anything that
  is not `judged`, or that reports `already_reviewed` with no findings. An empty
  result must never render as `## Review: ✅ No findings`.
- **The scribe writes findings to a per-run file**, never a shared
  `/tmp/findings.json`.
- **Verification is role-aware:** only `probe`, `judge`, `warden` and
  `ci_verifier` publish a file, so only they are verified by file existence.

## 4. Reservations (why "Already pending" happened)

`reserve_job()` refuses a new review while a `pending` row exists for the same
prefix, with a 2-hour fallback TTL (`cleanup_stale_pending`). A session that runs
long — or dies — leaves the row behind, so **every later trigger answered
"Already pending" and spawned nothing** (observed live on #190).

`_release_finished_reservations()` now releases a reservation when the cron job
reports `completed`, the job has vanished, it has no further runs, **or the PR
already carries a delivered review**. The last condition is the important one:
Hermes' job record lags a long session, so the delivered review is the reliable
signal.

Symptom → cause:

| Symptom | Cause |
|---|---|
| `⏭️ Already pending`, no review | stale reservation (above) |
| `⚠️ Failed to spawn ... database is locked` | DB write-lock contention while a worker holds the DB |
| spawn ok, no review posted | provider failed → fell through the fallback chain to an out-of-credit provider |
| review posted, gate still failing | missing `## Review:` marker / 🔴🟡 rows, or a stale gate run |

## 5. Model attribution

The sign-off must name the model that actually reviewed. The reviewing
model/provider travels with the pipeline:

```
.env (RIPTIDE_DEEPTHINK_MODEL/PROVIDER)
  → deepthink.spawn()  → create_*_review_pipeline(model=…, provider=…)
  → ws-5-scribe inputs → scribe.post_review_with_assembler(--model --provider)
  → "Riptide Review · model: `…` · provider: `…`"
```

**Never read the model from the spawned session's environment.** Spawned sessions
do not inherit the app's `.env`, so the fallback produced
`custom:LongCat-2.0` on reviews that ran `deepseek-v4-flash`.

The `.env` pin is authoritative — check it rather than trusting any doc:

```bash
grep -E "RIPTIDE_(DEEPTHINK|FIX)_(MODEL|PROVIDER)" /home/sc/workspace/riptide/.env
```

## 6. Triggers and commands

| Trigger | Path | Notes |
|---|---|---|
| `pull_request` opened/reopened/synchronize | Companion TL;DR + labels | `installations` synced via `installation` events |
| `issue_comment` | Companion skip/resume, `@riptide-bot <cmd>` | see `interaction_handler.py` |
| cron `*/15` | Bot 2 poller | >`RIPTIDE_MIN_LOC_CHANGED` LOC, stale ≥ `RIPTIDE_STALENESS_MINUTES` |
| cron `*/10` | Bot 3 proofshotter | UI-file changes only |

Commands: `@riptide-bot review` (alias `deepthink`, `full review`), `fix`,
`visual`, `status`, `help`, `relabel`, `companion skip|resume`.

**Cron poller scripts must source the repo `.env`** (`riptide-review-poll.sh`,
`riptide-proofshot-poll.sh`). Without it the poller pins the code defaults
instead of the configured provider and every poller-triggered review fails on the
provider fallback chain.

## 7. Verifying a change here

```bash
# Unit + integration (there is no pytest workflow in CI — run it locally)
/home/sc/.hermes/hermes-agent/venv/bin/python3 -m pytest riptide/tests -q

# Compile everything the service runs
python -m compileall -q riptide

# What code is actually loaded?
systemctl --user is-active riptide.service && curl -s localhost:8477/health

# Did the last review carry the gate marker and the real model?
gh api repos/<owner>/<repo>/issues/<pr>/comments --jq '.[-1].body' | head -5
```

Live end-to-end check: comment `@riptide-bot review` on a PR, then confirm the
ack, the spawned job (`~/.hermes/cron/jobs.json`), the posted review's first line,
and that a re-trigger after it completes spawns again rather than answering
"Already pending".
