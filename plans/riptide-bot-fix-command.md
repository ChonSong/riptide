# Plan: `@riptide-bot fix` — Autonomous Fix Command

**Status:** Proposal · **Branch:** new (`feat/fix-command`) · **Depends on:** PR #31 (merged, `730e824`)
**Date:** 2026-08-02 · **Owner:** ChonSong

## 1. What this is

`@riptide-bot fix` is an **autonomous coding agent command** — a new capability, separate from
`@riptide-bot review` (analysis only). When a user comments `@riptide-bot fix` on a PR, Riptide
spawns a Hermes session that can **edit files, commit, and push** fixes to that PR's branch.

Prior scoping (session f329280cb480): deferred to "a separate PR on a new branch — new
capability, needs its own design discussion." This plan is that design discussion.

## 2. Current state (verified 2026-08-02)

| Command | Regex | Location | Route |
|---------|-------|----------|-------|
| `companion skip/resume` | `@riptide-bot\s+companion\s+(skip\|resume)` | `companion.py:49` `SKIP_RE` | webhook.py Route 1 (`:282`) |
| `review`/`deepthink`/`full review` | `@riptide-bot\s+(review\|deepthink\|full\s*review)` | `deepthink.py:38` `REVIEW_RE` | webhook.py Route 2 (`:298`) |

**No `fix` command exists anywhere** (code, docs, README, SKILL.md, wiki — verified by grep).

Webhook routing (the insertion point), `webhook.py:297-317`:
```python
# Route 2: On-demand review command
if installation_id and body and "@riptide-bot" in body.lower():
    from riptide.deepthink import REVIEW_RE, handle_review_command
    if REVIEW_RE.search(body):
        result = handle_review_command(client, installation_id, owner, repo_name, pr_number, commenter)
        if result:
            client.post_pr_comment(installation_id, owner, repo_name, pr_number, result)
```

Spawn pattern (mirror this), `deepthink.py:146+` `_spawn_deepthink()`:
- `hermes cron create <run_at> <prompt> --name riptide-review-{owner}-{repo}-{pr} --skill github-pr-lifecycle --skill deep-think --skill excalidraw --deliver origin`
- 3 retries with 5s/15s/30s backoff; StateStore `reserve_job()` (idempotent `INSERT OR IGNORE`) → `mark_failed`/`mark_complete`
- run_at = now + 2 min; riptide profile (LongCat-2.0 pin via profile, **not** `--model` — CLI doesn't support it)
- **PYTHONPATH pitfall:** spawned sessions have no PYTHONPATH; prompt must `sys.path.insert(0, '/home/sc/workspace')` before `from riptide.grafiphy...`

## 3. Tooling decision

| Tool | Decision | Rationale |
|------|----------|-----------|
| **Graphify** | ✅ Use | Mandated by AGENTS.md for codebase questions; used as first-pass here (query on webhook routing confirmed hub structure). The fix session must run `graphify query/path/explain` on the PR's diff before editing to avoid breaking callers (blast-radius discipline). Graphs live in dated subdirs: `graphify-out/<YYYY-MM-DD>/graph.json`. |
| **Subagents** | ✅ Use, **sequential** | PR #31 already moved to sequential execution (commit `4565be8 fix: change subagent execution from parallel to sequential`) and user preference is one-at-a-time. Fix flow: (1) analysis/plan subagent → (2) implementation subagent → (3) verification subagent. Never parallel. |
| **Deepthink** | ✅ Use | The spawned fix session runs the deep-think loop (SURFACE→EXPLORE→CHALLENGE→SYNTHESIZE→VALIDATE) — same as review, plus a new VALIDATE-gate that runs tests before pushing. |

## 4. Design

### 4.1 Command surface

```
@riptide-bot fix                # Fix the PR's outstanding review findings
@riptide-bot fix <description>  # Fix a specific described problem
```

Regex: `FIX_RE = re.compile(r"@riptide-bot\s+fix\b(.*)", re.IGNORECASE | re.DOTALL)` — captures
optional description. Placed in a **new module `riptide/fixer.py`** (Bot 2 family), NOT in
`companion.py` (Bot 1 owns TL;DR only) and not overloaded onto `REVIEW_RE`.

### 4.2 Routing — webhook.py Route 2 (after review check)

```python
# Route 2b: On-demand fix command (@riptide-bot fix)
from riptide.fixer import FIX_RE, handle_fix_command
if FIX_RE.search(body):
    result = handle_fix_command(client, installation_id, owner, repo_name, pr_number, commenter, description)
    if result:
        client.post_pr_comment(installation_id, owner, repo_name, pr_number, result)
```

### 4.3 New module: `riptide/fixer.py`

Mirrors `deepthink.py` structure:

1. **`handle_fix_command(...)`** — fetch PR details; verify push eligibility; return confirmation string (same shape as `handle_review_command`, `deepthink.py:90-135`).
2. **`_spawn_fix(...)`** — `hermes cron create` with name `riptide-fix-{owner}-{repo}-{pr}`, run_at +2min, skills: `deep-think`, `github-pr-lifecycle`, new `riptide-fix` skill; 3 retries; StateStore `reserve_job()` / `mark_failed` / `mark_complete` (reuse existing StateStore — it's already owner/repo/pr scoped via job_id prefix `riptide-fix-...`).
3. **`_is_push_eligible(owner, repo, pr)`** — safety gate: allow when `owner == OUR_ORG` (we own repo) OR `pr_author == OUR_USERNAME` (we authored). Never push to foreign repos. Same ownership logic as Bot 2 filters (`deepthink.py:40+`).

### 4.4 Spawned fix session prompt (self-aware, grounded)

```
## Mission
Apply the fix for PR #{N} in {owner}/{repo} ({title}).
Description: {description or "resolve the review findings"}

## Pre-flight (mandatory)
1. sys.path.insert(0, '/home/sc/workspace')   # PYTHONPATH pitfall
2. cd /home/sc/workspace/{repo} && git fetch origin pull/{N}/head:pr-{N} && git checkout pr-{N}
3. gh pr view {N} --json files,additions,deletions,headRefOid  (current HEAD)
4. Run graphify query on the changed files: graphify query "<what does X touch>" --graph graphify-out/<today>/graph.json
5. graphify path <fileA> <fileB> for callers of anything you will change (blast radius)

## Deep-think loop
SURFACE → EXPLORE (graphify) → CHALLENGE → SYNTHESIZE → VALIDATE

## Constraints (hard)
- ONLY touch files in this PR's diff. Scope isolation (see deepthink prompt convention).
- NEVER edit github-private-key.pem, .env, or any credential/secret file.
- NO force-push, NO rewriting pushed history. Push with --force-with-lease ONLY to a NEW branch if needed.
- Run tests before pushing: python3 -m pytest riptide/tests/ (or repo's suite). No push on red tests.
- Conventional Commits (fix(scope): ...).
- Model attribution footer REQUIRED: <sub>🤖 Riptide Fix via Hermes · model: <model_name></sub>

## Execution
1. Implement the fix (minimal, targeted edits).
2. Run tests + python -m py_compile on changed files.
3. git add + commit (conventional) + git push origin pr-{N}.
4. Post summary comment: what changed, test results, files touched, commit SHA, link to diff.
```

### 4.5 New skill: `riptide-fix`

Small skill (the prompt above as canonical instructions) so spawned sessions have stable,
versioned fix behavior — same pattern as the `deep-think`/`github-pr-lifecycle` skill pairing.

### 4.6 StateStore / dedup

Reuse existing `StateStore` (orchestrator.py). Job id `riptide-fix-{owner}-{repo}-{pr}-{sha[:8]}-{tier}`.
Pending job TTL 30 min (existing). Do NOT reuse `deepthink_acted_prs.json` — separate state key so
a `fix` after a `review` isn't deduped against it. Add `fixer_acted_prs.json` if SHA-dedup is needed
(optional first cut — the StateStore reservation already prevents double-spawn).

## 5. Files to change

| File | Change |
|------|--------|
| `riptide/fixer.py` | **NEW** — FIX_RE, handle_fix_command, _spawn_fix, _is_push_eligible |
| `riptide/webhook.py` | Route 2b block in `handle_issue_comment()` (~line 317) |
| `~/.hermes/skills/riptide-fix/SKILL.md` | **NEW** — spawned-session instructions |
| `riptide/tests/test_fixer.py` | **NEW** — regex match, push-eligibility gates, spawn cmd shape, confirmation string |
| `README.md`, `SKILL.md`, AGENTS.md docs | Command table + docs |
| `CHANGELOG.md` | Entry |

## 6. Tests (mirror existing suites)

- `FIX_RE` matches `@riptide-bot fix`, `@riptide-bot fix the flaky test`, not `review`, not `companion skip`.
- `_is_push_eligible`: owned repo ✓, our authored PR in foreign repo ✓, foreign author/repo ✗.
- `_spawn_fix`: builds correct `hermes cron create` argv (positional prompt — no `--prompt` flag), retries on failure, calls `reserve_job` once.
- `handle_fix_command`: returns confirmation string with title/author/LOC/commit; returns error string when PR fetch fails.
- Webhook routing: issue_comment with `@riptide-bot fix` hits Route 2b (integration test in test_webhook_endpoint.py style).

## 7. Deploy checklist

```bash
# 1. branch + implement + tests
git checkout -b feat/fix-command
python -m py_compile riptide/fixer.py riptide/webhook.py
python3 -m pytest riptide/tests/ -v        # expect 192 + new tests green
git push origin feat/fix-command && gh pr create ...   # separate PR (per prior scoping)

# 2. after merge — production deploy (NEVER restart without cleaning pycache)
git pull
find . -name __pycache__ -type d -exec rm -rf {} +
systemctl --user restart riptide.service
sleep 4 && curl -s http://localhost:8477/health
```

## 8. Open questions for user

1. Should `fix` auto-apply to **all open review findings** or only the explicitly described problem? (Default: description wins; else resolve inline review threads)
2. Push strategy: push directly to the PR branch (requires write perm — fine for owned repos) vs. push a new branch + open a follow-up PR. (Default: direct to PR branch when eligible)
3. Should the fix session post an inline "will fix" ack immediately, or stay silent until the fix lands? (Default: instant ack comment, matching review behavior)
