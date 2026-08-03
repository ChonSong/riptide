# Handoff

## Agent Readiness

- **Local test**: `source .env && python3 -m pytest riptide/tests/ -v` (expected: all pass)
- **CI**: GitHub Actions checks (AgentLint, CI pipeline) must pass
- **Score thresholds**: AgentLint ≥ 60/100

## Common Failure Modes

- **Graphify freshness**: if `graphify` binary is missing, companion analysis skips graph context — not a hard failure, but TL;DR quality degrades.
- **Proofshotter (Bot 3)**: depends on Playwright being installed on the target system and the proofshot CLI at `RIPTIDE_PROOFSHOT_CLI`. When either is missing, Bot 3 gracefully skips visual verification and continues.
- **Companion GIF**: without Giphy/Tenor API keys, falls back to curated static GIFs per mood. Not a failure, just less dynamic.
- **Deepthink (Bot 2)**: requires authenticated `gh` CLI and Hermes cron setup. Bot stays silent if prerequisites aren't met.

## Test Coverage

```bash
riptide/tests/
├── test_agentlint_config.py
├── test_bot_autonomy.py
├── test_companion.py           # 20+ parametrized mood classification tests
├── test_deepthink.py
├── test_gif_selection.py       # GIF keyword scoring + fallback logic
├── test_github_app.py
├── test_health.py
├── test_orchestrator.py
├── test_webhook_endpoint.py
└── test_webhook_signature.py
```
