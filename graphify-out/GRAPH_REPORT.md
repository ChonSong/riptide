# Graph Report - riptide  (2026-08-02)

## Corpus Check
- 40 files · ~33,239 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 962 nodes · 1342 edges · 78 communities (73 shown, 5 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 36 edges (avg confidence: 0.56)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `32a1416a`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- GitHubAppClient
- excalidraw_renderer.py
- webhook.py
- Riptide — Self-Hosted GitHub App (Three Bots)
- _spawn_deepthink
- Companion
- make_companion
- Riptide — Two-Bot GitHub App
- start.sh
- Grafiphy — ELI5 Pseudocode Diagram Generation
- generate_labels
- riptide/orchestrator.py
- select_gif
- conftest.py
- labels
- label-definitions.json
- T0Orchestrator
- AGENTS.md
- TaskProfile
- labels
- classify_pr_mood
- StateStore
- TaskClassifier
- Problem: Spawned Hermes Sessions Can't Report Their Runtime Model
- labels
- labels
- TestResultValidator
- dimensions
- repos
- bot
- TestWebhookEndpoint
- paths
- TestAgentlintConfig
- paths
- paths
- paths
- paths
- ._dispatch_t1
- comp/auth
- comp/sessions
- ChonSong/hermes-webui
- comp/workspace
- comp/runner
- comp/reporter
- comp/proofshot
- comp/github-app
- ._parallel_review
- comp/extensions
- comp/diagram
- ChonSong/riptide
- comp/labeler
- NousResearch/hermes-agent
- [Unreleased]
- comp/companion
- comp/acp
- comp/cron
- comp/agent
- comp/plugins
- comp/tui
- Riptide
- Handoff
- Security Policy
- priority
- status/draft
- status/stale
- pre-commit
- type/docs
- type/perf
- type/refactor
- type/security
- type/test
- type/perf
- type/refactor
- type/security
- type/test
- pre-commit

## God Nodes (most connected - your core abstractions)
1. `Companion` - 35 edges
2. `StateStore` - 26 edges
3. `T0Orchestrator` - 25 edges
4. `_spawn_deepthink()` - 24 edges
5. `TestStateStore` - 23 edges
6. `TaskProfile` - 22 edges
7. `make_companion()` - 21 edges
8. `select_gif()` - 19 edges
9. `GitHubAppClient` - 19 edges
10. `classify_pr_mood()` - 16 edges

## Surprising Connections (you probably didn't know these)
- `TestSpawnRetry` --uses--> `Companion`  [INFERRED]
  riptide/tests/test_bot_autonomy.py → riptide/companion.py
- `TestClassifyPrMood` --uses--> `Companion`  [INFERRED]
  riptide/tests/test_companion.py → riptide/companion.py
- `TestFormatComment` --uses--> `Companion`  [INFERRED]
  riptide/tests/test_companion.py → riptide/companion.py
- `TestGenerateTldr` --uses--> `Companion`  [INFERRED]
  riptide/tests/test_companion.py → riptide/companion.py
- `TestOllamaCall` --uses--> `Companion`  [INFERRED]
  riptide/tests/test_companion.py → riptide/companion.py

## Import Cycles
- None detected.

## Communities (78 total, 5 thin omitted)

### Community 0 - "GitHubAppClient"
Cohesion: 0.07
Nodes (23): base64url_encode(), GitHubAppClient, InstallationTokenCache, jwt_token(), Authenticated GitHub API client using GitHub App installation tokens.     All me, Compare two commits and return files changed + commit metadata.          Uses Gi, Fetch all files changed in a PR., Post a PR-level comment (not inline). Uses issues endpoint for top-level comment (+15 more)

### Community 1 - "excalidraw_renderer.py"
Cohesion: 0.09
Nodes (33): _chunk_text(), _compute_text_h(), _find_elem(), make_arrow(), make_rect(), make_routed_arrow(), make_text(), make_zone() (+25 more)

### Community 2 - "webhook.py"
Cohesion: 0.06
Nodes (32): Request, Response, handle_review_command(), Handle @riptide-bot review command — spawn an on-demand deep-think review., Verify GitHub webhook X-Hub-Signature-256 header.     secret = WEBHOOK_SECRET en, verify_webhook_signature(), Basic Riptide test suite., Health endpoint returns expected fields. (+24 more)

### Community 3 - "Riptide — Self-Hosted GitHub App (Three Bots)"
Cohesion: 0.07
Nodes (28): API Reference, Architecture, Bot 1: Companion (Webhook-Triggered), Bot 2: Riptide Review (Cron-Triggered), Bot 3: Proofshotter (Cron-Triggered), Commands, Companion Configuration, Comparison with Alternatives (+20 more)

### Community 4 - "_spawn_deepthink"
Cohesion: 0.21
Nodes (8): _load_state(), Load processed PR state: {owner/repo#number: {head_sha, reviewed_at}}, Check if this PR was reviewed in the last 24 hours., _save_state(), _was_reviewed_today(), TestDedupLogic, TestStateSaveLoad, TestWasReviewedToday

### Community 5 - "Companion"
Cohesion: 0.09
Nodes (11): Companion, Load companion data file (structured per-PR dict)., Normalize legacy boolean skip values to structured dicts., Get the last commented commit SHA for a PR, or None if first time., Record the commit SHA this PR was last commented on., Read deepthink state and return a Bot 2 status line for the comment footer., Build the Markdown comment body using the Phase 4 TLDR spec.         Includes op, Verify _get_bot2_status reads deepthink state and formats footer. (+3 more)

### Community 6 - "make_companion"
Cohesion: 0.08
Nodes (14): classify_pr_mood(), Classify PR mood based on title keywords and changed file patterns.          Ana, make_companion(), Tests for Companion._generate_tldr with mocked Ollama., Tests for Companion._ollama_call method., Create a Companion instance with mocked github client and disabled warm-up., Tests for Companion._format_comment method., Tests for the module-level classify_pr_mood function. (+6 more)

### Community 7 - "Riptide — Two-Bot GitHub App"
Cohesion: 0.18
Nodes (10): 1. Configure environment, 2. Start the server, 3. Set up the cron for Bot 2, Architecture, Bot 1: Companion (Webhook-Triggered), Bot 2: Riptide Review (Cron-Triggered), Files, Key Differences from Octopus (+2 more)

### Community 9 - "Grafiphy — ELI5 Pseudocode Diagram Generation"
Cohesion: 0.22
Nodes (8): Architecture, ELI5 Pseudocode in Nodes, Excalidraw JSON Structure, Files, Grafiphy — ELI5 Pseudocode Diagram Generation, Integration, Label Format (Strict), Server Status

### Community 10 - "generate_labels"
Cohesion: 0.38
Nodes (6): _generate_edge_label(), generate_labels(), _generate_node_label(), Generate a label for an edge based on relationship type., Generate labels using templates based on graphify data structure.          Args:, Generate a label for a node based on its properties.

### Community 12 - "riptide/orchestrator.py"
Cohesion: 0.09
Nodes (19): _pick_best_tag(), companion.py — GitHub Companion PR Agent for Riptide.  Pipeline:   1. Fetch PR d, Pick the most relevant search tag for a PR based on title + file analysis., Select a GIF URL based on PR mood and content relevance.          Priority:, Map a specific tag to a deterministic static GIF URL.     Each unique tag gets i, Search Giphy for a GIF matching the tag. Returns MP4 or GIF URL., Search Tenor for a GIF matching the tag. Returns GIF URL., _search_giphy() (+11 more)

### Community 14 - "select_gif"
Cohesion: 0.05
Nodes (32): client(), invalid_signature(), mock_env(), mock_github_app(), mock_hermes_cron(), mock_hermes_cron_failure(), mock_ollama(), mock_ollama_failure() (+24 more)

### Community 15 - "conftest.py"
Cohesion: 0.09
Nodes (16): CodeChunk, A code block from the PR diff with context., All data needed for a PR review — gathered by T0, consumed by template + deepthi, Classify and review a PR.                  Args:             profile: classified, Classified PR review task., Dispatch tier-by-tier, verify before escalating (verification)., T2: Quick TL;DR via companion (cheap, always runs)., Multi-file analysis needed (deepthink). (+8 more)

### Community 16 - "labels"
Cohesion: 0.07
Nodes (26): Issue classification (no files to analyze), Non-conventional-commit PR title (no type: prefix), Priority escalation detected from conversation (cron sweep re-evaluates), ai_fallback_config, confidence_threshold, description, endpoint, model (+18 more)

### Community 17 - "label-definitions.json"
Cohesion: 0.16
Nodes (21): Path, Dispatch to T3 (proofshot visual capture)., _check_proofshot_config(), _checkout_pr(), _load_state(), _post_proofshot_comment(), Clone and checkout the PR branch into /tmp/proofshot-pr-{owner}-{repo}-N/., Run the proofshot visual verification workflow.      If captures are defined in (+13 more)

### Community 18 - "T0Orchestrator"
Cohesion: 0.09
Nodes (8): Test SQLite state store for job tracking and dedup., Duplicate job_id must not crash — second call is a no-op., Underscores in owner/repo names must be escaped in LIKE query., PR #42 must not match PR #420 (hyphen delimiter prevents prefix collision)., Concurrent reserve_job calls must create only one reservation., Pending jobs older than 2h are ignored (TTL)., Stale pending jobs are marked failed after cleanup., TestStateStore

### Community 19 - "AGENTS.md"
Cohesion: 0.14
Nodes (18): Dispatch to T1 (deepthink via Hermes cron)., CodeChunk, collect_code_chunks(), collect_repo_tree(), collect_review_context(), format_graph_context(), Review pipeline: data collection, template rendering, and post-generation valida, Collect all data needed for a review.          This is called by T0 BEFORE dispa (+10 more)

### Community 20 - "TaskProfile"
Cohesion: 0.11
Nodes (19): status/blocked, status/draft, status/needs-repro, status/needs-triage, status/reviewed, status/stale, color, description (+11 more)

### Community 21 - "labels"
Cohesion: 0.13
Nodes (8): SQLite-backed state for tracking parallel job completion and dedup.          Use, Try to reserve a delivery ID. Returns False if already processed., Escape SQLite LIKE wildcards (% and _) in a prefix string., Check if any pending job matches this owner/repo/pr prefix.          Uses LIKE p, Atomically check for existing pending job and reserve a new pending row., Mark stale pending jobs as failed (e.g., crashed Hermes sessions).          This, Get the latest job status for a PR (for cross-session awareness)., StateStore

### Community 22 - "classify_pr_mood"
Cohesion: 0.19
Nodes (9): Top-level dispatcher. Classifies PR and dispatches to review tiers.          Con, T0Orchestrator, Test T0 orchestrator with both modes., Small PR with no UI should not dispatch any tier., Large PR should dispatch T1., UI PR should dispatch T3 visual., Small PR in serial mode should use T2 and stop., Large PR in serial mode should escalate past T2. (+1 more)

### Community 23 - "StateStore"
Cohesion: 0.12
Nodes (14): Before submitting a change, Bot 1: Companion State Reporting, Bot 1: Companion (Webhook-Triggered), Bot 2: Riptide Review (Cron-Triggered), Bot 3: Proofshotter (Cron-Triggered), Commits and PRs, Conventions, Dependencies (+6 more)

### Community 24 - "TaskClassifier"
Cohesion: 0.12
Nodes (16): priority, priority/critical, priority/high, priority/low, priority/medium, color, color, description (+8 more)

### Community 25 - "Problem: Spawned Hermes Sessions Can't Report Their Runtime Model"
Cohesion: 0.12
Nodes (16): scope/large, scope/massive, scope/medium, scope/small, scope/tiny, labels, color, description (+8 more)

### Community 26 - "labels"
Cohesion: 0.15
Nodes (7): If a review is already pending, don't spawn another., Verify exponential backoff and state-only-on-success behavior., After 2 failures, 3rd attempt succeeds — no timeout wait., All 3 attempts fail — returns False, no exception., _is_cron_available False -> skips that attempt entirely., TimeoutExpired on attempts 1-2, success on 3., TestSpawnRetry

### Community 27 - "labels"
Cohesion: 0.16
Nodes (4): Classify PR tasks for tier dispatch., TaskClassifier, Test task classification for tier dispatch., TestTaskClassifier

### Community 28 - "TestResultValidator"
Cohesion: 0.15
Nodes (12): Acceptance Criteria, Affected Code Paths, Context Map (via graphify), Lead A: Hermes-Side Environment Variables, Lead B: Runtime Detection in Prompt Instructions, Lead C: Spawner-Side Model Detection, Lead D: Hybrid — Env Var + Prompt Fallback, Problem: Spawned Hermes Sessions Can't Report Their Runtime Model (+4 more)

### Community 29 - "dimensions"
Cohesion: 0.26
Nodes (7): _is_cron_available(), Check that `hermes cron create` works., Spawn deep-think with a custom prompt (from review_pipeline).          Uses the, Poll watched repos and spawn deep-think sessions on qualifying PRs., run(), _spawn_deepthink_with_prompt(), TestIsCronAvailable

### Community 30 - "repos"
Cohesion: 0.27
Nodes (4): Spawn a Hermes cron session for deep-think review on this PR.      Uses pre-gath, _spawn_deepthink(), Tests for _spawn_deepthink function., TestSpawnDeepthink

### Community 31 - "bot"
Cohesion: 0.18
Nodes (4): Validate subagent results before T0 uses them., ResultValidator, Test result validation logic., TestResultValidator

### Community 32 - "TestWebhookEndpoint"
Cohesion: 0.17
Nodes (12): scope, status, type, color, description, shared_labels, description, dimensions (+4 more)

### Community 33 - "paths"
Cohesion: 0.20
Nodes (10): **/config*, **/selectors*, comp/config, color, description, paths, repo_components, description (+2 more)

### Community 34 - "TestAgentlintConfig"
Cohesion: 0.20
Nodes (10): color, description, color, description, labels, color, description, bot (+2 more)

### Community 35 - "paths"
Cohesion: 0.20
Nodes (3): Test webhook endpoint routing and signature validation., Same X-GitHub-Delivery must not trigger twice., TestWebhookEndpoint

### Community 36 - "paths"
Cohesion: 0.28
Nodes (9): cli.py, **/entry*, **/main*, src/hermes_cli/**, comp/cli, color, description, paths (+1 more)

### Community 37 - "paths"
Cohesion: 0.33
Nodes (4): _build_orchestrator_prompt(), Build a small orchestrator prompt that delegates to subagents.      The prompt i, Tests for _build_orchestrator_prompt function., TestBuildOrchestratorPrompt

### Community 39 - "._dispatch_t1"
Cohesion: 0.32
Nodes (8): api/providers*, api/sse_*, src/gateway/**, comp/gateway, color, description, paths, comp/gateway

### Community 40 - "comp/auth"
Cohesion: 0.25
Nodes (8): docker-compose*, docker_init*, flake*, scripts/**, comp/deploy, color, description, paths

### Community 41 - "comp/sessions"
Cohesion: 0.25
Nodes (8): docker-compose.yml, Dockerfile, .github/**, start.sh, comp/infra, color, description, paths

### Community 42 - "ChonSong/hermes-webui"
Cohesion: 0.25
Nodes (8): static/*.html, static/*.js, static/style.css, static/ui*, comp/ui, color, description, paths

### Community 43 - "comp/workspace"
Cohesion: 0.36
Nodes (4): _gather_review_data(), Pre-gather review data in Python before spawning the Hermes session.      Return, Tests for _gather_review_data function., TestGatherReviewData

### Community 44 - "comp/runner"
Cohesion: 0.29
Nodes (7): api/auth*, api/oauth*, api/passkeys*, comp/auth, color, description, paths

### Community 45 - "comp/reporter"
Cohesion: 0.29
Nodes (7): api/gateway*, api/session*, api/streaming*, comp/sessions, color, description, paths

### Community 46 - "comp/proofshot"
Cohesion: 0.29
Nodes (7): api/**/*.py, api/routes.py, comp/api, color, description, paths, ChonSong/hermes-webui

### Community 47 - "comp/github-app"
Cohesion: 0.29
Nodes (7): api/workspace*, api/worktrees*, workspace*, comp/workspace, color, description, paths

### Community 48 - "._parallel_review"
Cohesion: 0.29
Nodes (7): **/browser*, **/execute*, **/runner*, comp/runner, color, description, paths

### Community 49 - "comp/extensions"
Cohesion: 0.29
Nodes (7): **/output*, **/reporter*, **/result*, comp/reporter, color, description, paths

### Community 50 - "comp/diagram"
Cohesion: 0.29
Nodes (7): proofshot/**, proofshot.config.json, riptide/proofshotter.py, comp/proofshot, color, description, paths

### Community 51 - "ChonSong/riptide"
Cohesion: 0.29
Nodes (7): riptide/github_app.py, riptide/webhook.py, server.py, comp/github-app, color, description, paths

### Community 53 - "NousResearch/hermes-agent"
Cohesion: 0.29
Nodes (7): type/bug, type/feature, color, description, color, description, labels

### Community 54 - "[Unreleased]"
Cohesion: 0.33
Nodes (6): api/extensions.py, static/extension*, comp/extensions, color, description, paths

### Community 55 - "comp/companion"
Cohesion: 0.33
Nodes (6): **/excalidraw_*, riptide/grafiphy/**, comp/diagram, color, description, paths

### Community 56 - "comp/acp"
Cohesion: 0.33
Nodes (6): riptide/deepthink.py, comp/deepthink, color, description, paths, ChonSong/riptide

### Community 57 - "comp/cron"
Cohesion: 0.33
Nodes (6): riptide/labeler*, riptide/resources/label-definitions*, comp/labeler, color, description, paths

### Community 58 - "comp/agent"
Cohesion: 0.33
Nodes (6): src/tools/**, color, description, paths, comp/tools, NousResearch/hermes-agent

### Community 59 - "comp/plugins"
Cohesion: 0.40
Nodes (4): Added, Changelog, Fixed, [Unreleased]

### Community 60 - "comp/tui"
Cohesion: 0.40
Nodes (5): riptide/companion.py, comp/companion, color, description, paths

### Community 61 - "Riptide"
Cohesion: 0.40
Nodes (5): src/acp/**, color, description, paths, comp/acp

### Community 62 - "Handoff"
Cohesion: 0.40
Nodes (5): src/cron/**, color, description, paths, comp/cron

### Community 63 - "Security Policy"
Cohesion: 0.40
Nodes (5): src/hermes_agent/**, color, description, paths, comp/agent

### Community 64 - "priority"
Cohesion: 0.40
Nodes (5): src/plugins/**, color, description, paths, comp/plugins

### Community 65 - "status/draft"
Cohesion: 0.40
Nodes (5): src/ui-tui/**, color, description, paths, comp/tui

### Community 66 - "status/stale"
Cohesion: 0.50
Nodes (3): Local test, Riptide, Workability

### Community 67 - "pre-commit"
Cohesion: 0.50
Nodes (3): Agent Readiness, Common Failure Mode, Handoff

### Community 69 - "type/perf"
Cohesion: 0.50
Nodes (3): Reporting a Vulnerability, Security Policy, Supported Versions

### Community 70 - "type/refactor"
Cohesion: 0.67
Nodes (3): type/ci, color, description

### Community 71 - "type/security"
Cohesion: 0.67
Nodes (3): type/deps, color, description

### Community 72 - "type/test"
Cohesion: 0.67
Nodes (3): type/docs, color, description

### Community 73 - "type/perf"
Cohesion: 0.67
Nodes (3): type/perf, color, description

### Community 74 - "type/refactor"
Cohesion: 0.67
Nodes (3): type/refactor, color, description

### Community 75 - "type/security"
Cohesion: 0.67
Nodes (3): type/security, color, description

### Community 76 - "type/test"
Cohesion: 0.67
Nodes (3): type/test, color, description

## Knowledge Gaps
- **256 isolated node(s):** `$schema`, `version`, `description`, `created`, `maintainer` (+251 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Companion` connect `Companion` to `webhook.py`, `make_companion`, `riptide/orchestrator.py`, `label-definitions.json`, `labels`, `dimensions`?**
  _High betweenness centrality (0.068) - this node is a cross-community bridge._
- **Why does `repos` connect `paths` to `comp/acp`, `comp/agent`, `comp/proofshot`?**
  _High betweenness centrality (0.065) - this node is a cross-community bridge._
- **Why does `dimensions` connect `TestWebhookEndpoint` to `TaskClassifier`, `TestAgentlintConfig`?**
  _High betweenness centrality (0.050) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `Companion` (e.g. with `TestBot2Status` and `TestSpawnRetry`) actually correct?**
  _`Companion` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `StateStore` (e.g. with `TestResultValidator` and `TestStateStore`) actually correct?**
  _`StateStore` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `T0Orchestrator` (e.g. with `TestResultValidator` and `TestStateStore`) actually correct?**
  _`T0Orchestrator` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `TestStateStore` (e.g. with `ResultValidator` and `StateStore`) actually correct?**
  _`TestStateStore` has 5 INFERRED edges - model-reasoned connections that need verification._