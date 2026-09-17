---
name: riptide-pr-webhook
description: Riptide webhook spawns Hermes sessions for Need Action PRs.
---

# Riptide PR Webhook

Riptide is a FastAPI webhook server that receives GitHub App webhooks and spawns autonomous Hermes sessions to address PR review feedback.

## Architecture

```
GitHub App (riptide-review, ID 4262983)
  → Webhook: https://riptide.codeovertcp.com/webhook/github
  → Cloudflare Tunnel (codeovertcp) → localhost:8477
  → Riptide server (FastAPI) →
      - PR opened/reopened/sync → code review (via Ollama)
      - PR merged → incremental index
      - @mention → code review
      - pull_request_review + "Need Action" label → Hermes cron session
```

## What was fixed

The Riptide server was partially built but the server was down and no "Need Action" session spawner existed.

### Changes made

1. **Added `handle_pull_request_review` handler** in `webhook.py` — detects reviews with `state: changes_requested` or `state: commented` where the PR has a "Need Action" label (case-insensitive, supports variants like "needs action", "need-action", "action required")

2. **Added `_spawn_hermes_session` helper** — calls `hermes cron create` with a timestamp 2 minutes in the future, loads the `github-pr-lifecycle` skill

3. **Set up systemd service** — auto-starts on boot

## Management

```bash
systemctl --user status riptide.service      # status
journalctl --user -u riptide --no-pager -n 50 # logs
systemctl --user restart riptide.service      # restart
```

## One-time DNS fix needed

The Cloudflare API token lacks DNS permissions, so the tunnel CNAME was never created. Go to Cloudflare dashboard → DNS → codeovertcp.com → add:

- Type: **CNAME**
- Name: `riptide`
- Target: `ddaeb2d9-cb6c-4a25-8525-1f1454a80a4b.cfargotunnel.com`
- Proxy: Proxied (orange cloud)

Then verify: `curl https://riptide.codeovertcp.com/health`

## Files

| Path | Purpose |
|------|---------|
| `~/workspace/riptide/server.py` | Entry point |
| `~/workspace/riptide/riptide/webhook.py` | Webhook handlers (modified) |
| `~/workspace/riptide/riptide/github_app.py` | JWT auth + GitHub API client |
| `~/workspace/riptide/riptide/review_worker.py` | Review pipeline |
| `~/workspace/riptide/.env` | GitHub App credentials |
| `~/workspace/riptide/start.sh` | Env loader script |
| `~/.config/systemd/user/riptide.service` | Systemd unit |
