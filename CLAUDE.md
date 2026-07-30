# Riptide

Self-hosted GitHub App providing AI-powered code review via two bots.

- **Bot 1 — Companion**: Posts TL;DR summaries on PR events with graphify-informed codebase analysis.
- **Bot 2 — Riptide Review**: Deep-think autonomous code review for large, settled PRs.
- **Bot 3 — Proofshotter** (planned): Visual verification screenshots for UI PRs.

## Local test

```
source .env 2>/dev/null
python3 -m pytest riptide/tests/ -v
curl -s http://localhost:8477/health
```

## Workability

- Conditional loading: companion and graphify are optional — services degrade gracefully when their dependencies are missing.
- Python 3.12+ / modern FastAPI.
