# Merge-Inflated PR Scope

<!-- Trigger: PR shows 50+ files but title describes small change -->

## Detection

```bash
# Count merge commits vs branch-author commits
git log --oneline <base>..<head> | wc -l          # total
git log --oneline <base>..<head> --merges | wc -l # merge commits
git log --oneline <base>..<head> --first-parent --no-merges  # author's actual work
```

If `(total - merge-commits) >> first-parent commits`, most changes are from OTHER PRs merged to master.

## Find Author's Actual Files

```bash
# Iterate first-parent non-merge commits, collect unique files
for sha in $(git log --oneline <base>..<head> --first-parent --no-merges --format="%h"); do
  git diff --name-only $sha^..$sha 2>/dev/null
done | sort -u
```

## Merge Carry Detection

Files that entered through merge from base branch that has since cleaned them up:

```bash
git log --oneline --all -- <path>
# If main has "remove X accidentally carried from merge base" → merge carry
```

## Inflation Ratio

```
Inflation factor = GitHub displayed files / author-touched files
```

96% reduction example: GitHub showed 211 files, author touched 8.
