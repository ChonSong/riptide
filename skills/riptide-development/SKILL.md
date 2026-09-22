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

## Bot 3 (proofshotter) — capture target and guards

Scope: the target/guard behaviour in this section lands in **#220** (it is not on
`main`); the instance facts and the playwright trap are true today. Read the
contract below as intended, not deployed, until #220 merges.

- **`proofshot/cli.py` is a single-file CLI, not a package.** It carries no
  `pyproject.toml`/`setup.py`, so `pip install -e ~/workspace/proofshot` fails.
  `proofshotter.py` does NOT call `cli.py pr`; it loads `ProofshotSession` from that
  file via importlib, so the dependency to satisfy is the class, not the subcommand.
- **`cli.py pr <n>` requires `--url`** (argparse `required=True`) and is a hardcoded
  chat-tiling walkthrough that posts its own comment and release upload. Do not wire
  it into CI — it cannot run as written and duplicates the bot's own path.
- **A capture target must be declared** (`url` in `proofshot.config.json`, or
  `RIPTIDE_PROOFSHOT_URL`). Never reintroduce a `localhost:8788` default: a repo
  declaring nothing must be skipped (`skipped(no-target)`), not captured.
- **8788 is `hermes-webui-dev.service`** — this project's own dev instance serving
  `master`, not "an unrelated application". A capture there races its user and cannot
  show a PR's change. The dedicated test instance is **8790**, booted with
  `HERMES_WEBUI_SKIP_ONBOARDING=1`. `hermes-webui-tests/lib/auth-fixture.ts` is a
  **no-op** — it does not log in; that skip-onboarding flag is what bypasses the gate.
- **A login gate answers HTTP 200**, so a status code can never tell it from the app
  shell. Check the rendered DOM (`_assert_capture_is_app_shell`) before posting.
- **The playwright browser is already installed — do not reinstall it.** The venv's
  playwright (1.62.0) expects chromium revision **1234** and
  `~/.cache/ms-playwright/chromium-1234` matches it (installed 2026-09-17, with
  `headless_shell` alongside). A real capture against `:8790` produced a GIF, so the
  capture half works on this host. What breaks a launch is
  **`NODE_OPTIONS=--gc-interval=100`**, the value agent terminal sessions carry:
  playwright's bundled node aborts and the API reports `Connection closed while
  reading from the driver`, which is easily misread as a missing browser.
  `--max-old-space-size=4096` (what the services run with) is fine. Run captures as
  `env -u NODE_OPTIONS ...`. Microsoft's CDN does answer a reinstall with
  `400 GatewayExceptionResponse` here — that is why the reinstall fails, not because
  the browser is absent, and no reinstall is needed.

## Worktrees DO exercise their own code

AGENTS.md's warning is stale on this host: from `/tmp/wt-<name>`, `import riptide`
resolves to the **worktree's** `riptide/`, not the shared checkout. Confirm it rather
than assuming either way:

```bash
cd /tmp/wt-<name> && /home/sc/.hermes/hermes-agent/venv/bin/python3 \
  -c "import riptide; print(riptide.__file__)"
```

Take a real before/after inside the same worktree (`git checkout --detach origin/main`,
run, then `git checkout <branch>`); the collected test count is the tell that the
intended tree ran.

Caveat — the editable install still wins in a **subprocess that runs a module as a
script**. `riptide/tests/test_entrypoints.py` spawns
`python3 riptide/deepthink.py --help` with `PYTHONPATH=""`, so `sys.path[0]` is the
worktree's `riptide/` (which holds no `riptide` package) and `import riptide` falls
through to the editable install → the **shared checkout's** package runs against the
worktree's script. That is how a worktree run can report one failure that CI never
sees (it appeared as `ImportError: cannot import name ... from
'/home/sc/workspace/riptide/riptide/...'`). For an answer that matches CI, copy the
changed files into the shared checkout, run there, then `git checkout --` them
(only tracked files; delete any file the branch adds).

## Review provenance

Scope: the `review_memory` and sign-off behaviour below lands in **#219**; `main`
still writes no provenance. The "one builder" rule is what a review of #219 asked
for, and #219's follow-up commit enforces it in `deepthink` and `conductor` too.

- **`review_memory` was written only on merge** (zero counts, no attribution), and its
  `metadata` column was **double-encoded** — `json.dumps` applied to an
  already-encoded string, so the column parsed back to a string, not a dict. Rows are
  now written when a review posts, carrying `{job, model, provider, head_sha}`. The
  merge-time writer cannot know which model reviewed, so capture that on the review
  path instead of reconstructing it later.
- **The sign-off handle is `riptide-review-<owner>-<repo>-<n>`** — the same string the
  spawner passes to `hermes cron create --name`. Keep one builder for it.
  `Riptide Review ·` must stay byte-identical: the CI gate and the fixer's
  review-detection both match that literal.
- **A cron-spawned session really does know its own session id**
  (`agent.agent_init._publish_session_id` →
  `gateway.session_context.set_current_session_id`, published to `os.environ` as
  `cron_<job_id>_<YYYYmmdd_HHMMSS>`); the webhook/service process has none. Render
  `session:` only when present — never invent one.
- **Verify a parked patch's call sites before building on it.** A patch that adds
  parameters to a helper is dead code unless its caller passes them; a diff's
  description is not evidence. Grep the call sites.

## A capture guard must test visibility, not presence

Matching a login-gate selector by **presence** refuses legitimate captures. Measured
on hermes-webui:

| | `:8790` app shell | `:8788` login gate |
|---|---|---|
| final URL | `/` | `/login?next=/` |
| password inputs | 2, **0 visible** (`#settingsPassword`, `#settingsCurrentPassword`) | 1, **visible** (`#pw` inside `#login-form`) |
| app markers | `main`, `nav`, `.sidebar`, `header` | none |
| body | `Chat \| WebUI sessions (0)` | `Enter your password to continue` |

So a gate match only counts when the element is **rendered** (`element.is_visible()`),
and the URL path is checked separately: the real gate redirects to `/login` on the
**same host**, so a cross-origin check alone does not catch it.

A guard that fires on the app is worse than no guard — Bot 3 then reports every
capture as impossible, and the symptom looks like a missing target rather than a
bad predicate. Verify a guard against the real app before trusting it; a unit test
with a hand-built page double cannot tell you this.

## The Companion's nesting metric is diff-scoped and mis-attributes

`DiffAnalyzer` (`riptide/diff_analyzer.py`) measures nesting over the patch's
**added lines** using indentation, and only resets `current_func` on an added line
at or below the function's definition indent. Two consequences:

- a changed argument inside a multi-line call whose **opening line is unchanged
  context** is counted as a statement at its raw indent (e.g. level 5), because the
  open bracket is invisible to the counter — even though it is a call continuation,
  not a nesting level;
- the finding is then attached to **whichever function the counter last saw**, not
  the function containing the line. A 🟡 naming function X can be about a line in
  function Y.

So verify before "fixing" one. Re-run the analyzer over your diff and print the
stack progression (`_get_added_lines` + `_nesting_level` + `_bracket_delta`) to see
which line actually crosses `MAX_NESTING_DEPTH`. A real example: a 🟡 against
`_assert_capture_is_app_shell` was really `captured_url=url,` in the poll path's
`_post_proofshot_comment(...)` call — pre-existing indentation, tripped only because
that argument was the added line.

**Do not restructure working code to satisfy the metric.** Extracting a function to
reduce real nesting is fine on its own merits, but it will not clear this finding;
say so rather than implying it did.

## Corrupt clones

A workspace directory holding only `.git` + `node_modules` where `git` reports
"fatal: not a git repository" is an aborted clone. `~/workspace/proofshot` and
`~/workspace/hermes-webui-tests*` were all in this state. Re-clone from the remote
(`git clone https://github.com/ChonSong/<repo>.git <dir>`); do not try to repair
the `.git`.

## References

- `references/unified-pipeline-design.md` — WS-3 architecture, 5-stage model
- `references/state-heuristics-centralization.md` — StateStore and dedup
- `references/cron-output-debugging.md` — Bot 2 stall diagnosis and recovery
- `references/ollama-port-silent-failure.md` — Wrong default port detection
- `references/stacked-pr-rebuild.md` — Squash-merge rebuild recipe
- `references/context-bundle-design.md` — Deterministic context bundle
- `references/two-tier-response.md` — Tier 1 + Tier 2 comment architecture
