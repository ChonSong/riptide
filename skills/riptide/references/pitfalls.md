# Riptide Pitfalls

## Common Failures

### Ollama Down or Port Mismatch

**Symptom:** `Error: HTTPConnectionPool(host='localhost', port=43311): Failed to establish a new connection`

**Causes:**
- Ollama not running
- Port mismatch: `.env` has `OLLAMA_BASE_URL=http://localhost:43311` but actual server is on `11434` (or vice versa)
- Machine rebooted without Ollama auto-start

**Diagnosis:**
```bash
ss -tlnp | grep ollama
curl -s http://localhost:11434/api/tags | head -1
curl -s http://localhost:43311/api/tags | head -1
grep OLLAMA_BASE_URL /home/sc/workspace/riptide/.env
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

**CRITICAL:** Use `LongCat-2.0` with provider `longcat`. NEVER use `custom:` prefix.

```bash
hermes cron create "..." "..." \
  --model "LongCat-2.0" \
  --provider "longcat"
```

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
