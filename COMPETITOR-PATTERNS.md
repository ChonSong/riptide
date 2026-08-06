# Competitor Review Patterns: CodeRabbit & Greptile

> Research findings on how leading AI code review bots structure their analysis, comment pipelines, and user experience. Mapped to Riptide's 5-pillar vision.
>
> **Date:** 2026-08-06
> **Scope:** Public documentation, blog posts, and third-party analysis of CodeRabbit and Greptile.

---

## 1. CodeRabbit

### 1.1 Multi-Stage Agentic Pipeline

CodeRabbit uses a **multi-stage agentic pipeline** consisting of:

- **Sandbox**: A prepared copy of the repository with installed dependencies (cached between reviews to reduce latency)
- **Live code graph**: A GraphRAG-style representation of the codebase for context-aware analysis
- **Ensemble of 7-8 models**: Multiple LLMs working in concert for review reliability
- **Judge gating**: Every finding is gated by a judge model before publication
- **Bounded recursion**: Tightly constrained pipeline stages prevent runaway agent behavior

> *"CodeRabbit reviews every pull request with a multi-stage agentic pipeline: a sandbox, a live code graph, and an ensemble of 7-8 models. CodeRabbit looks like a free-roaming AI agent, but what makes it reliable is how tightly it is constrained: fixed pipeline stages, bounded recursion, and a judge gating every finding."*
>
> — [How CodeRabbit Works: Inside Its AI Code Review Pipeline](https://theaiengineer.substack.com/p/how-coderabbit-actually-works) (Jun 2026)

### 1.2 Deterministic-First-then-LLM-Enrich

CodeRabbit integrates **static analysis before LLM review**:

> *"When a pull request is created or updated, CodeRabbit loads the tool configuration, runs the appropriate static analysis tools for the changed files, and forwards the results to the LLM for enhanced explanation and context. The final review combines both static analysis findings and AI-powered insights."*
>
> — [CodeRabbit Docs: Static Analysis Tools](https://deepwiki.com/coderabbit-docs/9-static-analysis-tools) (May 2025)

CodeRabbit provides **40+ built-in linters** covering ESLint, Pylint, Golint, RuboCop, and more:

> *"40+ built-in linters. Deterministic checks for style, naming conventions, and known anti-patterns complement the AI analysis. CodeRabbit's 40+ linters provide deterministic checks that complement the AI analysis. No other AI code review tool offers this combination."*
>
> — [CodeRabbit vs Bito: AI Code Review Comparison for 2026](https://www.bundle.app/en/technology/coderabbit-vs-bito-ai-code-review-comparison-for-2026-73DD82B4-5814-4259-838E-24463C9FE7E2)

### 1.3 Progressive Disclosure: Walkthrough → Changes Table → Line-by-Line

CodeRabbit posts a **structured review comment** with clear progressive disclosure:

1. **Walkthrough summary**: Plain-English explanation of what changed and why
2. **Changes table**: File-by-file impact breakdown
3. **Line-by-line code suggestions**: Specific, actionable feedback with severity labels
4. **One-click fix suggestions**: Auto-generated fixes that can be committed directly

> *"When a pull request is opened, CodeRabbit posts a structured review comment directly in the PR thread. The walkthrough section provides a concise, plain-English explanation of what changed and why. Below it, a changes table breaks down each modified file with a summary of what was done."*
>
> — [CodeRabbit Review 2026: Features, Pricing & Honest Verdict](https://max-productive.ai/ai-tools/coderabbit/)

> *"Pull Request Summary: The diff in the pull request is transformed into a clear summary, helping you understand the intent of the changes. Line-by-Line Code Suggestions: A detailed, line-by-line analysis of the code changes provides precise and actionable suggestions ready to be committed to your pull requests with a simple click. Chat with CodeRabbit: Query, contextualize, and seek advice within your code lines."*
>
> — [CodeRabbit - GitHub Marketplace](https://github.com/marketplace/coderabbitai)

### 1.4 Explainable, Layered Walkthroughs

> *"That is how reviewers end up reading call sites before the schema they depend on, tests before the business logic they cover, or UI changes before the underlying API exists. They have to jump backward and forward through the diff to reconstruct the path they should have been given upfront. CodeRabbit Review removes that reconstruction step."*
>
> — [Explainable AI Code Review: How CodeRabbit Review Works](https://www.coderabbit.ai/blog/coderabbit-review-reads-a-pr-how-author-would-explain-it) (Jun 2026)

### 1.5 Severity Tuning

CodeRabbit groups findings by **severity levels**: Critical, Warning, Info (🚨 / ⚠️ / ℹ️). This maps to a clear actionability hierarchy.

> *"CodeRabbit posts a structured review comment directly in your PR with a high-level walkthrough summary, per-file line-by-line comments with severity labels, and one-click fix suggestions."*
>
> — [CodeRabbit Review 2026: Features, Pricing & Honest Verdict](https://max-productive.ai/ai-tools/coderabbit/)

### 1.6 Context Behind Comments (Progressive Disclosure)

> *"Before this update, a comment stated the finding and stopped there. You could read what CodeRabbit flagged, but the context that prompted it stayed out of view. If a comment looked wrong, your options were to accept it or dismiss it. The latter left the underlying context untouched, so the same comment could return on your next pull request."*
>
> — [See the Context Behind Every CodeRabbit Review Comment](https://www.coderabbit.ai/blog/context-behind-code-review-comments) (Jul 2026)

---

## 2. Greptile

### 2.1 Full Codebase Indexing + Graph

Greptile's core differentiator is indexing the **entire codebase** before review:

> *"Greptile takes a fundamentally different approach. It indexes your entire codebase first, building a semantic graph of functions, classes, variables, dependencies, and architectural patterns before it ever looks at a pull request."*
>
> — [Greptile Review: The Full-Codebase AI Code Reviewer](https://aicoolies.com/reviews/greptile-review) (Mar 2026)

> *"Greptile constructs a graph index of your codebase, then uses a swarm of agents to catch potential issues that humans might miss."*
>
> — [Greptile: AI Code Review](https://www.greptile.com/)

### 2.2 Multi-Hop Investigation (Multi-Pass)

> *"The review process involves multi-hop investigation: Greptile reads the diff, traces affected dependencies across the codebase, checks git history for relevant context, and produces line-level comments with confidence scores."*
>
> — [Greptile Review (2026)](https://aicodereview.cc/tool/greptile/) (Mar 2026)

### 2.3 Sub-Agent Architecture (Orchestrator + Parallel TREX Agents)

> *"The Greptile reviewer agent acts as an orchestrator. It reads the diff, identifies issues worth investigating, and spins up a dedicated TREX agent per issue — all running in parallel."*
>
> — [TREX — Greptile's AI Code Reviewer That Actually Runs Your Code](https://promptgenius.net/blog/trex-greptile-ai-code-reviewer-runs-code) (Jun 2026)

### 2.4 TREX: Execution Layer (Deterministic Verification)

> *"Static code review has a ceiling. It can reason about what the code says. It can't tell you what it does. TREX (which stands for 'Test, Run, Execute') is Greptile's response to that ceiling: an execution layer built directly into code review."*
>
> — [Building TREX: Code Execution and Artifact Generation for AI Code Review](https://www.greptile.com/blog/trex-code-execution) (Jun 2026)

> *"TREX executes code during review to verify bugs are real, cutting the false positives that plague most AI code reviewers."*
>
> — [TREX by Greptile: AI Code Review That Runs Your Code](https://aiproductivity.ai/news/greptile-trex-ai-code-reviewer-runs-code/) (Jun 2026)

### 2.5 Multi-Model Architecture

> *"Greptile gives 22,000 engineering teams senior-level code review on every pull request. Here is the multi-model architecture behind it, and what we found when we put NVIDIA Nemotron Ultra to the test."*
>
> — [Frontier Code Review Accuracy at Lower Cost with NVIDIA Nemotron Ultra](https://www.greptile.com/blog/nvidia-nemotron-ultra-in-code-review)

### 2.6 Anatomy of a Review (Progressive Disclosure)

> *"Understand every component of a Greptile code review: PR summaries, confidence scores, inline comments, suggested fixes, and diagrams explained."*
>
> — [Anatomy of a Review - Greptile](https://www.greptile.com/docs/code-review/first-pr-review)

Greptile produces reviews with:
- **PR summaries**
- **Confidence scores** (0.0–1.0 on findings)
- **Inline comments**
- **Suggested fixes**
- **Diagrams** (visual explanation of issues)

### 2.7 Comment Editing / Progressive Refinement (Confirmed)

Greptile **edits the same general PR comment** on each review cycle, progressively refining the review:

> *"Filter for Greptile-authored comments and use the body from the most recently updated comment (updated_at), not the most recently created comment. Greptile may edit the same general PR comment on each review cycle; parse the current body, including the 'Prompt to fix all with AI' section, before deciding there are no remaining issues."*
>
> — [greploop SKILL.md](https://github.com/greptileai/skills/blob/main/greploop/SKILL.md) (Jul 2026)

### 2.8 Confidence Scores (Not Traditional Severity)

Greptile uses **confidence scores** (0.0–1.0) per finding rather than severity labels. This allows developers to calibrate: high-confidence findings are likely real bugs; lower-confidence ones may be style or inference issues.

---

## 3. Patterns to Adopt for Riptide

Mapping competitor patterns to Riptide's 5-pillar vision:

### Pillar 1: Deterministic Data Inputs (diff→concept pipeline, test status, graphify blast radius)

| Pattern | Source | Evidence | Adoption Recommendation |
|---------|--------|----------|------------------------|
| **Static analysis → LLM handoff** | CodeRabbit | 40+ linters run first; results forwarded to LLM for explanation | **Adopt.** Extend `diff_analyzer.py` to emit structured findings, then pass to LLM for enrichment. The existing security/complexity/error-handling patterns are the start of this pipeline. |
| **Code graph for blast radius** | CodeRabbit | "Live code graph" provides context-aware analysis | **Already have.** Graphify provides this. Formalize the diff→concept pipeline: graphify blast radius + test status + lint results → structured input to LLM. |
| **Codebase-wide indexing** | Greptile | Full-repo semantic graph before diff analysis | **Partially have.** Graphify indexes the repo. Use it to trace cross-file dependencies during review (not just local diff context). |
| **Execution-layer verification** | Greptile (TREX) | Runs code to verify bugs are real | **Explore.** Riptide's proofshotter already captures visual evidence. Extend to: run tests in sandbox, verify findings against actual runtime behavior. |

### Pillar 2: Two-Tier Response Flow (fast deterministic comment, then LLM enriches SAME comment via PATCH edit)

| Pattern | Source | Evidence | Adoption Recommendation |
|---------|--------|----------|------------------------|
| **Deterministic-first, then LLM-enrich same comment** | CodeRabbit | Static analysis runs first, LLM enhances explanation | **Adopt.** Post initial deterministic findings immediately (fast), then PATCH the same comment with LLM-enriched analysis. This matches Riptide's existing plan exactly. |
| **Edit same comment on review cycle** | Greptile | "Greptile may edit the same general PR comment on each review cycle" | **Adopt.** This is the exact two-tier flow. GitHub's PATCH `/repos/{owner}/{repo}/pulls/comments/{comment_id}` API enables it. |
| **Comment versioning** | Greptile | Uses `updated_at` to track progressive refinement | **Adopt.** Include a "last updated" timestamp and review cycle counter in the comment footer. |

### Pillar 3: Multi-Pass LLM Strategy (multiple deterministic calls, each analyzing a different aspect)

| Pattern | Source | Evidence | Adoption Recommendation |
|---------|--------|----------|------------------------|
| **Ensemble of 7-8 models** | CodeRabbit | Multiple LLMs in concert | **Adapt, don't copy.** Instead of model ensemble, use **task-specialized passes**: security pass, complexity pass, architecture pass, test-coverage pass. Each is a focused deterministic + LLM call. |
| **Sub-agent per issue (parallel)** | Greptile | Spins up dedicated TREX agent per issue in parallel | **Adopt structure, not parallelism cost.** Run passes sequentially or with bounded parallelism (cost control). Each pass produces findings in a structured format. |
| **Multi-hop investigation** | Greptile | Reads diff → traces deps → checks git history | **Adopt.** Explicit multi-hop: (1) diff analysis, (2) graphify blast radius, (3) git history for similar past bugs, (4) test status. Each hop feeds the next. |
| **Judge gating** | CodeRabbit | Every finding gated by a judge model | **Adopt lightweight version.** After LLM enrichment, run a deterministic "judge" pass: does the finding reference actual code? Does it duplicate an existing comment? Is confidence above threshold? |

### Pillar 4: Latency Tolerance (progress indicators on PR comments)

| Pattern | Source | Evidence | Adoption Recommendation |
|---------|--------|----------|------------------------|
| **Progressive comment refinement** | Greptile | Edits same comment on each review cycle | **Adopt.** Post "🔍 Riptide reviewing..." immediately. Update with findings as each pass completes. Final version is the enriched review. |
| **Sandbox caching** | CodeRabbit | Reuses prepared copy of repo between reviews | **Out of scope for now.** Self-hosted Riptide has local repo access; no sandbox needed. |
| **Structured walkthrough** | CodeRabbit | Walkthrough summary + changes table + line-by-line | **Adopt.** Structure the Riptide comment: (1) TL;DR verdict, (2) severity-grouped findings, (3) per-file details with line refs, (4) suggested fixes. |

### Pillar 5: High-Level Clarity (verdict-first, progressive disclosure)

| Pattern | Source | Evidence | Adoption Recommendation |
|---------|--------|----------|------------------------|
| **Walkthrough summary first** | CodeRabbit | Plain-English explanation of what changed and why | **Adopt.** Verdict-first: "This PR adds X, modifies Y, risks Z." Then drill down. |
| **Severity labels** | CodeRabbit | Critical / Warning / Info (🚨 / ⚠️ / ℹ️) | **Adopt.** Use severity labels. Riptide already plans this. |
| **Confidence scores** | Greptile | 0.0–1.0 per finding | **Consider as alternative.** Confidence is more nuanced than severity. Could combine: severity (actionability) + confidence (certainty). |
| **Changes table** | CodeRabbit | File-by-file impact breakdown | **Adopt.** Include a compact file-by-file impact table in the review summary. |
| **"Context behind every comment"** | CodeRabbit | Expandable context for each finding | **Adopt via progressive disclosure.** Collapse low-severity findings by default. Expandable details for those who want depth. |
| **One-click fix suggestions** | CodeRabbit | Auto-generated fixes committable with one click | **Already planned.** Riptide's fixer bot does this. Tie fixes to specific findings in the review comment. |

---

## 4. Synthesis: Recommended Riptide Review Pipeline

Based on the competitor research, here is a concrete pipeline design that synthesizes both CodeRabbit and Greptile patterns:

### Phase 0: Trigger & Preflight (existing)
- Webhook → Companion posts TL;DR
- Cron → Deepthink polls for deep review

### Phase 1: Deterministic Pass (fast, <5s)
- Run `diff_analyzer.py` (security/complexity/error-handling patterns)
- Run graphify blast radius query
- Run test status check
- Emit structured findings (JSON)

### Phase 2: Immediate Comment Post (latency tolerance)
- POST initial comment: "🔍 Riptide reviewing... (deterministic findings: N)"
- Include Phase 1 findings grouped by severity
- Comment includes progress indicator

### Phase 3: Multi-Pass LLM Enrichment (30s–3min)
Passes run sequentially or bounded-parallel:
1. **Security pass** → enrich security findings with exploitability context
2. **Complexity pass** → enrich complexity findings with simplification suggestions
3. **Architecture pass** → trace cross-file deps via graphify, check for drift
4. **Test coverage pass** → check if changed code has adequate test coverage

Each pass produces structured enriched findings.

### Phase 4: Comment Update via PATCH (progressive refinement)
- PATCH the Phase 2 comment with enriched findings
- Structure:
  ```
  ## Riptide Review (updated)
  
  ### Verdict: [Approve / Request Changes / Needs Discussion]
  
  ### Summary
  Plain-English walkthrough of changes and risks.
  
  ### Findings (N)
  🚨 Critical (N) | ⚠️ Warning (N) | ℹ️ Info (N)
  
  #### Critical
  - [file:line] finding — [confidence: 0.92] — [fix suggestion]
  
  #### Warnings
  - ...
  
  ### Impact Table
  | File | Change | Risk |
  |------|--------|------|
  
  ---
  *Review cycle 2 · Updated 45s ago · @riptide-bot fix to auto-address*
  ```

### Phase 5: Judge Gate & Dedup
- Filter out findings below confidence threshold
- Dedup against existing Riptide comments (existing SHA-based dedup)
- Dedup against findings already addressed in code

### Phase 6: Fix Suggestions (existing fixer integration)
- One-click or `@riptide-bot fix` to auto-address findings
- Each fix references the specific finding ID

---

## 5. Source URLs

### CodeRabbit
- [How CodeRabbit Works: Inside Its AI Code Review Pipeline](https://theaiengineer.substack.com/p/how-coderabbit-actually-works) (Jun 2026)
- [CodeRabbit Docs: Static Analysis Tools](https://deepwiki.com/coderabbit-docs/9-static-analysis-tools) (May 2025)
- [Explainable AI Code Review: How CodeRabbit Review Works](https://www.coderabbit.ai/blog/coderabbit-review-reads-a-pr-how-author-would-explain-it) (Jun 2026)
- [See the Context Behind Every CodeRabbit Review Comment](https://www.coderabbit.ai/blog/context-behind-code-review-comments) (Jul 2026)
- [Pipeline AI vs. Agentic AI for Code Reviews](https://www.coderabbit.ai/blog/pipeline-ai-vs-agentic-ai-for-code-reviews-let-the-model-reason-within-reason) (May 2025)
- [CodeRabbit - GitHub Marketplace](https://github.com/marketplace/coderabbitai)
- [CodeRabbit Review 2026: Features, Pricing & Honest Verdict](https://max-productive.ai/ai-tools/coderabbit/)
- [CodeRabbit vs Bito: AI Code Review Comparison for 2026](https://www.bundle.app/en/technology/coderabbit-vs-bito-ai-code-review-comparison-for-2026-73DD82B4-5814-4259-838E-24463C9FE7E2)

### Greptile
- [Building TREX: Code Execution and Artifact Generation for AI Code Review](https://www.greptile.com/blog/trex-code-execution) (Jun 2026)
- [TREX — Greptile's AI Code Reviewer That Actually Runs Your Code](https://promptgenius.net/blog/trex-greptile-ai-code-reviewer-runs-code) (Jun 2026)
- [TREX by Greptile: AI Code Review That Runs Your Code](https://aiproductivity.ai/news/greptile-trex-ai-code-reviewer-runs-code/) (Jun 2026)
- [Greptile v3: Agentic AI Code Review with 256% Better Results](https://www.greptile.com/blog/greptile-v3-agentic-code-review) (Nov 2025)
- [Frontier Code Review Accuracy at Lower Cost with NVIDIA Nemotron Ultra](https://www.greptile.com/blog/nvidia-nemotron-ultra-in-code-review)
- [Anatomy of a Review - Greptile](https://www.greptile.com/docs/code-review/first-pr-review)
- [greploop SKILL.md (GitHub)](https://github.com/greptileai/skills/blob/main/greploop/SKILL.md) (Jul 2026)
- [Greptile Review (2026) - aicodereview.cc](https://aicodereview.cc/tool/greptile/)
- [There is an AI Code Review Bubble](https://www.greptile.com/blog/ai-code-review-bubble) (Jan 2026)

---

## 6. Inference Labeling

Patterns marked with **[Inference]** below are not directly cited from sources but are reasoned from public knowledge of how these tools work:

- CodeRabbit's "pathway" concept is inferred from their blog about "how author would explain it" — they organize reviews in dependency order (call sites before schemas, tests before business logic). **[Inference]**
- Greptile's confidence score range (0.0–1.0) is inferred from their docs page describing "confidence scores" — no public technical spec was found. **[Inference]**
- CodeRabbit's exact severity labels (Critical/Warning/Info with emoji icons) are inferred from third-party review articles; their official docs don't publicly enumerate them. **[Inference]**
- The exact number of CodeRabbit's "7-8 models" and their individual roles is from a Medium article; CodeRabbit's own docs don't break down the ensemble composition. **[Inference]**

All other patterns above are directly cited from the linked sources.