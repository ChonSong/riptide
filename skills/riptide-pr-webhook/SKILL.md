---
name: riptide-pr-webhook
description: "Riptide webhook — GitHub App event routing into the companion, command handler and install sync."
---

# Riptide PR Webhook

Riptide is a FastAPI webhook server that receives GitHub App webhooks and routes
them into the companion, the on-demand command handler and the installation sync.
This skill documents the *current* routing surface — `riptide/webhook.py` is
canonical whenever the two disagree.

## Architecture

```
GitHub App (riptide-review, ID 4262983)
  → Webhook: https://riptide.codeovertcp.com/webhook/github
  → Cloudflare Tunnel (codeovertcp) → localhost:8477
  → Riptide server (FastAPI), router in riptide/webhook.py
      - pull_request (opened/reopened/synchronize) → companion TL;DR (+ labeler)
      - pull_request (closed + merged to default branch) → auto-deploy
      - issue_comment (created) → companion skip/resume + @riptide-bot commands
      - issue_comment (edited)  → checkbox toggles
      - installation / installation_repositories → sync installations + repos
      - any other event → logged, acked
```

Riptide Review (Bot 2) does **not** run from this endpoint — its cron poller
lives in `riptide/deepthink.py`. Commands reach it indirectly:
`issue_comment` → `interaction_handler.handle_command()` → `deepthink` /
`fixer` / `visual`.

Every delivery is deduplicated on `X-GitHub-Delivery` and signature-verified
before routing. Internal errors still ack with HTTP 200 so GitHub does not
retry-storm; the delivery is recorded as failed and the cron poller picks the
PR up instead.

Repos without an App installation are served through the `gh` CLI fallback when
they appear in `RIPTIDE_WATCHED_REPOS`.

## Management

```bash
systemctl --user status riptide.service        # status
journalctl --user -u riptide --no-pager -n 50  # logs
systemctl --user restart riptide.service       # restart
```

Health: `curl https://riptide.codeovertcp.com/health` → 200 when up.

## Files

| Path | Purpose |
|------|---------|
| `~/workspace/riptide/server.py` | Entry point |
| `~/workspace/riptide/riptide/webhook.py` | Event routing (`/webhook/github`) |
| `~/workspace/riptide/riptide/interaction_handler.py` | `@riptide-bot` command routing |
| `~/workspace/riptide/riptide/checkbox_handler.py` | Checkbox-toggle routing |
| `~/workspace/riptide/riptide/companion.py` | PR TL;DR companion |
| `~/workspace/riptide/riptide/deepthink.py` | Bot 2 review (cron-driven) |
| `~/workspace/riptide/riptide/github_app.py` | JWT auth + GitHub API client |
| `~/workspace/riptide/.env` | GitHub App credentials |
| `~/.config/systemd/user/riptide.service` | Systemd unit |

## Superseded — deleted in `92bd5bd` (do not implement)

An earlier revision of this skill described a `pull_request_review` flow: a
`handle_pull_request_review` handler that caught `changes_requested` /
`commented` reviews on PRs carrying a "Need Action" label, plus a
`_spawn_hermes_session` helper that shelled out to `hermes cron create`.

Commit `92bd5bd` ("refactor(riptide): trim to two-bot architecture") deleted
both. `webhook.py` has no `pull_request_review` branch and nothing in `riptide/`
reads a "Need Action" label; ad-hoc reviews are requested with
`@riptide-bot review` on `issue_comment`. The same revision's "One-time DNS fix
needed" is done as well — the tunnel CNAME exists and `/health` answers 200.
