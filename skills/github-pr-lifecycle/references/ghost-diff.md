# Ghost Diff — Stale Fork Master

<!-- Trigger: PR opened from fork where fork's master is behind upstream master -->

## Detection

`gh pr diff` compares PR head against fork's stale base — showing ALL upstream commits merged into fork since branch point as part of PR diff.

**Symptom:** PR shows 100+ files but title describes small change.

```bash
# Real changes (only PR's own commits)
git fetch origin master <pr-branch>
git diff origin/master..origin/<pr-branch> --stat
```

## Detection Protocol

```bash
# How far is fork master behind upstream?
git fetch upstream master && git log --oneline --left-right origin/master...upstream/master

# Real diff = merge-base to PR head
git merge-base origin/master origin/<pr-branch>
git diff <merge-base>..origin/<pr-branch> --stat
```

**Example:** PR #2 on `ChonSong/hermes-webui` showed 124 files / 16,620 additions on GitHub. `git diff origin/master..origin/fix/rg-session-search --stat` revealed **15 files, 54 insertions, 1888 deletions**.
