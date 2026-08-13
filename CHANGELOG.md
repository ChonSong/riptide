# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added
- AgentLint AGENTS.md compliance checks on PRs.
- Health endpoint, file logging, gunicorn production mode.
- Graphify data freshness refresh before companion analysis.
- Proofshot poll cron job for Bot 3.
- `@riptide-bot fix` autonomous fix command (edit/commit/push).

### Fixed
- Thread crash safety in companion PR handler.
- Docker compose standalone network creation.
- Personal path leak in graphify-out cache artifacts.
