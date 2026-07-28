# Graph Report - riptide  (2026-07-27)

## Corpus Check
- 14 files · ~11,671 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 229 nodes · 332 edges · 10 communities (9 shown, 1 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS · INFERRED: 1 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `8fe2ff54`
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

## God Nodes (most connected - your core abstractions)
1. `Companion` - 18 edges
2. `GitHubAppClient` - 18 edges
3. `Riptide — Self-Hosted GitHub App PR Reviewer` - 16 edges
4. `github_webhook()` - 11 edges
5. `process_jobs()` - 9 edges
6. `handle_pull_request()` - 9 edges
7. `embed_texts()` - 8 edges
8. `_run_review()` - 8 edges
9. `_run_incremental_index()` - 8 edges
10. `Riptide — Self-Hosted GitHub App PR Reviewer` - 8 edges

## Surprising Connections (you probably didn't know these)
- `demo_workflow()` --calls--> `chunk_text()`  [EXTRACTED]
  riptide_example.py → riptide/embed.py
- `demo_workflow()` --calls--> `embed_texts()`  [EXTRACTED]
  riptide_example.py → riptide/embed.py
- `start_worker()` --indirect_call--> `process_jobs()`  [INFERRED]
  riptide/webhook.py → riptide/review_worker.py
- `demo_workflow()` --calls--> `init_store()`  [EXTRACTED]
  riptide_example.py → riptide/store.py
- `demo_workflow()` --calls--> `upsert_chunks()`  [EXTRACTED]
  riptide_example.py → riptide/store.py

## Import Cycles
- None detected.

## Communities (10 total, 1 thin omitted)

### Community 0 - "GitHubAppClient"
Cohesion: 0.11
Nodes (14): base64url_encode(), GitHubAppClient, InstallationTokenCache, jwt_token(), Authenticated GitHub API client using GitHub App installation tokens.     All me, Fetch all files changed in a PR., Post a PR-level comment (not inline). Uses issues endpoint for top-level comment, Post an inline comment on a specific file:line. (+6 more)

### Community 1 - "store.py"
Cohesion: 0.08
Nodes (34): ndarray, chunk_text(), embed_query(), embed_texts(), embed.py — Ollama embeddings for Riptide.  Wraps the proven logic from pr-review, Split text into overlapping chunks, preferring code-structure boundaries., Embed a list of text chunks via Ollama /api/embed.     Returns list of embedding, Embed a single query string (returns zero vector on failure). (+26 more)

### Community 2 - "github_webhook"
Cohesion: 0.08
Nodes (42): Request, Response, Verify GitHub webhook X-Hub-Signature-256 header.     secret = WEBHOOK_SECRET en, verify_webhook_signature(), enqueue_review(), Add a review job. Blocks if queue is full (serialises reviews)., _clear_retry_count(), _find_hermes_bin() (+34 more)

### Community 3 - "github_app.py"
Cohesion: 0.05
Nodes (36): 1. Automatic Code Review, 2. Companion TLDR, 3. Deep Thinking for "Need Action", 4. ProofShot Visual Verification, API Reference, Architecture, Build a Graph, Companion Configuration (+28 more)

### Community 4 - "review_worker.py"
Cohesion: 0.12
Nodes (25): Queue, embed_texts(), enqueue_index(), _format_finding_body(), _format_summary_comment(), _get_review_scripts(), _import_review_scripts(), _llm_review() (+17 more)

### Community 5 - "embed_texts"
Cohesion: 0.16
Nodes (4): classify_pr_mood(), Companion, companion.py — GitHub Companion PR Agent for Riptide.  Pipeline:   1. Fetch PR d, Build the Markdown comment body using the Phase 4 TLDR spec.         Includes op

### Community 6 - "review.py"
Cohesion: 0.12
Nodes (15): another sync, Checklist, ELI5 test, fallback test, final test, final test after fixes, issues endpoint fix, real test (+7 more)

### Community 7 - "_run_incremental_index"
Cohesion: 0.12
Nodes (15): 1. Configure environment, 2. Point webhook at this server, 3. Install the GitHub App webhook, 4. Start the server, Architecture, Automatic review, Files, Key differences from Octopus (+7 more)

## Knowledge Gaps
- **55 isolated node(s):** `start.sh script`, `What this PR does`, `Checklist`, `sync test`, `another sync` (+50 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `embed_texts()` connect `store.py` to `review_worker.py`?**
  _High betweenness centrality (0.175) - this node is a cross-community bridge._
- **Why does `GitHubAppClient` connect `GitHubAppClient` to `github_webhook`, `review_worker.py`?**
  _High betweenness centrality (0.118) - this node is a cross-community bridge._
- **Why does `Companion` connect `embed_texts` to `github_webhook`?**
  _High betweenness centrality (0.106) - this node is a cross-community bridge._
- **What connects `start.sh script`, `What this PR does`, `Checklist` to the rest of the system?**
  _55 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `GitHubAppClient` be split into smaller, more focused modules?**
  _Cohesion score 0.10826210826210826 - nodes in this community are weakly interconnected._
- **Should `store.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07692307692307693 - nodes in this community are weakly interconnected._
- **Should `github_webhook` be split into smaller, more focused modules?**
  _Cohesion score 0.0797979797979798 - nodes in this community are weakly interconnected._