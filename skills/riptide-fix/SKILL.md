---
name: riptide-fix
description: |
  Use when a spawned Hermes session is executing an @riptide-bot fix command.
  Covers the full autonomous fix lifecycle: parse review findings, verify each
  against current code at the PR HEAD (never trust stale line numbers), edit
  only files in the PR's diff, run tests before pushing, push via gh-authenticated
  git when eligible, and report per-finding verdicts. Enforces hard safety gates:
  no pushes to forks/foreign repos, no force-push, no secret-file edits, no push
  on red tests.
---

# Riptide Fix

Autonomous fix executor for `@riptide-bot fix [description]` PR commands. You were
spawned by `riptide/fixer.py` with a mission prompt containing the PR coordinates,
push eligibility, and scope. Follow it — this skill is the canonical behavior
reference for that mission.

## Lifecycle (sequential — one subagent at a time, never parallel)

1. **Pre-flight** — `sys.path.insert(0, '/home/sc/workspace')` before any
   `from riptide...` import (spawned sessions have no PYTHONPATH). Clone the
   repo at the PR HEAD with `gh repo clone`, checkout the exact head SHA from
   the mission prompt. Never work on a stale checkout.
2. **Graphify first-pass** — before editing anything, run
   `graphify query "<what does X touch>" --graph graphify-out/<today>/graph.json`
   and `graphify path <A> <B>` for callers of the code you will change. Ground
   the blast radius in the actual graph, not guesses.
3. **Verification gate (before any edit)** — parse the latest `@riptide-bot`
   review comment's `## 🔍 Findings` plus inline threads
   (`gh api repos/{owner}/{repo}/pulls/{N}/comments`). For each finding, fetch
   the file at the PR HEAD and match by **code context, never line numbers**.
   Verdict per finding:
   - `valid` — still present, proceed to implementation.
   - `skip-already-addressed` — code already fixed; skip with one-line reason.
   - `skip-stale-false-positive` — finding doesn't match current code; skip.
4. **Implementation** — minimal, targeted edits for `valid` findings only.
   ONLY touch files in the PR's diff (scope isolation).
5. **Validate** — run the repo's test suite and `python -m py_compile` on every
   changed `.py` file. Iterate until green. **No push on red tests.**
6. **Push (only if the mission says push-eligible)** — `git add` only the files
   you edited (never `git add -A`), Conventional Commit (`fix(scope): ...`),
   `git push origin HEAD:<pr-branch>`. gh's credential helper authenticates as
   ChonSong. **Never force-push. Never rewrite pushed history.**
7. **Summary comment (always)** — post a PR comment with per-finding verdict +
   reason, files touched, test results, commit SHA (or the full `git diff`
   patch if push was not authorized, with a "cannot push to fork/foreign repo"
   note). End with the model attribution footer:
   `<sub>🤖 Riptide Fix via Hermes · model: <model_name></sub>`

## Hard constraints (violating any of these is a mission failure)

- **You (fixer) are the ONLY riptide entity permitted to push.** The companion / deepthink / proofshotter crons are comment-only — they never push. If a spawned session is one of those crons, it must NOT attempt `git push` even if it has repo write access; comment-only patch is the path.
- NEVER edit `github-private-key.pem`, `.env`, or any credential/secret file.
- NO force-push, NO history rewrites, NO `git add -A`.
- NO push when tests are red.
- NO push to fork PRs or foreign repos — comment-only patch instead, never silent.
- If scope is a user description (`fix <text>`), fix ONLY findings matching that
  text; do not roam.

## Test suite (riptide repo)

The gated suite is `python scripts/check_test_baseline.py` — it runs `riptide/tests`
and compares the failing node set against `riptide/tests/baseline_failures.txt`.
Exit 0 is green and it is the only run CI judges (`.github/workflows/pytest.yml`).
The repo-root `tests/` dir is NOT gated and is red on `main` (24 sqlite schema
failures, identical on the PR head) — never use it as the pre-push gate, and never
report its failures as a regression the fix introduced.

Interpreter: the session `python3` is the Hermes venv (has pytest). A command
promoted to a background process gets a different PATH (uv python, no pytest), so
run the suite in the foreground or call the venv interpreter by absolute path.

## Pitfalls

- **Stale line numbers**: review findings reference the diff at review time;
  the PR may have moved. Always re-locate by context.
- **Reviewer false positives are common**: assume every finding is wrong until
  verified against current code. Fixing a non-issue is worse than skipping it.
- **Graphify path**: graphs live in dated subdirs —
  `graphify-out/YYYY-MM-DD/graph.json`. No root graph.json exists.
- **gh push auth**: works because `gh auth setup-git` configured the git
  credential helper for github.com as ChonSong. If push fails with 403, the
  session is not push-eligible — fall back to the comment-only patch path.
