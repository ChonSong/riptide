# Graph Report - .  (2026-07-25)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 114 nodes · 186 edges · 14 communities (13 shown, 1 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 1 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `188c84ae`
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
- search
- webhook.py
- _finalise_finding
- enqueue_review

## God Nodes (most connected - your core abstractions)
1. `GitHubAppClient` - 18 edges
2. `process_jobs()` - 9 edges
3. `embed_texts()` - 8 edges
4. `_run_review()` - 8 edges
5. `_run_incremental_index()` - 8 edges
6. `github_webhook()` - 8 edges
7. `search()` - 7 edges
8. `handle_pull_request()` - 7 edges
9. `demo_workflow()` - 7 edges
10. `enqueue_review()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `demo_workflow()` --calls--> `chunk_text()`  [EXTRACTED]
  riptide_example.py → riptide/embed.py
- `demo_workflow()` --calls--> `embed_texts()`  [EXTRACTED]
  riptide_example.py → riptide/embed.py
- `demo_workflow()` --calls--> `search()`  [EXTRACTED]
  riptide_example.py → riptide/store.py
- `start_worker()` --indirect_call--> `process_jobs()`  [INFERRED]
  riptide/webhook.py → riptide/review_worker.py
- `demo_workflow()` --calls--> `init_store()`  [EXTRACTED]
  riptide_example.py → riptide/store.py

## Import Cycles
- None detected.

## Communities (14 total, 1 thin omitted)

### Community 0 - "GitHubAppClient"
Cohesion: 0.16
Nodes (9): GitHubAppClient, Authenticated GitHub API client using GitHub App installation tokens.     All me, Fetch all files changed in a PR., Post a PR-level comment., Post an inline comment on a specific file:line., Create a check run (GitHub CI integration)., Update a check run with results., Add a reaction to a comment. (+1 more)

### Community 1 - "store.py"
Cohesion: 0.22
Nodes (10): demo_workflow(), Demonstrate the basic Riptide workflow., get_stats(), init_store(), store.py — NumPy vector store for Riptide.  Pure numpy (float32 blobs) + SQLite, Return chunk count and unique path count., Create tables if they don't exist., Upsert a list of (chunk_text, vector) for a given file.     Replaces all existin (+2 more)

### Community 2 - "github_webhook"
Cohesion: 0.27
Nodes (11): Request, Response, github_client(), github_webhook(), handle_installation(), handle_issue_comment(), handle_pull_request(), Handle pull_request events — enqueue review or incremental index. (+3 more)

### Community 3 - "github_app.py"
Cohesion: 0.22
Nodes (7): base64url_encode(), InstallationTokenCache, jwt_token(), Generate a GitHub App JWT.     https://docs.github.com/en/apps/creating-github-a, Verify GitHub webhook X-Hub-Signature-256 header.     secret = WEBHOOK_SECRET en, Caches installation tokens. GitHub App tokens expire after 1 hour.     We refres, verify_webhook_signature()

### Community 4 - "review_worker.py"
Cohesion: 0.31
Nodes (9): Queue, enqueue_index(), _get_review_scripts(), _import_review_scripts(), process_jobs(), Import review.py, store.py as modules from the pr-review scripts dir., Top-K vector search using the numpy store., Single-threaded daemon loop. Blocks on queue.get(), processes one job at a time. (+1 more)

### Community 5 - "embed_texts"
Cohesion: 0.32
Nodes (7): chunk_text(), embed_query(), embed_texts(), embed.py — Ollama embeddings for Riptide.  Wraps the proven logic from pr-review, Split text into overlapping chunks, preferring code-structure boundaries., Embed a list of text chunks via Ollama /api/embed.     Returns list of embedding, Embed a single query string (returns zero vector on failure).

### Community 6 - "review.py"
Cohesion: 0.25
Nodes (7): build_prompt(), format_comment(), llm_review(), review.py — LLM code review module for Riptide.  Adapts Octopus's review pipelin, Format review result as a GitHub-flavoured Markdown comment., Call Ollama /api/generate for code review., Build the code-review prompt, adapted from Octopus SYSTEM_PROMPT.md.      Severi

### Community 7 - "_run_incremental_index"
Cohesion: 0.25
Nodes (8): embed_texts(), After a PR is merged, update the numpy vector store with only the changed files., Embed texts using local Ollama., Add or update a file entry in the numpy vector store., Remove a file from the numpy store., _remove_from_store(), _run_incremental_index(), _upsert_to_store()

### Community 8 - "_run_review"
Cohesion: 0.25
Nodes (8): _format_finding_body(), _format_summary_comment(), _llm_review(), Full review: fetch diff → retrieve context → LLM → post results., Run LLM review using existing review.py., Format a single finding as an inline comment body., Format the PR-level summary comment., _run_review()

### Community 9 - "search"
Cohesion: 0.33
Nodes (6): ndarray, Top-K vector search using the numpy store., retrieve_context(), bytes_to_vec(), Cosine-similarity top-K search over all chunks.     Returns [(chunk_text, path,, search()

### Community 10 - "webhook.py"
Cohesion: 0.40
Nodes (3): init_db(), Start the background review worker thread., start_worker()

### Community 11 - "_finalise_finding"
Cohesion: 0.50
Nodes (4): _finalise_finding(), parse_review(), Parse LLM review into structured findings.     Adapted from Octopus review-dedup, Add a validated finding to the list, skip if too short.

## Knowledge Gaps
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `embed_texts()` connect `embed_texts` to `store.py`, `review_worker.py`, `_run_incremental_index`?**
  _High betweenness centrality (0.453) - this node is a cross-community bridge._
- **Why does `GitHubAppClient` connect `GitHubAppClient` to `github_webhook`, `github_app.py`, `review_worker.py`, `_run_incremental_index`, `_run_review`, `webhook.py`?**
  _High betweenness centrality (0.345) - this node is a cross-community bridge._
- **Why does `_run_incremental_index()` connect `_run_incremental_index` to `GitHubAppClient`, `review_worker.py`, `embed_texts`?**
  _High betweenness centrality (0.140) - this node is a cross-community bridge._