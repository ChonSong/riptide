# GitHub PR Lifecycle

Branch → commit → push → create → edit → CI → merge. The `gh` way plus the `curl`/`api` fallback with documented pitfalls.

## Workflow

```bash
# Create
gh pr create --title "feat: ..." --body "## Summary\n..." [--draft]

# Edit (gh pr edit silently fails — use api)
gh api -X PATCH repos/O/R/pulls/N -f title="..." -f body="..."

# Merge
gh pr merge --squash --delete-branch [--auto] [--admin]

# CI
gh pr checks --watch
gh run view <ID> --log-failed

# Inline comment
gh api repos/O/R/pulls/N/comments \
  -f body='**🔴 Critical:** ...' \
  -f commit_id='<full-sha>' \
  -f path='file.py' \
  -F line=42 \
  -f side='RIGHT'

# Multi-line inline
gh api repos/O/R/pulls/N/comments \
  -f body='...' -f commit_id='...' -f path='...' \
  -F line=199 -F start_line=186 -f side='RIGHT' -f start_side='RIGHT'
```

## Silent-Failures (ALWAYS Check)

- **gh pr edit** — returns exit code 1 WITHOUT applying edit when GitHub emits deprecation warning. Use `gh api -X PATCH` instead.
- **gh pr diff >20k lines** — fails with "diff exceeded maximum." Use `gh api repos/O/R/pulls/N/files --paginate`.
- **GITHUB_TOKEN env var** — overrides stored `gh` auth. If stale/revoked, `unset GITHUB_TOKEN GH_TOKEN`.
- **Inline comments** — `-F line=N` (integer) not `-f line=N` (string). String "42" → 422 error.
- **Markdown bodies** — shell mangles backticks. Use `gh pr comment --body-file <file>` not `--body`.
- **gh release upload** — stores asset by file basename. Rename before upload or lookup fails.
- **gh pr diff** — no file path filtering. Use `git diff` with PR branch for per-file.

## Pre-Flight (Before Deep Analysis)

```bash
# Fork ghost diff — real changes only
git diff origin/master..origin/<branch> --stat

# Branch mismatch — local main ahead of origin
git log --oneline origin/main..main

# PR scope
git diff main..<branch> --stat
git log --oneline main..<branch>

# Stale fork master detection
git fetch upstream master && git log --oneline --left-right origin/master...upstream/master
```

## Posting Inline Comments

**Fields:**

| Field | Type | Flag |
|-------|------|------|
| body | string | `-f body='...'` |
| commit_id | string (full SHA) | `-f commit_id='...'` |
| path | string | `-f path='...'` |
| line | **integer** | `-F line=N` |
| side | `RIGHT`/`LEFT` | `-f side='RIGHT'` |

**LEFT side** = old file (deleted lines). **RIGHT side** = new file (added/modified).

**Markdown in body** — use `--input` with JSON file to avoid shell mangling:

```bash
cat > /tmp/comment.json << 'EOF'
{"body": "**🔴 Critical:** `code`\n\n```suggestion\nreplacement\n```", "commit_id": "...", "path": "...", "line": 42, "side": "RIGHT"}
EOF
gh api repos/O/R/pulls/N/comments --input /tmp/comment.json
```

**Multi-line** — add `start_line` and `start_side`.

## CI Validation Triage

```bash
# Is master also red?
gh run list --repo O/R --workflow CI --branch master --limit 6

# Fetch job logs (gh run view --log-failed may show nothing)
gh run view <ID> --json jobs --jq '.jobs[] | {id, name, conclusion}'
gh api repos/O/R/actions/jobs/<JOB_ID>/logs
```

## Merge Inflation Detection

PR shows 100+ files but title says small change:

```bash
# Real scope = merge-base to PR head
git merge-base origin/master origin/<pr-branch>
git diff <merge-base>..origin/<pr-branch> --stat

# Author-touched files (excludes merge carries)
for sha in $(git log --oneline <base>..<head> --first-parent --no-merges --format="%h"); do
  git diff --name-only $sha^..$sha 2>/dev/null
done | sort -u
```

## Dead Code Verification

When PR removes code, verify unreferenced:

```bash
grep -rn "<function_name>" path/to/repo --include="*.py" --include="*.js"
```

Zero hits → safe to remove. Hits in files NOT modified → still has live callers.

## Bot Filter Chain

For cron-polled PR bots:

```
Draft? → SHA dedup? → Staleness? → Scope match? → Already posted? → TRIGGER
```

**24h cooldown is redundant** — SHA dedup already prevents re-processing. Remove it.

## Subagent 3-Bullet Rule

1. **Input:** file path, diff, JSON
2. **Task:** exactly one action
3. **Output:** path, format, pass/fail

NEVER: background context, "explore the architecture," multi-step exploration.

## References (Load on Demand)

- `references/merge-inflation.md` — PR shows 50+ files but title describes small change
- `references/ghost-diff.md` — fork master behind upstream, 100+ file ghost diff
- `references/inline-comments.md` — posting inline review comments with markdown
- `references/excalidraw.md` — generating and uploading architecture diagrams
- `references/bot-patterns.md` — building cron-polled PR bots
- `references/dead-code.md` — verifying removed code is unreferenced
- `references/ci-triage.md` — CI failures vs pre-existing red on master
- `references/cron-constraints.md` — execute_code blocked, heredoc blocked, rm -rf blocked
- `references/generated-artifacts.md` — PRs with 4000+ LOC from graphify-out/
- `references/patch-reformat.md` — patch tool reformatting whole file on small edit
- `references/scope-reduction.md` — surgical removal via interactive rebase
