# Graph Report - riptide  (2026-07-28)

## Corpus Check
- 13 files · ~8,732 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 147 nodes · 191 edges · 13 communities (12 shown, 1 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 1 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `b1f0e0c6`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- GitHubAppClient
- store.py
- github_webhook
- github_app.py
- review_worker.py
- embed_texts
- review.py
- _run_incremental_index
- _run_review
- Grafiphy — ELI5 Pseudocode Diagram Generation
- generate_labels

## God Nodes (most connected - your core abstractions)
1. `Companion` - 18 edges
2. `GitHubAppClient` - 14 edges
3. `Riptide — Self-Hosted GitHub App (Two Bots)` - 12 edges
4. `github_webhook()` - 8 edges
5. `Grafiphy — ELI5 Pseudocode Diagram Generation` - 8 edges
6. `orchestrate()` - 7 edges
7. `Riptide — Two-Bot GitHub App` - 7 edges
8. `run()` - 6 edges
9. `get_graphify_graph()` - 6 edges
10. `handle_issue_comment()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `get_companion()` --calls--> `Companion`  [EXTRACTED]
  riptide/webhook.py → riptide/companion.py
- `github_client()` --references--> `GitHubAppClient`  [EXTRACTED]
  riptide/webhook.py → riptide/github_app.py
- `github_webhook()` --calls--> `verify_webhook_signature()`  [EXTRACTED]
  riptide/webhook.py → riptide/github_app.py

## Import Cycles
- None detected.

## Communities (13 total, 1 thin omitted)

### Community 0 - "GitHubAppClient"
Cohesion: 0.16
Nodes (9): GitHubAppClient, Authenticated GitHub API client using GitHub App installation tokens.     All me, Fetch all files changed in a PR., Post a PR-level comment (not inline). Uses issues endpoint for top-level comment, Post an inline comment on a specific file:line., Create a check run (GitHub CI integration)., Update a check run with results., Add a reaction to a comment. (+1 more)

### Community 1 - "store.py"
Cohesion: 0.17
Nodes (16): generate_labels(), get_graphify_graph(), orchestrate(), _parse_god_nodes(), _parse_query(), Generate ELI5 pseudocode labels., Generate Excalidraw JSON with ELI5 pseudocode nodes.          Layout: Top-to-bot, Run a graphify command and return (stdout, stderr). (+8 more)

### Community 2 - "github_webhook"
Cohesion: 0.19
Nodes (14): Request, Response, Verify GitHub webhook X-Hub-Signature-256 header.     secret = WEBHOOK_SECRET en, verify_webhook_signature(), get_companion(), github_client(), github_webhook(), handle_installation() (+6 more)

### Community 3 - "github_app.py"
Cohesion: 0.08
Nodes (23): API Reference, Architecture, Bot 1: Companion (Webhook-Triggered), Bot 2: Riptide Review (Cron-Triggered), Companion Configuration, Comparison with Alternatives, Configure, Development (+15 more)

### Community 4 - "review_worker.py"
Cohesion: 0.29
Nodes (9): _is_cron_available(), _load_state(), Poll watched repos and spawn deep-think sessions on qualifying PRs., Load processed PR state: {owner/repo#number: head_sha}, Check that `hermes cron create` works., Spawn a Hermes cron session for deep-think review on this PR., run(), _save_state() (+1 more)

### Community 5 - "embed_texts"
Cohesion: 0.16
Nodes (4): classify_pr_mood(), Companion, companion.py — GitHub Companion PR Agent for Riptide.  Pipeline:   1. Fetch PR d, Build the Markdown comment body using the Phase 4 TLDR spec.         Includes op

### Community 6 - "review.py"
Cohesion: 0.28
Nodes (5): base64url_encode(), InstallationTokenCache, jwt_token(), Generate a GitHub App JWT.     https://docs.github.com/en/apps/creating-github-a, Caches installation tokens. GitHub App tokens expire after 1 hour.     We refres

### Community 7 - "_run_incremental_index"
Cohesion: 0.18
Nodes (10): 1. Configure environment, 2. Start the server, 3. Set up the cron for Bot 2, Architecture, Bot 1: Companion (Webhook-Triggered), Bot 2: Riptide Review (Cron-Triggered), Files, Key Differences from Octopus (+2 more)

### Community 9 - "Grafiphy — ELI5 Pseudocode Diagram Generation"
Cohesion: 0.22
Nodes (8): Architecture, ELI5 Pseudocode in Nodes, Excalidraw JSON Structure, Files, Grafiphy — ELI5 Pseudocode Diagram Generation, Integration, Label Format (Strict), Server Status

### Community 10 - "generate_labels"
Cohesion: 0.38
Nodes (6): _generate_edge_label(), generate_labels(), _generate_node_label(), Generate a label for an edge based on relationship type., Generate labels using templates based on graphify data structure.          Args:, Generate a label for a node based on its properties.

## Knowledge Gaps
- **34 isolated node(s):** `start.sh script`, `Architecture`, `Skip/Resume Per PR`, `Bot 2: Riptide Review (Cron-Triggered)`, `Repo Structure` (+29 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Companion` connect `embed_texts` to `github_webhook`?**
  _High betweenness centrality (0.178) - this node is a cross-community bridge._
- **Why does `GitHubAppClient` connect `GitHubAppClient` to `github_webhook`, `review.py`?**
  _High betweenness centrality (0.112) - this node is a cross-community bridge._
- **Why does `orchestrate()` connect `store.py` to `embed_texts`?**
  _High betweenness centrality (0.101) - this node is a cross-community bridge._
- **What connects `start.sh script`, `Architecture`, `Skip/Resume Per PR` to the rest of the system?**
  _34 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `github_app.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08333333333333333 - nodes in this community are weakly interconnected._