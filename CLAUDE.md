# Riptide

Self-hosted GitHub App providing AI-powered code review via three bots.

- **Bot 1 — Companion**: Posts TL;DR summaries on PR events with graphify-informed blast radius and contextual GIF reactions.
- **Bot 2 — Riptide Review**: Deep-think autonomous code review for large, settled PRs via Hermes cron sessions.
- **Bot 3 — Proofshotter**: Visual verification screenshots for UI PRs via Playwright captures.

## Local test

```bash
source .env 2>/dev/null
python3 -m pytest riptide/tests/ -v
curl -s http://localhost:8477/health
```

## Workability

- Conditional loading: companion, deepthink, and proofshotter are optional — services degrade gracefully when their dependencies are missing.
- Python 3.12+ / modern FastAPI.
