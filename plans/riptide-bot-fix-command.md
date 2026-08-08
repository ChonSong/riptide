# @riptide-bot fix — Design

**Status:** Implemented (PR #33) · **Date:** 2026-08-03

## What

`@riptide-bot fix [description]` — spawns a Hermes session that edits, commits, and pushes to a PR's branch. Triggered via `@riptide-bot fix` comment on a PR.

## Security gates

| Gate | Check | Failure mode |
|------|-------|--------------|
| Commenter auth | `commenter == OUR_USERNAME or commenter == author or commenter == owner` | Return "Not authorized" |
| Push eligibility | `owner == OUR_ORG or pr_author == OUR_USERNAME` | Comment-only patch |
| Fork detection | `head_repo == owner/repo` (fail-closed if missing) | Comment-only patch |
| Scope isolation | Only touch files in PR diff | Prompt-enforced |
| No secrets | Never edit `.env`, `*-key.pem` | Prompt-enforced |
| No force-push | No `--force`, no history rewrite | Prompt-enforced |
| Tests before push | `pytest riptide/tests/` | No push on red |

## Architecture

```
Webhook (issue_comment with @riptide-bot fix)
  → webhook.py Route 2b
    → fixer.handle_fix_command() — auth gate, eligibility check
      → hermes cron create (one-shot, --repeat 1)
        → Hermes session: verify findings → edit → test → push/post
          → PR comment with summary + model attribution
```

## Files

| File | Purpose |
|------|---------|
| `riptide/fixer.py` | `FIX_RE`, `handle_fix_command()`, `_is_push_eligible()`, `_spawn_fix()`, `_build_fix_prompt()` |
| `riptide/webhook.py:317-335` | Route 2b wiring |
| `riptide/tests/test_fixer.py` | 19 tests: regex, auth, eligibility, fork detection, prompt |

## Config

```bash
RIPTIDE_OUR_USERNAME=ChonSong     # authorization identity
RIPTIDE_OUR_ORG=ChonSong          # push eligibility
RIPTIDE_FIX_MODEL=custom:LongCat-2.0  # pin model on spawned jobs
RIPTIDE_FIX_PROVIDER=custom
```

## Notes

- Fork pre-flight uses `git fetch origin pull/{N}/head:pr-{N}` (not `{head_ref}` which fails for forks)
- `_is_cron_available` uses `shutil.which` (not subprocess `which`)
