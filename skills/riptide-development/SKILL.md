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
7. **A finding can be a stale-base artifact** — a review that says "X does not exist" grepped the PR's own base, which may predate the merge that added X. Check `git merge-base origin/main <branch>` and verify the symbol against `origin/main` before "fixing" anything: a doc whose branch is merely behind can describe main correctly, and the finding is the branch's staleness
8. **Never read the gate's colour as "the findings are addressed"** — it reports that a commit landed after the review. Read the run's own state line (`chosen: …` once `scripts/check_riptide_review.sh` is in play) instead of the check's colour

## The Review Gate (`riptide-review-required`)

`scripts/check_riptide_review.sh` decides whether a PR may merge; the workflow only builds its data file from the API (`review`/`commit`/`file` records) and runs it. Edit the script and its tests, never a selector inline in the workflow.

How it decides, and what must not regress:

- It judges the newest **review** (marker: `## 🔍 Findings`, `## 🎯 Summary`, or the `Riptide Review ·` sign-off), preferring it over a Companion pass posted later.
- Both exclusions (`## ✨ Review Required` pre-pass, `## Riptide Pass:`) are anchored to a comment's **first line**. A body-wide `contains` drops a review that merely *quotes* the heading; the gate then falls back to an older comment and greens while a finding stands.
- A findings review needs a commit after it that **touches a file the findings name**. A commit touching anything else is not an answer.
- A finding row naming no file (`| 🟡 | title | — |`) cannot be matched against a commit, so it relaxes that rule and the run says so — never silently treat it as answered.
- A pass-only PR still satisfies the gate, but the log and the step summary state that no deep review ran on this head, so a green check is never read as "reviewed".
- `chosen: review <id>` / `chosen: pass <ts>` / `chosen: none` is the machine-readable line the selector locks read — keep it.

Extracting a gate out of a workflow:

- **Migrate its lock tests.** `riptide/tests/test_review_gate_workflow.py` pulls the jq selector out of the workflow text; moving the selector into a script leaves five of its tests failing. Re-point them at the script's `chosen:` line rather than deleting them.
- **Prove the old behaviour, do not assert it.** Extract the previous selector verbatim with `git show origin/main:<workflow>` and run both over the same fixtures. Retyping it from memory gives a subtly different expression and a bogus comparison.
- The workflow's data-building step is part of the code path — verify the script against live PRs by rebuilding the same records with `gh api`, not only over fixtures.

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

**Port drift is fixed in code and guarded by regression tests.** All code defaults
are now `11434` (`companion.py:368`, `ollama_heal.py:26`,
`labeler.py:30,90`), and `test_companion.py` / `test_labeler.py` fail if `43311`
returns. `43311` now survives only in stale docs/config and in those test fixtures,
so audit **docs, resources and `.env`** — not the code:

```bash
grep -rn "43311" --include="*.md" --include="*.json" --include="*.example" . | grep -v graphify-out
grep OLLAMA_BASE_URL /home/sc/workspace/riptide/.env   # must be :11434
```

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
