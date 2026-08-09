# PR Scope Reduction via Rebase

<!-- Trigger: PR has unrelated files (opportunistic docs, leftover scaffolding) -->

## Interactive Rebase

```bash
# Start interactive rebase
git rebase -i origin/main

# Drop commits with unrelated files
# Edit commits to remove unrelated changes
```

## Surgical File Removal

```bash
# Remove file from all commits in branch
git filter-branch --force --index-filter \
  'git rm --cached --ignore-unmatch path/to/file' \
  --prune-empty -- --all

# Or for a single commit
git rm --cached path/to/file
git commit --amend
```

## Split Mixed PR

When a PR bundles multiple unrelated features:

1. Create fresh branch from current main
2. Copy only the feature files from the PR branch
3. For dependent files, patch manually
4. Verify: `python -m py_compile` + `python -m pytest`
5. Commit, push, open PR

**Real case:** PR #55 was branched from old main. It deleted files that main now has. Solution: Created new branch from current main, copied only `proofshotter.py`, patched `orchestrator.py`, opened PR with zero file deletions.
