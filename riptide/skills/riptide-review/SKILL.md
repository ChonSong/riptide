---
name: riptide-review
description: "Riptide Review Bot — architecture, classification, and prompt contracts for deterministic + LLM PR review. Loaded via --skill for every Bot 2 cron session."
version: 1.0.0
author: Riptide
platforms: [linux]
metadata:
  hermes:
    tags: [riptide, review, bot, pipeline, architecture]
    related_skills: [deep-think]
---

# Riptide Review Bot — Architecture & Operations

This document defines how Bot 2 (Riptide Review) operates: what is deterministic, what is LLM-driven, how sessions stay focused, and how multiple models collaborate.

## I. Capability Classification

Every capability in the review pipeline is classified by execution type. **Do not reclassify without updating this table.**

### Deterministic (Python, no LLM)

| Capability | Implementation | Location |
|---|---|---|
| PR state detection | `gh pr view` + JSON parsing | `deepthink.py` |
| SHA dedup check | State file lookup | `deepthink.py` |
| Graphify analysis | `graphify god-nodes/query/affected` | `grafiphy/orchestrator.py` |
| Excalidraw rendering | `render_review()` → `upload_excalidraw()` | `grafiphy/excalidraw_renderer.py` |
| Blast radius calculation | Distance map from changed files | `grafiphy/orchestrator.py` |
| Diff parsing | `collect_code_chunks()` | `review_pipeline.py` |
| Comment assembly | Template rendering from JSON | `assemble_review.py` |
| GitHub posting | `gh pr comment` / `gh api` | `assemble_review.py` |
| Skill selection | Rule-based depth classification | `review_pipeline.py` |

### LLM (Hermes session, judgment required)

| Capability | Why LLM | Model tier |
|---|---|---|
| Severity assessment | Contextual judgment (is this a bug or intended?) | Authority |
| Design smell detection | Cross-domain pattern recognition | Authority (brooks-lint lens) |
| Logic bug detection | Understanding intent vs implementation | Authority |
| Suggestion generation | Creative reasoning about improvements | Authority |
| Scope audit | Semantic understanding of change intent | Fast (initial) → Authority (final) |

### Hybrid (LLM proposes, deterministic validates)

| Capability | LLM role | Deterministic role |
|---|---|---|
| Inline comment posting | Generates finding + suggestion | Validates line numbers in diff hanks |
| Summary review | Generates findings list | Assembles markdown from template |
| Multi-model consensus | Each model independently assesses | Compares verdicts, escalates disagreements |

---

## II. Decision Framework: Deterministic vs LLM

Use this framework when adding new capabilities.

### Use **deterministic** when ALL of these hold:

- [ ] The task has a single correct answer (parsing, matching, formatting)
- [ ] Output is structural (JSON, markdown, API payload)
- [ ] No semantic understanding of code is required
- [ ] Cost must be zero (no API call, no token usage)
- [ ] Same input must always produce same output (reproducibility)
- [ ] The task can be expressed as a finite set of rules

**Examples:** Parsing diffs, rendering diagrams, checking SHA equality, formatting JSON, posting comments.

### Use **LLM** when ANY of these hold:

- [ ] Judgment is required (is this code correct? is this a design smell?)
- [ ] Semantic understanding of code intent is needed
- [ ] Cross-domain pattern recognition (DRY, coupling, naming)
- [ ] Creative reasoning (suggest improvements, restructure code)
- [ ] Context depends on project conventions not visible in the diff alone
- [ ] The task cannot be fully specified with rules

**Examples:** Assessing severity, detecting logic bugs, suggesting refactors, recognizing architectural decay.

### Use **hybrid** when:

- [ ] LLM generates a proposal that must be validated against structural constraints
- [ ] Multiple models should cross-check each other
- [ ] LLM output feeds into a deterministic pipeline (JSON → template → post)

---

## III. Multi-Model Orchestration

Riptide uses a **two-tier model architecture** for cost-quality optimization.

### Model Tiers

| Tier | Model | Cost | Use case |
|---|---|---|---|
| **Fast** | `deepseek-v4-flash-free` (or equivalent) | Free / very low | Initial scan, triage, scope audit |
| **Authority** | `LongCat-2.0` (or equivalent) | Higher | Final verdict, severity assessment, brooks-lint lens |

### Orchestration Patterns

#### Pattern A: Fast Triage → Authority Deep-Dive

```
1. Fast model scans PR diff + graphify data
   → Output: "TRIVIAL" | "NEEDS_REVIEW" | "ARCH_REVIEW_REQUIRED"
   
2. If TRIVIAL → skip authority, post auto-approve
   If NEEDS_REVIEW → spawn authority session
   If ARCH_REVIEW_REQUIRED → spawn authority session with brooks-lint
```

**When to use:** Cost-sensitive, many PRs to triage.

#### Pattern B: Dual-Assessment + Consensus

```
1. Both models independently assess the same PR
   → Each outputs findings[] JSON
   
2. Deterministic comparator:
   - Both agree on severity → post comment
   - Disagree → escalate to authority model's verdict
   - Both miss something → fast model catches what authority missed (rare)
```

**When to use:** High-stakes PRs, catching false positives.

#### Pattern C: Authority-Only

```
1. Single authority model reviews PR
   → Outputs findings[] JSON
   
2. Deterministic assembly + posting
```

**When to use:** Default. Most PRs don't need multi-model consensus.

### Model Selection Rules

- **TRIVIAL / INLINE_ONLY** → No model (deterministic only)
- **STANDARD** → Pattern C (Authority-only)
- **ARCH** → Pattern A (Fast triage → Authority deep-dive) or Pattern C if triage already done
- **High-value repo** (riptide, hermes-webui) → Pattern B for critical PRs

---

## IV. Session Scoping

Each Hermes cron session must be **focused, self-contained, and single-purpose**.

### Session Contract

Every session receives:

```
INPUT (pre-gathered, injected as context):
  - PR metadata (number, title, author, SHA, files changed, LOC)
  - Diff summary (first 12k chars, full diff available via gh pr diff)
  - Graphify data (god nodes, communities, blast radius)
  - Pre-generated diagram URL (if available)
  - Review depth classification (TRIVIAL/INLINE_ONLY/STANDARD/ARCH)

OUTPUT (structured, deterministic consumption):
  - findings[] JSON array posted to /tmp/findings.json
  - Then run: python -m riptide.assemble_review --findings /tmp/findings.json ...

CONSTRAINTS:
  - Max 3 inline comments
  - Real issues only (no padding, no invention)
  - Must validate line numbers against diff hunks
  - Use --body-file for summary (never --body with markdown)
```

### Anti-Patterns (NEVER)

- ❌ Session spawns another session (recursion)
- ❌ Session modifies the repo (no git push from review session)
- ❌ Session waits for user input (cron is fire-and-forget)
- ❌ Session reads the same data twice (pre-gathered is sufficient)
- ❌ Session generates Excalidraw (deterministic pre-generation only)
- ❌ Session loads all skills upfront (load conditionally via skill_view)

### Focused Session Types

| Session type | Responsibility | Output |
|---|---|---|
| **Review session** | Analyze code, find issues | `findings[]` JSON |
| **Assembly session** | Format and post review | GitHub comment |
| **Triage session** | Classify depth, decide model | Depth enum + model selection |

Currently, Review + Assembly happen in the **same session** (the LLM generates findings, then runs `assemble_review`). Triage happens **before** spawn (deterministic classification in Python).

---

## V. Prompt Contracts

The prompt injected into each Hermes session follows a strict contract.

### Structure

```
┌─────────────────────────────────────────────────────────┐
│  SYSTEM CONTEXT (from --skill riptide-review)           │
│  → This document's relevant sections                    │
├─────────────────────────────────────────────────────────┤
│  PRE-GATHERED DATA BLOCK (Python → prompt)              │
│  → PR metadata, diff summary, graphify, diagram URL     │
├─────────────────────────────────────────────────────────┤
│  TASK INSTRUCTION (specific to this PR)                 │
│  → What to analyze, what to look for                   │
├─────────────────────────────────────────────────────────┤
│  OUTPUT CONTRACT (what to produce)                      │
│  → JSON schema, constraints, next steps                │
└─────────────────────────────────────────────────────────┘
```

### Example Prompt

```markdown
## Pre-Gathered Context

PR #42 in ChonSong/riptide — 200 LOC changed
Title: feat: add autonomous review pipeline
Author: ChonSong
HEAD SHA: abc123def456

### Files Changed
- riptide/deepthink.py (+150/-30)
- riptide/review_pipeline.py (+80/-10)
- riptide/tests/test_deepthink.py (+40/-0)

### Diff Summary
````
[first 12k chars of diff]
````

### Graphify Analysis
God Nodes:
- _spawn_deepthink() (25 edges)
- T0Orchestrator (20 edges)

### Pre-generated Diagram
[View Diagram](https://excalidraw.com/#json=abc123)

## Your Task

Analyze this PR for code quality, correctness, and design issues.
You are a senior engineer. Focus on real issues only.

### Output

Write findings to /tmp/findings.json as:
```json
[
  {
    "severity": "warning",
    "title": "Short title",
    "detail": "Detailed explanation with suggestion",
    "file": "riptide/deepthink.py",
    "line": 42
  }
]
```

Then run:
```bash
python -m riptide.assemble_review \
  --findings /tmp/findings.json \
  --owner ChonSong --repo riptide --pr 42 \
  --diagram-url "https://excalidraw.com/#json=abc123"
```

### Constraints

- Max 3 inline comments
- Only flag real issues (no padding)
- Verify line numbers fall within diff hunks
- Use `gh api ... --input /tmp/comment.json` for complex markdown
- Use `-F line=N` (integer), never `-f line=N` (string → 422)
- If you have no critical/warning findings, say so explicitly
```

---

## VI. Pipeline Flow (End-to-End)

```
┌──────────────────────────────────────────────────────────────┐
│  STAGE 0: POLL (deterministic, Python)                        │
│                                                              │
│  deepthink.py run() → scan watched repos                      │
│  Filter: >100 LOC, >30min stale, owned repo or authored      │
│  Dedup: state file (24h cooldown, SHA-based)                  │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGE 1: PRE-PROCESSING (deterministic, Python)              │
│                                                              │
│  _gather_review_data() → diff, files, tree, graphify          │
│  classify_review_depth() → TRIVIAL/INLINE_ONLY/STANDARD/ARCH  │
│  select_skills(depth) → skill list (no skills for TRIVIAL)    │
│  pre_generate_diagram() → Excalidraw URL                      │
│  build_orchestrator_prompt() → self-contained prompt          │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGE 2: TRIAGE (deterministic or Fast model)               │
│                                                              │
│  TRIVIAL → skip LLM, post auto-approve                       │
│  INLINE_ONLY → fast scan, post if issues found               │
│  STANDARD → spawn authority session                          │
│  ARCH → spawn authority session with brooks-lint              │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGE 3: REVIEW (LLM, Hermes cron session)                   │
│                                                              │
│  Load: --skill riptide-review (this document)                 │
│  Input: pre-gathered data block                               │
│  Task: analyze, find issues, write findings.json              │
│  Output: structured findings (max 3)                          │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGE 4: ASSEMBLY (deterministic, Python)                    │
│                                                              │
│  LLM runs: python -m riptide.assemble_review ...              │
│  → validates findings structure                               │
│  → assembles markdown from template                           │
│  → posts inline comments (validates line numbers)             │
│  → posts summary review (via --body-file)                     │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGE 5: POST-PROCESSING (deterministic, Python)             │
│                                                              │
│  Update state file (SHA + timestamp)                          │
│  Log results                                                  │
│  Clean up temp files                                          │
└──────────────────────────────────────────────────────────────┘
```

---

## VII. Reference: Inline Comment API

### Single-line comment

```bash
gh api repos/OWNER/REPO/pulls/PR/comments --method POST \
  -f body='**🟡 Warning:** description' \
  -f commit_id='FULL_SHA' \
  -f path='file.py' \
  -F line=42 \
  -f side='RIGHT'
```

### Multi-line comment

```bash
gh api repos/OWNER/REPO/pulls/PR/comments --method POST \
  -f body='...' \
  -f commit_id='SHA' \
  -f path='file.py' \
  -F line=199 \
  -F start_line=186 \
  -f side='RIGHT' \
  -f start_side='RIGHT'
```

### Complex markdown (backticks, suggestion blocks)

```bash
cat > /tmp/comment.json << 'EOF'
{
  "body": "**🔴 Critical:** `fn()` fails\n\n```suggestion\nfixed code\n```",
  "commit_id": "FULL_SHA",
  "path": "file.py",
  "line": 42,
  "side": "RIGHT"
}
EOF
gh api repos/OWNER/REPO/pulls/PR/comments --input /tmp/comment.json
```

### Critical Gotcha

`-F` for integers (line, start_line), `-f` for strings. Using `-f line=42` sends the string `"42"` → 422 error.

---

## VIII. Reference: Summary Review

```bash
cat > /tmp/review.md << 'GATEOF'
## 🎯 Summary
(1-2 sentences: what this PR does)

## 🔍 Findings
| Severity | File | Line | Issue |
|----------|------|------|-------|
| 🟡 Warning | file.py | 42 | Issue description |

## 📊 Code Analysis
- `file.py:10-25` — description of change and architectural reasoning

## 🔗 Diagram
[Visual Review Diagram](URL)

## 📌 Next Steps
(max 3 actionable items)

## 💭 Explanation
(Trade-offs considered, approach rationale)

---
<sub>Riptide Review via Hermes</sub>
GATEOF
gh pr comment PR --repo OWNER/REPO --body-file /tmp/review.md
```

---

## IX. Reference: Severity Convention

| Severity | Icon | When to use |
|---|---|---|
| Critical | 🔴 | Definite bug, security issue, data loss risk |
| Warning | 🟡 | Potential issue, performance concern, code smell |
| Suggestion | 🟣 | Style improvement, minor refactor, nitpick |
| Info | 🔵 | Educational note, no action required |
| Approved | 🟢 | Clean section, good pattern to highlight |

---

## X. Adding New Capabilities

When adding a new capability to the pipeline:

1. **Classify it** using Section II's framework
2. **Add it** to Section I's table
3. **If deterministic:** implement in Python, add tests
4. **If LLM:** define the prompt contract, add to session prompt
5. **If hybrid:** define both the LLM proposal format and deterministic validation
6. **Update this document** before deploying

---

*This document is the single source of truth for Bot 2's architecture. When in doubt, follow the classification in Section I.*
