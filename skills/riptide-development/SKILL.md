---
name: riptide-development
description: "Use when working on the Riptide auto-review bot codebase."
---

# Riptide Development

Development principles and patterns for the Riptide codebase.

## Core Principles

### Deterministic Python > LLM

Never delete deterministic Python code and replace it with LLM generation. When consolidating or refactoring, wire Python processes together: pre-generate data/rendering in Python, then have the LLM reference it.

### Environment Variable Preservation

When editing config-bearing code (URLs, paths), preserve the `os.environ.get()` pattern with sensible defaults. Never hardcode values that main branch keeps configurable.

### GitHub Push Protection Avoidance

GitHub push protection blocks commits containing strings that look like secrets. In test fixtures that need to trigger secret-detection patterns, construct values at runtime via string concatenation with split fragments.

## Code Review Discipline

When the user asks for a review-and-fix pass on a PR:

1. **Verify each finding against actual code** — read the file from the PR branch, don't assume based on review text
2. **Fix only still-valid issues** — skip the rest with a brief reason
3. **Keep changes minimal** — don't bundle unrelated cleanups
4. **Validate before reporting** — `python -m py_compile` + `python -m pytest -q`
5. **Regex audit** — when a review claims a regex is broken, test it directly: `python3 -c "import re; print(re.compile(r'...').search('eval('))"`
6. **Prove pre-existing failures with git stash** — when the suite has failures you believe are unrelated, `git stash`, run the failing file on the clean tree, confirm it still fails, then `git stash pop`

## Rebase Workflow for Stacked PRs

When PRs are stacked on old main and conflict with current main:

1. Create fresh branch from current main: `git checkout -b <name> origin/main`
2. Copy only the feature files from the PR branch: `git checkout origin/<pr> -- files...`
3. Manually integrate into the existing main version of shared files
4. Verify: `python -m py_compile` + `python -m pytest`
5. Commit, push, open PR

**When the base was REBUILT (squash + force-push) instead of old-main**, the cherry-pick rebuild is the reliable pattern — `git rebase --onto` replays stale commits from the old base and `--skip` can silently drop the real feature commits.

## Merging with Branch Protection

This repo requires 1 approving review. GitHub won't let the PR author approve their own PR — so self-owned PRs can NEVER pass review. The only merge path is the admin bypass:

```bash
gh pr merge <N> --squash --delete-branch --admin
```

**Squash-merge breaks every PR stacked on top** — each stacked branch still contains the lower PR's commits un-squashed, so GitHub reports "merge conflicts" / `DIRTY` against main (same content, different SHA). Fix:

```bash
git fetch origin main && git checkout <stacked-branch>
git rebase origin/main          # applies cleanly for squash-stack cases
git diff origin/main --stat     # verify ONLY this PR's files remain
git push --force-with-lease origin <stacked-branch>
```

## State Management

### Schema Migrations (state.py)

When adding schema migrations to `StateStore`:
1. Run migrations BEFORE updating `schema_version` — if migration fails, the version must remain at the old value
2. Wrap `ALTER TABLE` in `try/except sqlite3.OperationalError` for idempotent re-runs
3. Batch inserts into a single transaction, not per-row commits

### Migration Testing

Never touch real user data in tests. Patch `riptide.state.POLLER_DB_PATH` to a temp path and create legacy schemas programmatically.

## Deploy Patterns (scripts/deploy.sh)

- `set -euo pipefail` is active — every command must handle its exit code. Use `|| true` for commands that legitimately return non-zero
- Use `pgrep -Ef` (extended regex), not `pgrep -f` (basic regex where `()` are literal)
- Remove `--collect` from `systemd-run` — it creates a race condition with `start_new_session=True`

### Verifying a Deploy

```bash
systemctl --user is-active riptide.service
systemctl --user status riptide.service --no-pager
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8477/health
```

**App logs are NOT in journald in `--prod`** — `server.py` adds a RotatingFileHandler at `RIPTIDE_DATA_DIR/riptide.log`. `journalctl --user -u riptide.service` shows ONLY uvicorn/gunicorn access lines.

### Live Verification — Unit Tests Can Mask Dead Code

After deploying behavior-changing bot code, verify the LIVE artifact (real comment on a real PR) reflects the new behavior, not just health + tests. The repo's own PRs are free live-test fixtures.

**Detection recipe:**
```bash
gh api "repos/ChonSong/riptide/issues/<N>/comments" \
  --jq '.[] | select(.user.login=="riptide-review[bot]") | .body'
```

- Footer `_Reviewed by Riptide T0` + giphy + "reviewing..." = LEGACY path ran
- Tier-1 deterministic body (findings + "🔍 enrichment in progress" marker) = new path ran

**Grep for production callers of the method under test BEFORE shipping** — if the only hits are the definition and tests, the entry path is missing.

## Cron Output Debugging

When `@riptide-bot review` posts confirmation but no review ever appears:

```bash
# Get job ID from logs
grep "Created job\|Spawned deep-think" riptide.log | tail
# Read error from cron output
tail -20 ~/.hermes/cron/output/<job_id>/*.md
```

**Common errors:** HTTP 401 (billing), HTTP 404 (model), HTTP 504 (timeout), `context_length_exceeded` (PR too big).

**Key facts:**
- One-shot cron jobs vanish from `hermes cron list` after completion — output dir persists
- Confirmation comment ≠ completion. Riptide posts confirmation BEFORE the cron job runs

## Ollama Connectivity

Ollama on this host runs on the **standard port 11434**, NOT 43311. A wrong port is a SILENT failure.

```bash
# Probe the real endpoint
curl -s localhost:11434/api/tags
# Check .env matches
grep OLLAMA_BASE_URL /home/sc/workspace/riptide/.env
```

**When auditing for wrong default port, grep ALL of these:**
1. `companion.py` — `os.environ.get("OLLAMA_BASE_URL", "http://localhost:43311")`
2. `labeler.py` — TWO places
3. `riptide/resources/label-definitions.json` — resource JSON overrides code default

## Graphify

- **graphify** = codebase knowledge graph tool. Output lives in `graphify-out/YYYY-MM-DD/`
- **graphify_ingest** = Riptide's deterministic Excalidraw pre-generator

## Cron Job Prompt Budgets

Current orchestrator prompt sizes:
- Small PR: ~2,300 chars (~575 tokens)
- Medium PR: ~12,700 chars (~3,150 tokens)
- Large PR: ~17,400 chars (~4,350 tokens)

Plus loaded skills (deep-think: 20k chars, github-pr-lifecycle: 53k chars). Total context for a large PR: ~90k chars ≈ 22k tokens.

## Testing Conventions

- CI requires test files in every `feat:`/`fix:` commit. Bundle tests with code changes in the same commit
- **Never `write_file` a "new" test file blindly** — if the path already exists it silently REPLACES a tracked file. Check `git show HEAD:<path> | head` first
- Use `patch` for targeted edits, not sed/awk
- Verify with `python -m py_compile` + `python -m pytest`, not just claims of working
- Test isolation: use `tempfile.mkdtemp()` and patch module-level path constants

## References

- `references/unified-pipeline-design.md` — WS-3 architecture, 5-stage model
- `references/state-heuristics-centralization.md` — StateStore and dedup
- `references/cron-output-debugging.md` — Bot 2 stall diagnosis and recovery
- `references/ollama-port-silent-failure.md` — Wrong default port detection
- `references/stacked-pr-rebuild.md` — Squash-merge rebuild recipe
- `references/context-bundle-design.md` — Deterministic context bundle
- `references/two-tier-response.md` — Tier 1 + Tier 2 comment architecture
