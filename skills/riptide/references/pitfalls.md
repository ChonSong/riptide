# Riptide Pitfalls

## Common Failures

### Ollama Down or Port Mismatch

**Symptom:** `Error: HTTPConnectionPool(host='localhost', port=43311): Failed to establish a new connection`

Port 43311 is the *historical wrong* default: Ollama on this host listens on
**11434**, and `.env` sets `OLLAMA_BASE_URL=http://localhost:11434`. A config
still carrying 43311 fails silently (no comment posted, by design).

**Causes:**
- Ollama not running
- A stale copy of the old 43311 default (code default, resource JSON, or docs)
- Machine rebooted without Ollama auto-start

**Diagnosis:**
```bash
ss -tlnp | grep ollama
curl -s http://localhost:11434/api/tags | head -1     # the real endpoint
grep OLLAMA_BASE_URL /home/sc/workspace/riptide/.env  # must match
```

**Fix:**
```bash
systemctl --user enable ollama.service
systemctl --user start ollama.service
# Fix .env if port mismatch
```

### graphify CLI Not in PATH

**Symptom:** `Graphify error: [Errno 2] No such file or directory: 'graphify'`

**Cause:** `companion.py` calls `graphify` as bare command but systemd service inherits minimal PATH.

**Fix:**
```bash
export PATH="/home/sc/.hermes/hermes-agent/venv/bin:$PATH"
export GRAPHIFY_BIN=/home/sc/.hermes/hermes-agent/venv/bin/graphify
```

### Module Import Errors

**Symptom:** `Grafiphy failed: No module named 'grafiphy'` or `ModuleNotFoundError`

**Cause:** Server runs inside hermes-agent venv which doesn't have `riptide/` on `sys.path`.

**Fix:**
```bash
export PYTHONPATH="/home/sc/workspace:$PYTHONPATH"
```

## Testing Pitfalls

### Before/After Comparisons in a Git Worktree Test the Wrong Code

The dev venv has `riptide` installed **editable**, so `import riptide` resolves to
`/home/sc/workspace/riptide` no matter what the cwd is. Running a script or test
from a `git worktree` checkout of another branch therefore exercises the *main*
checkout's code — a "before" run can pass while testing the fixed code, and a fix
can look verified when it was never loaded.

Do this instead:

```bash
# temporarily restore the pre-fix files in the main checkout, run, then restore
git checkout <base-sha> -- riptide/<file>.py
<run the reproducer or test>
git checkout HEAD -- riptide/<file>.py   # restore the fix
git status --short                       # must be clean
```

### The Suite's Result Depends on Ambient State

`~/.hermes/state/riptide-work-state.json`, the live `state.db` and even the live
`~/.hermes/cron/jobs.json` are reachable from tests on branches that predate the
hermetic conftest. A leftover state file has masked four failures at once. Get the
answer CI will see with a fresh home:

```bash
HOME=$(mktemp -d) /home/sc/.hermes/hermes-agent/venv/bin/python3 -m pytest riptide/tests -q
```

`riptide/tests/test_fixer_ephemeral.py` builds a Docker image, so it is opt-in
(`RIPTIDE_EPHEMERAL_DOCKER=1`) and skips otherwise.

## Webhook Pitfalls

### Falsy pull_request in Test Fixtures

`issue["pull_request"] = {}` is falsy — `is_pr = bool(issue.get("pull_request"))` bails before routing.

**Fix:** Always use `{"url": "..."}` in test fixtures.

### Mock `github_client()` with `return_value=`

```python
# Correct
with patch("riptide.webhook.github_client", return_value=gh_instance):
    ...
```

Without `return_value`, the instance is replaced and ack assertion fails.

### `-F line=` (integer) not `-f line=` (string)

```bash
# Correct
gh api repos/owner/repo/pulls/N/comments \
  --method POST \
  -f body='**🔴 Critical:** fix needed' \
  -f commit_id='abc123' \
  -f path='file.py' \
  -F line=42 \
  -f side='RIGHT'

# Wrong — -f sends string "42", API rejects with 422
gh api repos/owner/repo/pulls/N/comments \
  -f line=42
```

## Poller Pitfalls

### Search Date Format

`updated:>=` only accepts `YYYY-MM-DD`, NOT `YYYY-MM-DDTHH:MM:SS`.

### Search Query

Use direct URL with `requests` params dict — `-f q=` breaks on spaces.

### PAT Source

`gh auth token` reads from `~/.config/gh/hosts.yml`.

## Bot 2 (Deepthink) Pitfalls

### Spawned Sessions Have No PYTHONPATH

```python
import sys
sys.path.insert(0, '/home/sc/workspace')
from riptide.grafiphy.excalidraw_renderer import render_review
```

### `hermes cron create` Positional Prompt

```bash
# Correct — prompt is positional (4th argument)
hermes cron create "2026-07-28T15:08:00" \
  "PR #N review instructions..." \
  --name "riptide-review" \
  --skill deep-think \
  --deliver origin

# Wrong — --prompt is not a valid flag
hermes cron create "2026-07-28T15:08:00" --prompt "PR #N..."
```

### Model Pinning

**The `.env` pin is authoritative — never assume a model/provider, check it:**

```bash
grep -E "RIPTIDE_(DEEPTHINK|FIX)_(MODEL|PROVIDER)" /home/sc/workspace/riptide/.env
```

The review and fix sessions are spawned with exactly those values
(`deepthink.DEEPTHINK_MODEL/PROVIDER`, `fixer.FIX_MODEL/PROVIDER`). The code
defaults (`LongCat-2.0` / `longcat`) are only fallbacks for a missing `.env` and
are **not** what production runs.

- Do **not** add a `custom:` prefix to the model name (e.g.
  `custom:LongCat-2.0`): it is not a provider-qualified model here and produced
  the wrong model attribution in review sign-offs.
- A pin whose provider is out of quota does not fail cleanly: the session falls
  through `fallback_providers` (observed: longcat HTTP 402 → opencode HTTP 401)
  and the spawn dies with `HTTP 401: Insufficient balance`. Symptom: the job in
  `~/.hermes/cron/jobs.json` shows `last_status: error` and **no review comment
  appears**.
- The reviewing model must travel with the pipeline into the scribe
  (`create_*_review_pipeline(model=…, provider=…)`). Spawned sessions do **not**
  inherit the app's `.env`, so anything read from the session environment falls
  back to the code default and the sign-off names the wrong model.
- Cron poller scripts must source the repo `.env` before invoking the poller
  (`riptide-review-poll.sh`, `riptide-proofshot-poll.sh`); without it the poller
  pins the code defaults and every poller-triggered review fails.

See `docs/REVIEW-CONTRACT.md` for the full contract.

### Review comment markers (gate deadlock and false passes)

The CI gate (`riptide-review-required`) only recognises a comment carrying
`## Review:`, `## 🔍 Findings`, `## 🎯 Summary`, `Riptide Review ·`, or
`## Riptide Pass:`, and only *requires a follow-up commit* when the body has a
`| 🔴` / `| 🟡` table row.

- A findings-bearing review **must** lead with `## Review:` and emit the severity
  table, or findings cannot block a merge.
- The Companion's `## Riptide Pass: ✅ No findings` is **not** a review — the
  poller deliberately does not treat it as one, so PRs whose deep-think review
  never landed still get re-reviewed instead of looking reviewed forever.
- The gate does not re-run on comments; a failing/passing result can be stale
  (re-run it or push a commit).

### "Already pending" — stale review reservations

`reserve_job()` refuses a new review while a `pending` row exists, with a 2-hour
TTL. A long-running or crashed session used to hold that row, so every later
`@riptide-bot review` answered *"Already pending"* and spawned nothing.

`_release_finished_reservations()` now releases when the job completed,
vanished, has no further runs, **or the PR already carries a delivered review**
(the job record lags long sessions, so the delivered review is the reliable
signal). To inspect or clear by hand:

```python
from riptide.state import StateStore
from riptide.deepthink import _release_finished_reservations
st = StateStore()
_release_finished_reservations(st, "riptide-review-<owner>-<repo>-<pr>", "<owner>", "<repo>", <pr>)
```

### Conductor artifacts are per-PR (never shared)

Review sessions run concurrently and all use `/tmp`. Every workstream must write
to its own canonical path: `/tmp/riptide-review-pr-<n>-<role>.json`
(`conductor._canonical_output_path`). Never reintroduce `/tmp/output.json`,
`/tmp/findings.json` or a shared `/tmp/pr-<n>-context.json`. The scribe refuses to
post when it has no findings, and only when the judge payload is stamped
`judged: true` — an empty result must never render as `## Review: ✅ No findings`.


### Stale State from Manual Runs

Running `deepthink.py` manually records every qualifying PR's SHA + timestamp, blocking re-processing by the real cron.

**Fix:**
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/sc/.local/share/riptide/state.db')
conn.execute(\"DELETE FROM pr_heuristics WHERE pr_key LIKE '%#N'\")
conn.commit()
"
```

## Production Deployment Discipline

**NEVER modify workspace files that production imports without going through the full PR process first.**

Production runs from `/home/sc/workspace/riptide`. The server imports modules directly from this directory.

### The Only Allowed Workflow

```
1. Create branch  →  git checkout -b feat/xxx
2. Make changes   →  edit files on branch
3. Commit + push  →  git push origin feat/xxx
4. Open PR        →  gh pr create
5. User reviews   →  WAIT for explicit approval
6. Merge          →  git merge (only after user says "merge it")
7. Deploy         →  pull main, clean __pycache__, restart server
```

### Server Restart Required

Code changes do not take effect until the server process restarts. The server does not hot-reload.

```bash
systemctl --user restart riptide.service
sleep 4 && curl -s http://localhost:8477/health
```

### Clean Restart (stale .pyc prevention)

```bash
systemctl --user stop riptide.service
find /home/sc/workspace/riptide -type d -name __pycache__ -exec rm -rf {} +
systemctl --user start riptide.service
```

## Tunnel Edge Config Goes Stale

After adding hostname to local `config.yml`, Cloudflare edge may stay on old version.

**Fix:** API config push with `cfut_` token from `~/.cloudflared/cert.pem`.

## `via_app` AttributeError on Issue Comments

```python
# Wrong — crashes when field is None
via_app = comment.get("performed_via_github_app", {})
if via_app.get("id"):  # AttributeError: 'NoneType' has no attribute 'get'

# Correct
via_app = comment.get("performed_via_github_app") or {}
```

## Lockout Bug Pattern

When a function returns a string on every path (success AND errors), callers using `if result:` treat all returns as success. This permanently blocks retries.

```python
# Buggy — marks "spawned" on error strings
if result:
    _mark_processed(conn, comment_id, '{"result":"spawned",...}')

# Fixed — only mark spawned on actual success
if result:
    spawned = "Riptide Fix triggered" in result
    status = "spawned" if spawned else "not-spawned"
    _mark_processed(conn, comment_id, f'{{"result":"{status}",...}}')
```

## CI Test Gate — Per-Commit Checks

The `test-required` CI gate checks **each commit individually**, not just the PR diff. Every `feat:` / `fix:` commit must include paired test changes.

**Rule:** When adding tests to a feature commit, either amend the existing commit or squash them together. CI checks each commit individually — a separate "add tests" commit will fail the gate if the original feature commit has no tests.

## Scope Isolation for Reviews

Riptide's inline comments must ONLY reference files in this PR's diff. Do NOT reference other PRs, other extensions, or code not touched by this PR.

```markdown
## Scope Isolation
ONLY review files in this PR's diff. Do NOT reference other PRs, other extensions, or code not touched by this PR.
```

## Model Attribution Required

Every review comment MUST include model attribution:

```markdown
---
<sub>🤖 Riptide Review via Hermes · model: <model_name> · `@riptide-bot companion skip` to opt out</sub>
```
