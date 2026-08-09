# Generated Artifact PRs

<!-- Trigger: PR has 4000+ LOC but real code change is tiny -->

## Detection

```bash
# Split "real code" vs "generated" by path pattern
gh pr diff $N --repo O/R --name-only | sort > /tmp/pr-files.txt

# Prove tracked file was force-added past .gitignore
git check-ignore -v graphify-out/cache/ast/v0.9.26/<hash>.json

# Confirm artifacts NOT on base branch
git ls-tree origin/master -- graphify-out/cache/ | head
```

## What to Say in Review

1. "X of Y added LOC are regenerable artifacts; real change is ~N lines."
2. Flag force-added gitignored files — remove with `git rm -r --cached <dir>`.
3. If committed snapshot contradicts PR's own premise, note it's immediately stale by design.

## Pattern

Riptide PRs with 4000+ LOC are generated-artifact candidates first. PRs #19 (4,361 of 4,467 LOC artifacts) and #20 (4,476 of 4,490 LOC artifacts) had real changes under 120 LOC.
