# Handoff

## Agent Readiness

- **Local test**: `source .env && python3 -m pytest riptide/tests/ -v` (expected: all pass)
- **CI**: GitHub Actions checks (AgentLint, CI pipeline) must pass
- **Score thresholds**: AgentLint ≥ 60/100

## Common Failure Mode

- Graphify freshness: if `graphify` binary is missing, companion analysis skips graph context — not a hard failure, but TL;DR quality degrades.
- Proofshotter: Bot 3 depends on Playwright being installed on the target system.
