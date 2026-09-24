# Riptide — Triggers, Variables, and Stopping Conditions

**Status: draft.** This document traces each trigger to the variables it sets and the condition
that stops it. Written from verified evidence; two critique passes (correctness, verbosity) have been
applied. Awaiting human review before it is treated as current.

Companion docs: `AGENTS.md` (per-bot behaviour), `docs/REVIEW-CONTRACT.md` (what a review must
contain and what CI accepts). This file answers a different question: *what gets passed where, and
what ends each path.*

## 1. Triggers

| Trigger | Fires when | Entry point | Sets |
|---|---|---|---|
| Webhook | PR `opened` / `reopened` / `synchronize` | `riptide/webhook.py` | Companion TL;DR comment; installation + PR context |
| Webhook, bad signature | Any | `webhook.py` | Returns **200**, not 401, so the cron poller picks the PR up instead of GitHub retrying (`webhook.py:262`) |
| Review poller (cron) | Every 15 min | `riptide/deepthink.py` | Reservations, spawn attempts; the scheduled review session |
| Proofshot poller (cron) | Every 10 min | `riptide/proofshotter.py` | UI-file detection; capture attempt (claim retired, see §4) |
| `@riptide-bot review` | Comment (aliases: `deepthink`, `full review`) | webhook → poller | On-demand review, same downstream as the poller |
| `@riptide-bot fix [description]` | Comment; author, repo owner or ChonSong only | `riptide/fixer.py` | A fix session; ack comment naming the spawned job |
| `@riptide-bot proofshot` / `visual` | Comment; author or owner | `riptide/visual.py` | User-initiated visual capture (not a bot claim) |
| `@riptide-bot companion skip` / `resume` | Comment | `webhook.py` | Per-PR Companion state |
| CI gate `riptide-review-required` | Every push | `.github/workflows/` | Red until a review-shaped comment exists after the head commit |

## 2. Variables and identifiers

- **Review track id**: `riptide-review-<owner>-<repo>-<pr>` — one string used as the Conductor
  track id (`conductor.py:489`) *and* the cron job name (`deepthink.py:387`, via
  `hermes cron create --name`). Chased with `hermes cron list`.
- **Fix job name**: `riptide-fix-<owner>-<repo>-<pr>`, from a single helper `_fix_job_name()`
  (`fixer.py`), so the name in the ack cannot drift from the name actually scheduled.
- **Model / provider**: read from `.env` (`RIPTIDE_DEEPTHINK_MODEL`/`_PROVIDER`,
  `RIPTIDE_FIX_MODEL`/`_PROVIDER`). **Spawned sessions do not inherit `.env`**, so the value is
  threaded explicitly through the pipeline to the scribe's `--model`/`--provider`. Code defaults
  (`LongCat-2.0`) are fallbacks only and are not what runs.
- **Canonical output paths**: every workstream writes its own path via
  `conductor._canonical_output_path(pr_number, role)`. Concurrent reviews share `/tmp`, so a shared
  `/tmp/output.json` or `/tmp/findings.json` reintroduces cross-review corruption.
- **Reservation key** and **dedup key**: both SHA-based. Dedup adds a 24h cooldown *and* a check
  that a review comment was actually delivered.
- **Depth classification**: `trivial` = under 10 changed logic lines and no logic files;
  deep-think is reserved for PRs over 100 changed LOC settled 30+ minutes.
- **Two retry delays, deliberately distinct.** The spawn retry doubles a 5s base to 5s/10s/20s
  (`deepthink.py:562`); a separate loop waits 2s/4s (`:405`) for a different retry context. The
  `:380` docstring still claims 5s/15s/30s as this is written: correcting it is #218, which is
  open, and this doc does not depend on it landing. The delays themselves were left as they run.
- **Fix cooldown**: `FIX_COOLDOWN_SECONDS` (default 300s, from `RIPTIDE_FIX_COOLDOWN`) throttles
  repeat fix requests (`poller.py:60`).
- **Findings payload**: only a payload stamped `judged: true` may be posted. An empty result must
  never render as a clean pass.

## 3. Workstream chain

| Role | Consumes | Must produce |
|---|---|---|
| `probe` (ws-1) | PR metadata, diff | PR context at its canonical path |
| `judge` (ws-2) | PR context | Findings stamped `judged: true` |
| `artisan` (ws-3) | Findings path | Diagram; declares `pipeline=["excalidraw", "upload"]` |
| `engine` (ws-4) | `inputs["command"]` | **Intended**: uploaded diagram URL. **Actual**: cannot deliver it (§4) |
| `scribe` (ws-5) | Findings, `diagram_url`, model/provider | The posted review body + `Riptide Review ·` sign-off |
| `warden` | Pipeline artifacts | Verification verdict |

## 4. Stopping conditions, per scenario

| Scenario | Stop condition | Records / releases | If it misfires |
|---|---|---|---|
| Review delivered | Comment posted with the sign-off | Dedup recorded; reservation released | — |
| Spawn failed | Retries exhausted | **Nothing** — no dedup, so the next poll retries | A failed spawn silently parks the PR; the retry delay is inconsistent (§2) |
| Review gate | The last review-shaped comment that is *not* a pass, with commits counted after its `created_at` (`riptide-review-required.yml:62`) | Gate goes green | **False green**: a follow-up commit satisfies the gate without resolving the finding; green ≠ addressed |
| Already reviewed | Same SHA + 24h cooldown | Skips | SHA-only dedup once skipped PRs whose review never landed |
| Stale reservation | Job completes/vanishes, or the review is delivered | Reservation released | Stale reservation blocks every later trigger with "Already pending" |
| Fix requested, cron CLI absent | Refuses and says so | **No** `fix_queue` row | A row nothing drains would block that PR permanently |
| Fix already queued | Row younger than `QUEUE_BLOCK_MAX_AGE_SECONDS` (= `FIX_TTL_SECONDS`, 2h) | Blocks a second fix | A row left by an older deployment would hold the gate |
| Proofshot | Claim retired | Nothing emitted | The bot promised evidence nothing could produce |
| Diagram upload | — | — | `ws-4-engine` shell stdout cannot write `diagram_url` into the track inputs, so `[Diagram](url)` never appears even if the command resolved |

## 5. Failure modes this document exists to prevent

Each of these has produced a real defect:

1. **A start with no stop.** A reservation or queue row that nothing releases or drains blocks the
   very thing it represents.
2. **A proxy accepted as the substance.** `mergeable` instead of `mergeStateStatus`; a green check
   instead of addressing the finding; a truncated grep read as complete.
3. **A declaration with no supplier.** `code_chunks` and `frontend_components` are renderer
   parameters no caller ever populates; a workstream asks for a command that cannot resolve.
4. **A claim with no capability.** Companion promised proofshot evidence that nothing could capture.
5. **Silent shrinkage.** A conditional section that renders as *nothing* when its input is missing,
   instead of saying what is missing.

## 6. Verifying a scenario from outside

```bash
gh pr checks <n>                                   # gate + baseline + test-required state
hermes cron list                                    # scheduled jobs, including review/fix sessions
gh pr view <n> --json mergeStateStatus,statusCheckRollup
systemctl --user is-active riptide.service && curl -s localhost:8477/health
```

State (read-only): `~/.local/share/riptide/state.db` — tables `jobs`, `work_queue`, `review_memory`,
`fix_queue`, `deliveries`, `processed_comments`. A review's provenance belongs in
`review_memory.metadata` (job name, session id, model, provider, `head_sha`) so it can be queried
rather than scraped from comments.

## 7. Open gaps

- Reviews are not yet chaseable from GitHub: the sign-off carries `model:` and `provider:` but no
  job or session id.
- The `ws-4-engine` upload path cannot deliver `diagram_url` (see §4); `pipeline/engine.py` runs
  shell commands, so it cannot write back into track inputs.
- No test asserts that the triggers, identifiers and paths in this document still exist.
