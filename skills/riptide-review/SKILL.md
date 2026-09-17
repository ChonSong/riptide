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

## XI. Running a Review Track (Conductor) — Verification Protocol

A scheduled review session is handed a track id and told to run
`Conductor("riptide-review-<owner>-<repo>-<pr>").run()`. The Scribe posts the
review, so the session's job is **to make sure what gets posted is true**, not to
forward the pipeline's verdict.

### Cron has no approving user for delete-shaped commands

`rm -f <file>` (and `rm -rf`) stall the session at an approval prompt that nobody will
answer, so the prompt file the cron job is told to delete survives the run. Delete it with
`python3 -c "import os; os.remove(p)"` instead. `git worktree remove --force <path>` is not
blocked and cleans up a review worktree fine.

### A Phase-1 "finding" can be a false positive — refute it in the posted review

On a deliberately-flawed probe PR the deterministic Phase-1 comment claimed "command
injection risk: subprocess with shell=True" while the real defect (a swallowed exception
leaving a variable unbound) went unflagged. Read the actual call site before forwarding any
security claim: `shell=True` on a **string literal** with no interpolation and no argument
reaching it is not injectable. Post the refutation as one `info` finding (it renders outside
the 🔴/🟡 table, so it cannot trip the gate) rather than silently dropping it — the claim is
already public on the PR.

### The head can move while you work

The author amends commits mid-session (observed: two amends within five minutes,
both correcting a review that had just been posted). Before finalizing,
`git fetch && git log -1 --format=%H` the branch, re-locate every finding against
the new head by code context, and rewrite the review. Never leave a review
standing on a stale SHA, and never re-report a finding the new head already
fixed.

### Seed the Scribe with validated findings

`Judge.evaluate()` is deterministic-only and narrow: `_is_in_focus()` keeps only
messages mentioning imports / dead code / bare-except, so complexity and security
warnings never reach the Scribe, and any PR the StateStore remembers returns
`{"findings": [], "already_reviewed": True}` — left alone, `_resolve_findings`
then *refuses* to post (`{"posted": false, "error": "judge reported already_reviewed
with no findings"}`), so a spawned review can finish with no review at all. Read
`workstreams["ws-5-scribe"]["outputs"]` after every run: `posted: true` is the only
proof a review landed. The `pr_heuristics.reviewed_at` marker that triggers this is
written by `_spawn_deepthink` on a successful *spawn*, so it is not evidence that a
review was ever posted (the poller re-triggers on that mismatch; the probe does not). Reproduce every claim by executing code, then write
the vetted list into `workstreams["ws-5-scribe"]["inputs"]["findings"]` in
work-state before dispatch (`read_state()` → mutate → `write_state()`; there is no
`update_workstream(inputs=…)`).

### Verifying by running: build the venv first

No venv on this host has pytest (`~/.venv`, `~/venvs/*`, system python all lack it), so
"run the tests" needs a throwaway one first:

```bash
uv venv /tmp/rip197/venv --python 3.11
VIRTUAL_ENV=/tmp/rip197/venv uv pip install pytest requests httpx fastapi \
  pydantic cryptography structlog tenacity prometheus-client
```

Then `cd <worktree> && RIPTIDE_WORK_STATE=/tmp/... python -m pytest riptide/tests -q -p no:cacheprovider`.
Use `-o faulthandler_timeout=20` to get a stack trace of a hang (pytest-timeout is NOT installed
and is not in requirements/pyproject, so the outer `timeout` is the only bound). Expect
`test_webhook.py` to stall ~20-40s inside `state.py:store_review_outcome`; a whole-suite run can
take 6+ minutes in one worktree and 2 minutes in another on the same machine — ambient-state
noise, not a regression. Create worktrees with `git worktree add -f /tmp/wt-head <sha>` and remove
them with `git worktree remove --force` (plain `rm -rf` is blocked in cron).

### Verify by running, not by reading

- Run `pytest -q riptide/tests/test_conductor_integration.py riptide/tests/test_review_pipeline_wiring.py`.
  CI (`test-required`) only checks that feat/fix commits touch a test file — no
  workflow runs pytest, so a NameError in the spawn path ships green.
  `_spawn_deepthink` is a favourite failure: passing `repo_name` where the
  parameter is `repo` aborts both the poller and `@riptide-bot review` before any
  reservation is made.
- Watch each workstream's `output_protocol["path"]`. A `pr-0`/`track-` path means
  that workstream's inputs lack `pr_number`, and concurrent reviews overwrite each
  other's findings file.
- Only `probe`/`judge`/`warden`/`ci_verifier` publish a file; role-aware
  verification decides success for the rest, so a "failed" artisan/engine/scribe is
  not automatically a problem (the engine's `upload_excalidraw` does not exist and
  always exits 127).
- Read the post back with `gh api repos/O/R/issues/comments/{id}` (the per-comment
  endpoint takes no issue number), then check the gate's view: the last comment
  matching `## 🔍 Findings`, `## 🎯 Summary`, `Riptide Review ·` or
  `## Riptide Pass:` decides whether a follow-up commit is required. A findings
  review therefore blocks the merge until the author pushes again — say so in the
  report instead of suppressing findings to keep the check green.
- `gh pr comment` posts as the authenticated user (ChonSong), not
  `riptide-review[bot]`, and the Scribe's model attribution comes from the
  Conductor's brief, which no pipeline builder currently sets.

- **Re-run the gate after posting** (`gh run rerun <id>`) to confirm the check picks your review up. The workflow never re-runs on comments, so the check stays red with "No Riptide review found" until a commit is pushed; a rerun distinguishes that from "Review has findings…". Read the verdict with `gh api repos/O/R/actions/jobs/<job_id>/logs` — `gh run view --log-failed` prints nothing useful here. A review carrying 🟡 rows then reports `::error::Review has findings. At least one commit addressing them is required before merge.` — proof that the severity row, not the marker, is what blocks.
- **`Conductor.run()` returns `{"track", "results": [{"workstream", "status", "output"}]}`** — a list, not the track's `workstreams` dict. The Scribe's proof is at `results[i]["output"]["posted"]`; iterating `result["workstreams"]` silently prints nothing and reads as a clean run.
- **Clear the reservation after posting**: `StateStore().mark_complete("<track>-<sha12>-<rand>")` (id from the `jobs` table). Neither the Conductor nor the prompt does it, so the `pending` row blocks the next `@riptide-bot review` for the 2h TTL.
- **Audit-hook noise kills the signal**: filter the interpreter tree (`/home/sc/.local/share/uv/`) and aggregate hits by *path shape* instead of reading the dump — a full suite emits ~9k hits inside the session temp root (per-test `apply()` mkdir/open) while the finding is the handful outside it. The `riptide-pr<N>-pre-*` dirs appear as `os.mkdir`+`shutil.rmtree` pairs, i.e. cleaned, not leaked.

### Verifying a "hermetic test suite" PR

Never quote the PR body's failure count or its audit-probe table — measure both.

- Two worktrees, one interpreter: `git worktree add -f /tmp/wtNNN-base <base-sha>` and
  `-f /tmp/wtNNN-head <head-sha>`, then a throwaway venv (the recipe above). Run
  `pytest riptide/tests -q -p no:cacheprovider --tb=no -rf` in each, extract
  `^(FAILED|ERROR)`, strip the ` - …` suffix, sort, diff. A pre-fix count is only
  comparable if the interpreter and ambient state match.
- Separate *masked* from *introduced*: point the base worktree at an EMPTY work-state
  (`RIPTIDE_WORK_STATE=/tmp/empty.json`). If that failure set equals the head's, the
  change removed ambient masking and introduced nothing. (riptide #198: base with the
  developer's work-state 26 failed / with an empty one 30 == head 30, the 4-test delta
  being `TestCreateWorkstream` x3 and `TestWorkStateThreadSafety::test_concurrent_writes`,
  which only passed because the live work-state already held `test-track` / `track-0..4`,
  so `create_workstream()` did not `KeyError`.)
- Verify "zero ambient access" with your own audit hook, not the PR's table: a `-p` plugin
  that calls `sys.addaudithook` for `open`/`os.mkdir`/`sqlite3.connect`/`sqlite3.connect`
  and dumps hits at `pytest_sessionfinish`, matching paths by exact prefix (a `/tmp/riptide`
  substring also matches `/tmp/riptide-tests-*`). Residuals this found at #198: 7 reads of
  the developer's real repo via `os.environ.get("GRAPHIFY_CWD", f"/home/sc/workspace/{repo}")`
  (`grafiphy/orchestrator.py:104` — no RIPTIDE_* var reaches it) and 2 `os.mkdir('/tmp/riptide')`
  from the companion tests. **Re-measured when this work was re-landed on main (PR #203): the same 7 reads of `/home/sc/workspace/riptide/graphify-out/graph.json` persist** — the landed suite never sets `GRAPHIFY_CWD`, so "the failure set no longer depends on ambient state" holds only for the constants in `_TEMP_VALUES`, and the inventory's claim that `RIPTIDE_WORKSPACE_ROOT` covers the workspace roots is wrong. An audit hook is per-process: it must be installed before the
  suite imports riptide, and the dumped file is your evidence — keep it.
- A guard that claims to "fail loudly" can fail open: `getattr(module, attr, None)` +
  `continue` treats a renamed constant as compliant. Prove it by `delattr`-ing one watched
  attribute and re-running the guard — that is a reportable finding, not a nitpick.
- Roots created at import leak: count `/tmp/riptide-tests-*` before and after, including
  after `pytest --collect-only` and after a bare `import riptide.tests` with no pytest.

### Running the Conductor needs an interpreter that has the runtime deps

`python` on PATH is the Hermes venv and does **not** have `tenacity`, so
`Probe.gather()` → `import riptide.state` dies with `ModuleNotFoundError: tenacity`
after `ws-1-probe` has already been marked `in_progress`. Dispatch with the same
throwaway venv you built for pytest (`<venv>/bin/python`), cwd at the repo root so
the Scribe's `subprocess` call to `python -m riptide.assemble_review` resolves (that
child uses PATH's python and only needs stdlib + `gh`).

### A crashed dispatch leaves a workstream that is never retried

`next_pending_workstream()` returns only `status == "pending"`, so a workstream left
`in_progress` by an aborted dispatch is silently skipped on re-dispatch: the probe
never re-runs, the judge then reports `judge: context file missing`, and `run()`
carries on anyway (it breaks on `blocked`, never on `failed`) into
artisan/engine/scribe. `detect_stall`/`recover` are imported by `conductor.py` but
never called, so nothing recovers the stuck workstream. Posting still succeeded only
because the Scribe was seeded; unseeded, `_resolve_findings` refuses with "neither
findings nor findings_path available" — the correct failure mode. After any
interrupted run, reset that workstream by hand (`read_state` → set status →
`write_state`), or the track stays wedged.

### Traps that only show up when you run it

- **The gate matches the `<sub>Riptide Review · …</sub>` sign-off, not the `## Review:` header.**
  `riptide-review-required.yml` selects comments containing `## 🔍 Findings`, `## 🎯 Summary`,
  `Riptide Review ·` or `## Riptide Pass:`, and explicitly **skips the Companion's
  `## ✨ Review Required` complexity pre-pass** (it posts first and carries 🟡 rows, so counting
  it as a review reddens clean PRs). `## Review:` is not in the selector (removed in f7a920a),
  and the loose `critical`+`warning` clause was removed too — both are now locked by
  `riptide/tests/test_review_gate_workflow.py`. Reproduce by running the workflow's own `--jq`
  selector over a candidate body; a findings body without the sign-off is invisible to the gate.
  `docs/REVIEW-CONTRACT.md` §2 documents this now — but verify against the workflow, not the doc.
- **The pre-pass skip is a body-substring test, so a review that quotes that heading is skipped too.**
  The selector drops any comment whose body `contains()` the Companion's pre-pass heading — not only
  the pre-pass itself. A review *about* the selector that reproduces the heading verbatim is invisible
  to the gate: on #206 a 🟡 review (header + table + sign-off, posted 23:57:09) was excluded, the rerun
  reported `Review is clean — no follow-up commit required.`, and deleting only that one `select` clause
  from the same comment list selected the review with `HAS_FINDINGS=true`. Once the review drops out, the
  gate reads whatever else matches — usually the Companion's `## Riptide Pass:`, which reports clean.
  So never reproduce the pre-pass heading as a contiguous string in a review body; name it in words,
  and confirm the verdict from the job log rather than the check colour.
- **Only a 🔴/🟡 severity-table row blocks the merge.** The gate's `HAS_FINDINGS` is
  `test("\\|[\\s]*🔴|\\|[\\s]*🟡")`, and `_build_severity_table` emits no table when there are no
  critical/warning findings — so a review carrying only 🟣 suggestions / 🔵 info reports
  "Review is clean — no follow-up commit required" and the check goes green (verified by rerunning
  the gate over the posted body). The §XI line "a findings review therefore blocks the merge" is
  too strong: pick the severity, and the gate follows.
- **The check only turns green on a rerun or a push.** `gh run rerun <run-id>` re-evaluates the
  current comments; a gate that ran *before* the review was posted (observed: 11s earlier) stays red
  with "No Riptide review found" until then. Confirm the verdict from the job log line
  `Review is clean — no follow-up commit required.`/`::error…`, not from the conclusion alone.
- **Pre-flight the diff against the PR's base SHA, not local `origin/master`.** A stale local
  master turns an 18-file docs PR into a 128-file "ghost diff". Use
  `gh api repos/O/R/pulls/N --jq .base.sha` and `gh pr diff`.
- **`create_fix_pipeline` is not live code (at 797d694).** No production caller exists — `fixer._spawn_fix`
  spawns a Hermes session from `_build_fix_prompt` and never touches the Conductor — and dispatching a fix
  track crashes at `ws-3-artisan` (`KeyError: 'path'`: the builder passes `gh` file payloads while
  `_run_artisan` indexes `path`/`content`), with `post_fix_summary` unhandled by `_run_scribe`. Review it as
  dead code held up by a signature-only test, not as a working pipeline. Same shape in `fixer.py`:
  `process_fix_queue` (the only drainer of the queue) has no caller, so queued fixes never start.
- **Measure the test baseline, don't quote the doc.** Compare the failure *set* at the PR head
  against the failure set at the base SHA (`git worktree add` the base, run the documented
  command in both) — AGENTS.md's "~53 failures" note was ~2x the measured 28 and named a
  module that did not fail at all.
- Seed the Scribe even for docs-only PRs. Decide the findings yourself from executed code
  and write them to `workstreams["ws-5-scribe"]["inputs"]["findings"]`; the deterministic judge
  returns `findings_count: 0` (it only surfaces import/dead-code/bare-except hits, and reports
  `already_reviewed` for PRs the StateStore remembers). Evidence that seeding is what made the
  review real: on a test-only PR the judge returned `{"findings_count": 0, "already_reviewed": true}`
  while the 3 seeded findings are what appeared in the posted comment.
- Ambient state can mask a whole fix: for a work_state PR, run the comparison *both* ways. On #196 the failure set with the developer's real work-state was identical at base and head (26 = 26, `RIPTIDE_WORK_STATE` copy), while forcing `{"version": 1, "tracks": {}}` showed 30 → 26 — the 4 fixed tests (`test_pipeline.py::TestCreateWorkstream` x3 + `TestWorkStateThreadSafety::test_concurrent_writes`) pass at base only because the live file already holds `test-track` / `track-0..4`. A masked fix reports as "no change" under ambient state; say which run produced the number you quote.
- Count failures as *sets*, never as counts. Two back-to-back full-suite runs of the same worktree
  gave 32 and 30 failures (base) — `test_deploy.py::TestDeployLock` / `TestDeployNoWait` flicker in
  both directions and pass 3/3 in isolation — so a single sample cannot support "31 → 26". Diff the
  FAILED node IDs of two runs on each side and report the intersection as stable.
- **Run the Conductor with the throwaway venv's `python` FIRST on PATH.** `Scribe.post_review_with_assembler`
  shells out to bare `python -m riptide.assemble_review` (scribe.py:149), so PATH decides whether the post
  uses the venv or a system interpreter without the deps; run from the head worktree so `riptide` resolves
  from the PR's own code. The Conductor is synchronous and returns each workstream's `outputs` — `posted:
  true` plus reading the comment back is the only proof a review landed.
- **`state.py` migration restores are gated, so check reachability before believing one fixes anything.**
  `_migrate_poller_comments()` only runs under `if version < 2`, and a `StateStore` whose `pr_heuristics`
  predates a column is only repaired by ALTERs guarded on `PRAGMA table_info` — so a restored body can be
  inert on the live DB (v9) while still being "restored". Seed a legacy poller DB plus a copy of the real
  `state.db` and assert the row did *not* appear, rather than trusting the docstring. The source path is
  also compiled differently from its writer: `state.POLLER_DB_PATH` hardcodes `~/.local/share/riptide` while
  `poller.py` builds `$RIPTIDE_DATA_DIR/metadata.db`, so set `RIPTIDE_DATA_DIR` in a scratch dir to show the
  migration silently no-ops (the existing tests monkeypatch the constant and cannot see it).

### Reviewing a `hermes-webui-extensions` PR

The extension repo ships its own deterministic CI — run it before reading any code, and run
it at the PR's base SHA too, or "is this red because of the PR?" stays a guess.

```bash
export PATH=/home/sc/node-v20.20.0-linux-x64/bin:$PATH   # v22.22.0; system node must be >= 22
cd /tmp/wt-head                                          # `git worktree add -f` the head, then the base
node scripts/validate-extensions.mjs      # per-entry schema; prints `ok <id>` / `fail <id>` + reasons
node scripts/test-extension-validator.mjs # aborts via assertValidResults when any entry is invalid
node scripts/scan-extension-safety.mjs    # secrets, dangerous JS, URL-literal vs permissions.network_external
node scripts/generate-registry.mjs --out dist/registry.json
```

- `gh` inside `~/workspace/hermes-webui-extensions` resolves to the **upstream** remote
  (`hermes-webui/hermes-webui-extensions`), so `gh pr view 6` dies with "Could not resolve to a
  PullRequest with the number of 6". Always pass `--repo ChonSong/hermes-webui-extensions`.
- The safety gate allowlists a URL literal only when `new URL(literal)` parses to a loopback
  hostname, so a template literal like `` `http://127.0.0.1:${port}${path}` `` always fails
  (`new URL` throws on the interpolated port) while the full-literal constant
  `'http://127.0.0.1:17900'` passes. Report that as the fix rather than "add an allowlist".
- `extension.json` `post_install.docs_url` must be an http(s) URL and `local_app_label` a
  non-empty string when present — `"#"` and `""` are what usually turn this CI red.
- The `python`/`python3` on PATH here *is* the Hermes venv (3.11) and it **does** have
  tenacity/requests/fastapi, so `Conductor.run()` works on it; keep a throwaway venv for pytest.
- Hands-on checks that pay: run a repo `timeout 900` Conductor dispatch in the background and
  poll it — a 5-workstream run exceeded the 600 s foreground cap. For the docker-tunnel-manager
  sidecar, start it against a **stub `docker` package** (`PYTHONPATH=<stub>` plus
  `DTM_SIDECAR_PORT=<scratch port>`) so POST prune/delete endpoints never touch the real daemon;
  the stub is also how you prove routing (`parts[1:4] == ["api","volumes"]` can never match a
  3-element slice, so `/api/volumes/<name>/delete`, `/api/containers/<id>/logs` and
  `/api/images/<id>/history` all 404 while the extension's JS calls them). For extension DOM
  logic, `jsdom` is already in `~/workspace/hermes-webui-extensions/node_modules`; a 40-line
  harness (turn inserted complete vs turn filled later vs card added inside the 100 ms window)
  turns "the MutationObserver looks fine" into a verdict.
- The deterministic bundle's `critical` XSS hits are recall-tuned regexes: `innerHTML = ident`
  and `innerHTML = html` both fire. Check each site (escape helper `t()` before innerHTML, hex
  `short_id` in attributes, static SVG constant) and say plainly which ones you refute — a
  review that only forwards them is noise.
- Draft and long-stale PRs reach the Conductor (nothing filters drafts), and re-stating a scope
  finding already posted at an unchanged head is worth exactly one short paragraph — pair it
  with the new verified blockers, never alone.

### The gate's comment selector (pass-shadowing is fixed as of #207's base)

Current selector, verbatim from the workflow's run log:
`sort_by(.created_at) as $all | (($all | map(select((.body | contains("## Riptide Pass:")) | not)) | last) // ($all | last))`
over comments containing `## 🔍 Findings` / `## 🎯 Summary` / `Riptide Review ·` / `## Riptide Pass:` —
it now prefers the newest **non-pass** comment, so the Companion's pass no longer shadows a
findings review. On older bases the selector was plain `sort_by(.created_at) | last`, where it did
(see the #204 history below). `companion.py` posts
`## Riptide Pass: ✅ No findings` whenever its **deterministic** analysis has nothing actionable
(observed on #204 at 23:23:10, 41 s after a 🔴 review at 23:22:29) — so a companion delta review
landing later silently greens the gate over your findings. Confirm the gate's own verdict rather
than the check colour: `gh run rerun <riptide-review-required run>`, then read the job log for
`::error::Review has findings…` (your review counted) vs `::error::No Riptide review found…`
(nothing counted). Also note a push *after* your review's `created_at` satisfies the follow-up
requirement, so a review + the author's next commit turns the gate green without addressing it.

### The head can move mid-review — re-run the whole Conductor at the new SHA

Reset every workstream of the track (`read_state` → `status="pending"`, `outputs={}` →
`write_state`), re-seed `ws-5-scribe.inputs.findings` with findings re-verified at the new head
(re-anchor every finding by code context and drop the ones the new head fixed), and dispatch from a
worktree of the new SHA. A full 5-workstream run is ~3 s. State in the second review which findings
the new head resolved, with the CI counts that prove it.

### Hermetic in HOME, not in PATH

Measure the suite twice: `PATH=<venv>/bin:/usr/bin:/bin` (no `hermes`) and the dev PATH. At #204
the only difference was `test_fixer.py::TestSpawnFix` (6 tests, `1168 passed, 10 skipped` vs
`1174 passed, 4 skipped`), because `fixer._is_cron_available()` is `shutil.which("hermes")` — the
conftest isolates state paths, never PATH, so a PATH-dependent test still reads green locally and
red (or skipped) in CI.

### Reviewing a "restore what a merge dropped" PR

- Diff the object *inventory*, not just the named fix:
  `git show <merge>^:riptide/state.py | grep -oE "CREATE (TABLE|INDEX) IF NOT EXISTS [a-z_]+" | sort -u`
  against the head file. On #207 the two tables came back, but five index CREATEs the same fa99b25
  merge dropped (`idx_deliveries_status`, `idx_processed_comments_pr_key`,
  `idx_processed_comments_spawned`, `idx_work_queue_kind_status`,
  `idx_work_queue_status_created_at`) are still absent from the source *and* from the live DB —
  perf-only, so a 🟣 suggestion, never a blocker.
- Prove the *repair* path, not just the fresh-DB path: init a scratch DB, `DROP TABLE review_memory`
  + `review_profiles`, re-instantiate `StateStore`, and confirm they return. The restored block is
  ungated (no `version < 7`), which is the whole reason it repairs a v9 DB — a gated restore would
  be inert while still reading as "restored".
- **`riptide/review_memory.py`'s wrappers hardcode `StateStore()`.** `store_review_outcome()` /
  `get_memory_context()` ignore every `RIPTIDE_*` env var, so an "exercise the production call path"
  script writes synthetic rows into the LIVE `~/.local/share/riptide/state.db` (observed:
  `OwnerA/repo-a#7`, `OwnerB/repo-b#8`). Call `StateStore(db_path).store_review_outcome(...)`, and if
  you did hit the live DB, delete those exact ids and re-read the count before finishing.
- An empty second baseline (`tests/baseline_failures.txt`) is the intended end state: any root-suite
  failure fails CI, and a stale entry just gets reported. Confirm CI-exactly — throwaway `uv venv` +
  `pip install -r requirements.txt pytest httpx`, then run the workflow's own command
  (`scripts/check_test_baseline.py --tests tests --baseline tests/baseline_failures.txt`): exit 0 at
  the head, and 24 failed / 58 passed at the base. The repo-root suite ships no conftest and is
  hermetic (82 passed at 10/10 runs, and with a scrubbed `HOME`).

---

*This document is the single source of truth for Bot 2's architecture. When in doubt, follow the classification in Section I.*
