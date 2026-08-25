# Riptide — Complete Implementation Plan

**Date:** 2026-08-25
**Status:** Active — Worker 9 (CI Verifier) + Worker 10 (Cleanliness) open as PR #174
**Author:** Riptide Architecture Team

---

## Current Workstream: Fix Pipeline Reliability (PR #174)

PR #174 adds two new Conductor pipeline stages to make the fix command reliable and reviews thorough:

### Worker 9: CI Verifier (`riptide/pipeline/ci_verifier.py`)
- Polls `gh pr checks` after a fix push (30s interval, 10min timeout)
- Classifies failures: FIXABLE (test-required, agentlint) vs NON-FIXABLE (CodeRabbit, review-required, GitGuardian)
- Retries once for fixable failures, escalates non-fixable to human
- New 6-stage fix pipeline: probe → judge → artisan → engine → **ci_verifier** → scribe

### Worker 10: Cleanliness (`riptide/pipeline/cleanliness.py`)
- Evaluates 7 PR cleanliness signals during review:
  1. Merge conflicts (`gh pr view --json mergeable`)
  2. Related open PRs touching same files
  3. Test coverage (source-only changes without tests)
  4. PR description quality (body length, issue links)
  5. Commit hygiene (Conventional Commits compliance)
  6. PR staleness (age > 14/30 days)
  7. CI pre-check (existing failures before review)
- Produces severity-rated findings with actionable suggestions
- Calculates cleanliness score (0-100)
- Extended probe with `_gather_cleanliness_signals()` + 7 helpers

### Files Changed
- `riptide/pipeline/ci_verifier.py` (new — CIVerifier class)
- `riptide/pipeline/cleanliness.py` (new — Cleanliness class)
- `riptide/pipeline/conductor.py` (+ci_verifier/cleanliness dispatch, `create_fix_pipeline()`)
- `riptide/pipeline/roles.py` (+ci_verifier/cleanliness roles)
- `riptide/pipeline/probe.py` (+_gather_cleanliness_signals + 7 helpers)
- `riptide/fixer.py` (spawn Conductor fix pipeline, CI verification prompt)
- `riptide/tests/test_ci_verifier.py` (27 tests)
- `riptide/tests/test_cleanliness.py` (12 tests)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current State Assessment](#2-current-state-assessment)
3. [Architecture Design](#3-architecture-design)
4. [Worker Specifications](#4-worker-specifications)
5. [ADHD-Friendly Output Requirements](#5-adhd-friendly-output-requirements)
6. [Implementation Phases](#6-implementation-phases)
7. [Wiring & Integration Points](#7-wiring--integration-points)
8. [Open PRs & Merge Status](#8-open-prs--merge-status)
9. [Success Metrics](#9-success-metrics)
10. [Risks & Mitigations](#10-risks--mitigations)
11. [Immediate Next Steps](#11-immediate-next-steps)

---

## 1. Executive Summary

Riptide is evolving from 4 isolated bots into a coordinated multi-agent system with 8 specialized workers. This plan covers:

- **Worker 4: Diagram Analyst** — Annotated diagrams from agent findings
- **Worker 5: Test Oracle** — Targeted test execution from PR diff
- **Worker 6: Review Memory** — Institutional learning across reviews
- **Worker 7: Interaction Handler** — Unified command router + conversational interaction
- **Worker 8: Architecture Documentarian** — Living codebase map

Plus ADHD-friendly output formatting across all agents (informed by [i-have-adhd](https://github.com/ayghri/i-have-adhd) analysis).

**Timeline:** 3 weeks to full implementation
**Current progress:** Phase 1 in progress (Worker 7 code complete, Worker 4 code complete)

---

## 2. Current State Assessment

### Existing Workers

| Bot | Name | Trigger | Status | Issues |
|-----|------|---------|--------|--------|
| Bot 1 | Companion | Webhook (PR open) | ✅ Working | Verbose output, no action-first format |
| Bot 2 | Deepthink | Cron / `@riptide-bot review` | ✅ Working | Findings table exceeds 5 items, no time estimates |
| Bot 2b | Fixer | `@riptide-bot fix` | ✅ Working | Preamble-heavy, no numbered plan |
| Bot 3 | Proofshotter | Cron (10min) | ⚠️ Isolated | Broken manual route, no Bot 2 integration |

### Recent Merged Changes

| PR | Description | Status |
|----|-------------|--------|
| #130 | Remove duplicate auto-deploy + add tests | ✅ Merged |
| #136 | Wire diagram_url + triggered_at for timing | ✅ Merged |
| #127 | Docs: Fix Command section + env vars | ✅ Open (ready) |
| #128 | Longcat provider fix + ephemeral testing | ✅ Open (ready) |

### Critical Bugs Fixed

1. **Provider misrouting** — `custom` provider resolved to OpenRouter instead of LongCat
2. **Duplicate auto-deploy** — webhook triggered two systemd-run invocations on merge
3. **Timing metric** — review showed "since PR opened" instead of "since review requested"

---

## 3. Architecture Design

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         @riptide-bot review                             │
└─────────────────────────────────────┬───────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  WORKER 2: Deepthink (Hermes deep-think)                               │
│  - Read diff, graphify, deterministic analysis                         │
│  - Read Review Memory (Worker 6) for historical context                │
│  - Output: findings.json                                               │
└─────────────────────────────────────┬───────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
│  WORKER 4: Diagram   │ │  WORKER 5: Test      │ │  WORKER 7:           │
│  Analyst             │ │  Oracle              │ │  Interaction Handler │
│  - Generate annotated│ │  - Run targeted tests│ │  - Route commands    │
│    diagram           │ │  - Detect missing    │ │  - Handle follow-ups │
│  - Upload + annotate │ │    tests             │ │  - Status reporting  │
│  → diagram_insights  │ │  → test_report.json  │ │  → ack comment       │
└──────────┬───────────┘ └──────────┬───────────┘ └──────────┬───────────┘
           │                        │                        │
           └────────────────────────┼────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  WORKER 2 (return): Assemble + Post Review                             │
│  - Consume findings + diagram + test report                            │
│  - Post final review with embedded diagram                             │
└─────────────────────────────────────┬───────────────────────────────────┘
                                      │
                                      ▼ (on merge)
┌─────────────────────────────────────────────────────────────────────────┐
│  WORKER 6: Review Memory                                               │
│  - Store review outcome                                                │
│  - Update repo review profile                                          │
│                                                                       │
│  WORKER 8: Architecture Documentarian                                  │
│  - Update graphify with merged changes                                 │
│  - Generate changelog entry                                            │
└─────────────────────────────────────────────────────────────────────────┘
```

### Worker Inventory

| Worker | Name | Type | Trigger | Output |
|--------|------|------|---------|--------|
| Bot 1 | Companion | Existing | Webhook | TL;DR comment |
| Bot 2 | Deepthink | Existing | Cron / `@review` | findings.json |
| Bot 2b | Fixer | Existing | `@fix` | PR commit |
| Bot 3 | Proofshotter | Existing | Cron | GIF comment |
| **Worker 4** | **Diagram Analyst** | **New** | **After Bot 2** | **diagram_insights.json** |
| **Worker 5** | **Test Oracle** | **New** | **After Bot 2** | **test_report.json** |
| **Worker 6** | **Review Memory** | **New** | **Post-merge** | **updated profile** |
| **Worker 7** | **Interaction Handler** | **New** | **Any `@riptide-bot`** | **routed action** |
| **Worker 8** | **Architecture Documentarian** | **New** | **Post-merge** | **updated graphify** |
| **Worker 9** | **CI Verifier** | **New** | **After fix push** | **ci_result.json** |
| **Worker 10** | **Cleanliness** | **New** | **During review** | **cleanliness.json** |

---

## 4. Worker Specifications

### Worker 4: Diagram Analyst

**Purpose:** Generate annotated Excalidraw diagrams that communicate agent understanding.

**Input:** `findings.json` from Bot 2
**Output:** `diagram_insights.json` with diagram_url + annotations + narrative

**Key Components:**

| Component | File | Purpose |
|-----------|------|---------|
| Main entry | `diagram_analyst.py` | CLI + Hermes integration |
| Renderer | `grafiphy/excalidraw_renderer.py` | Excalidraw JSON generation |
| Enricher | `grafiphy/diagram_enricher.py` | Annotation overlay (Phase 2) |
| Upload | `upload_excalidraw()` | Upload to excalidraw.com |
| Skill | `skills/diagram_analyst.py` | Hermes skill definition |

**Output Schema:**
```json
{
  "version": 1,
  "pr_number": 42,
  "generated_at": "2026-08-17T10:30:00+00:00",
  "diagram_url": "https://excalidraw.com/#json=...",
  "annotations": [
    {
      "element_id": "callout_0",
      "type": "finding",
      "finding_idx": 0,
      "file": "webhook.py",
      "line": 344,
      "severity": "warning",
      "message": "Race condition detected"
    }
  ],
  "narrative": {
    "summary": "PR #42 by @ChonSong changes 150 LOC across 3 file(s)...",
    "confidence": 0.85,
    "gaps": ["Test coverage impact unknown"],
    "key_insights": ["Duplicate systemd-run invocation"]
  }
}
```

**Diagram Sections:**
1. Title (PR info + risk level)
2. Narrative (agent understanding)
3. Code Impact Map (files colored by severity)
4. Findings (callout boxes linked to code)
5. Connections (arrows showing relationships)
6. Confidence/Gaps (footer indicators)

---

### Worker 5: Test Oracle

**Purpose:** Run targeted tests based on PR diff and report results.

**Input:** PR diff + changed files
**Output:** `test_report.json`

**Key Features:**
- File → test mapping (which tests cover which files)
- Targeted test execution (not full suite)
- Missing test detection
- Visual regression via `tests/_layout_helpers.py`

**Output Schema:**
```json
{
  "tests_run": 23,
  "passed": 21,
  "failed": 2,
  "missing_tests": ["webhook.deploy.*"],
  "coverage_delta": {
    "before": 78.5,
    "after": 82.3
  },
  "visual_regressions": [],
  "duration_s": 45.2
}
```

---

### Worker 6: Review Memory

**Purpose:** Store review outcomes and inject historical context into future reviews.

**Storage:** SQLite table `review_memory` in StateStore

**Schema:**
```sql
CREATE TABLE review_memory (
  id TEXT PRIMARY KEY,
  pr_key TEXT NOT NULL,  -- owner/repo#number
  pr_number INTEGER,
  owner TEXT,
  repo TEXT,
  head_sha TEXT,
  findings_count INTEGER,
  critical_count INTEGER,
  warning_count INTEGER,
  verdict TEXT,  -- approved, needs_work, needs_review
  user_feedback INTEGER,  -- +1, -1, or NULL
  created_at TEXT,
  metadata TEXT  -- JSON blob
);

CREATE TABLE review_profiles (
  repo TEXT PRIMARY KEY,
  total_reviews INTEGER,
  common_findings TEXT,  -- JSON array of frequent issues
  last_review_at TEXT,
  updated_at TEXT
);
```

**Injection Prompt:**
```python
def get_memory_context(owner: str, repo: str) -> str:
    """Get historical review context for a repo."""
    state = StateStore()
    profile = state.get_review_profile(repo)
    if not profile:
        return ""
    
    common = json.loads(profile.get("common_findings", "[]"))
    if not common:
        return ""
    
    return f"""## Review History for {repo}
This repo has {profile['total_reviews']} previous reviews. Common patterns:
- {chr(10).join(f"- {c}" for c in common[:3])}

Focus your review on these recurring issues if relevant."""
```

---

### Worker 7: Interaction Handler

**Purpose:** Unified command router for all `@riptide-bot` commands.

**Commands:**

| Command | Description | Authorization |
|---------|-------------|---------------|
| `@riptide-bot review` | Trigger deep-think review | Anyone |
| `@riptide-bot fix [desc]` | Fix issues | Author/Owner only |
| `@riptide-bot proofshot` | Visual capture | Author/Owner only |
| `@riptide-bot explain <n>` | Explain finding #n | Anyone |
| `@riptide-bot diagram` | Generate diagram | Anyone |
| `@riptide-bot companion skip/resume` | Toggle Companion | Anyone |
| `@riptide-bot status` | Show bot status | Anyone |
| `@riptide-bot help` | Show help | Anyone |

**Code complete:** `riptide/interaction_handler.py` (467 lines)

---

### Worker 8: Architecture Documentarian

**Purpose:** Update graphify and changelog on PR merge.

**Trigger:** Post-merge webhook (`action: closed, merged: true`)

**Actions:**
1. Run `graphify update .` with merged commit data
2. Generate changelog entry from PR description + findings
3. Update `review_profiles` table

---

## 5. ADHD-Friendly Output Requirements

Informed by [i-have-adhd](https://github.com/ayghri/i-have-adhd) analysis. All agents MUST follow these 10 rules:

### The 10 Rules

| # | Rule | Implementation |
|---|------|----------------|
| 1 | **Lead with next action** | First line = verdict + action, not preamble |
| 2 | **Number multi-step tasks** | `1. Do X, 2. Do Y, 3. Do Z` |
| 3 | **End with one concrete next action** | `Next: <single action> (<time>)` |
| 4 | **Suppress tangents** | One issue at a time, offer rest as separate |
| 5 | **Restate state every turn** | `Pass 2 of 4: Complexity analysis...` |
| 6 | **Specific time estimates** | `~2 min`, not "a bit" |
| 7 | **Make wins visible** | `✅ No issues found. Ready to merge.` |
| 8 | **Matter-of-fact errors** | Cause + fix, no "Uh oh" |
| 9 | **Cap lists at 5 items** | Split remainder into `<details>` |
| 10 | **No preamble/recap/closers** | Start with answer, end when done |

### Agent-Specific Templates

**Deepthink Review Template:**
```markdown
{{critical_count}} critical, {{warning_count}} warning(s). Fix {{file:line}} first — {{reason}}.

## Critical ({{count}})
{{for each}}
{{index}}. **{{file}}:{{line}}** — {{issue}}. Fix: {{fix}}. ~{{time}}.
{{endfor}}

## Warnings ({{count}})
{{for each}}
{{index}}. **{{file}}:{{line}}** — {{issue}}. Fix: {{fix}}. ~{{time}}.
{{endfor}}

{{if diagram_url}}
[Architecture Diagram]({{diagram_url}})
{{endif}}

Next: {{single_action}} (~{{time_total}}).

<sub>Riptide Review · model: `{{model}}` · {{elapsed}}</sub>
```

**Companion TL;DR Template:**
```markdown
{{verdict_icon}} {{one_line_verdict}}

## What changed ({{file_count}})
{{for each}}
{{index}}. {{file}}: {{change}} ({{loc}} LOC)
{{endfor}}

{{if findings}}
## Findings ({{count}})
| | File | Issue | Fix | Time |
|---|------|-------|-----|------|
{{for each}}
| {{severity}} | `{{file}}:{{line}}` | {{issue}} | {{fix}} | ~{{time}} |
{{endfor}}
{{endif}}

Blast radius: {{radius}} code paths affected.

Next: {{single_action}}.

<sub>Riptide Companion · model: `{{model}}` · {{elapsed}}</sub>
```

**Fixer Template:**
```markdown
Fixing {{count}} findings in #{{pr}}. Estimated: {{total_time}}.

{{for each}}
{{index}}. `{{file}}:{{line}}` — {{issue}} (~{{time}})
{{endfor}}

{{if push_eligible}}
I'll push each fix as a separate commit. Watch: `gh pr checks --repo {{owner}}/{{repo}} {{pr}}`
{{else}}
Push not authorized (fork/foreign). I'll post patches as comments.
{{endif}}

Next: {{single_action}}.

<sub>Riptide Fix · model: `{{model}}`</sub>
```

**Proofshotter Template:**
```markdown
{{verdict}} UI changes in #{{pr}}.

![Visual comparison]({{gif_url}})

{{if issues_detected}}
⚠️ {{count}} visual regression(s) detected.
{{else}}
✅ No visual regressions. Matches baseline.
{{endif}}

Next: {{single_action}}.

<sub>Riptide Proofshot · {{elapsed}}</sub>
```

### Findings Cap Implementation

```python
# In assemble_review.py
MAX_VISIBLE_FINDINGS = 5

def _render_findings(findings: list) -> str:
    if len(findings) <= MAX_VISIBLE_FINDINGS:
        return _render_findings_table(findings)
    
    visible = findings[:MAX_VISIBLE_FINDINGS]
    remainder = findings[MAX_VISIBLE_FINDINGS:]
    
    parts = [_render_findings_table(visible)]
    parts.append(f"\n<details><summary>Additional findings ({len(remainder)})</summary>\n")
    parts.append(_render_findings_table(remainder, start_index=MAX_VISIBLE_FINDINGS))
    parts.append("</details>")
    return "\n".join(parts)
```

---

## 6. Implementation Phases

### Phase 1: Foundation (Days 1-2)

**Goal:** Deploy Interaction Handler + Diagram Analyst wiring

| Task | File | Effort | Status |
|------|------|--------|--------|
| Worker 7: Interaction Handler | `interaction_handler.py` | 4h | ✅ Code complete |
| Worker 7: webhook.py integration | `webhook.py` | 2h | ⏳ Pending |
| Worker 4: Diagram Analyst | `diagram_analyst.py` | 4h | ✅ Code complete |
| Worker 4: Skill definition | `skills/diagram_analyst.py` | 1h | ✅ Code complete |
| Worker 4: assemble_review integration | `assemble_review.py` | 2h | ⏳ Pending |
| Worker 4: deepthink.py spawn | `deepthink.py` | 2h | ⏳ Pending |
| Tests for all changes | `tests/` | 3h | ⏳ Pending |

**Deliverables:**
- `@riptide-bot help` shows all commands
- `@riptide-bot status` shows bot status
- Diagram Analyst generates diagram from findings.json
- Reviews include embedded diagram + insights

### Phase 2: Intelligence (Days 3-5)

**Goal:** Test Oracle + Review Memory

| Task | File | Effort | Status |
|------|------|--------|--------|
| Worker 5: Test Oracle main | `test_oracle.py` | 6h | ⏳ Pending |
| Worker 5: File→test mapping | `test_oracle.py` | 3h | ⏳ Pending |
| Worker 6: Review Memory schema | `state.py` | 2h | ⏳ Pending |
| Worker 6: Memory injection | `deepthink.py` | 2h | ⏳ Pending |
| Worker 6: Post-merge hook | `webhook.py` | 2h | ⏳ Pending |
| Worker 5: Hermes skill | `skills/test_oracle.py` | 1h | ⏳ Pending |
| Tests | `tests/` | 4h | ⏳ Pending |

**Deliverables:**
- Targeted test execution from PR diff
- Review memory stored after each review
- Future reviews reference historical patterns

### Phase 3: Evolution (Days 6-8)

**Goal:** Architecture Documentarian + Proofshotter integration

| Task | File | Effort | Status |
|------|------|--------|--------|
| Worker 8: Documentarian | `documentarian.py` | 3h | ⏳ Pending |
| Worker 8: Graphify update | `documentarian.py` | 2h | ⏳ Pending |
| Worker 3: Fix manual route | `webhook.py` | 1h | ⏳ Pending |
| Worker 3: State migration | `proofshotter.py` | 2h | ⏳ Pending |
| Worker 3: Before/after diff | `proofshotter.py` | 4h | ⏳ Pending |
| Tests | `tests/` | 3h | ⏳ Pending |

**Deliverables:**
- Graphify updated on PR merge
- Manual `@riptide-bot proofshot` works
- Before/after visual diffs in proofshot captures

### Phase 4: Polish (Days 9-10)

**Goal:** ADHD-friendly formatting + metrics

| Task | File | Effort | Status |
|------|------|--------|--------|
| Agent template updates | All agents | 4h | ⏳ Pending |
| Time estimates per finding | `assemble_review.py` | 2h | ⏳ Pending |
| Findings cap at 5 | `assemble_review.py` | 1h | ⏳ Pending |
| Metrics dashboard | `metrics.py` | 3h | ⏳ Pending |
| End-to-end testing | Manual | 4h | ⏳ Pending |

**Deliverables:**
- All agents use ADHD-friendly format
- Time estimates on all findings
- Metrics to track improvement

---

## 7. Wiring & Integration Points

### webhook.py Changes

```python
# Add to handle_comment()
from riptide.interaction_handler import handle_command, parse_legacy_visual_command

# In handle_issue_comment():
# Check for @riptide-bot commands first
response = handle_command(
    client, installation_id, owner, repo,
    pr_number, commenter, comment_body, comment_id,
)
if response:
    client.post_pr_comment(installation_id, owner, repo, pr_number, response)
    return

# Legacy visual command (redirect to proofshotter)
visual_desc = parse_legacy_visual_command(comment_body)
if visual_desc is not None:
    from riptide.proofshotter import handle_manual_command
    handle_manual_command(
        client, installation_id, owner, repo, pr_number, commenter, visual_desc
    )
    return
```

### deepthink.py Changes

```python
# In _build_orchestrator_prompt()
# Add Step 3: Diagram Analysis
diagram_step = """### Step 3: Diagram Analysis
Run the diagram analyst — it generates an annotated diagram from your findings.

```
python -m riptide.diagram_analyst \
  --findings /tmp/findings.json \
  --owner {owner} --repo {repo} --pr {pr_number} \
  --title "{title}" --author "{author}" --loc {total_loc} \
  --output /tmp/diagram_insights.json
```

The output includes:
- diagram_url — annotated Excalidraw diagram
- annotations — mapping of diagram elements to findings
- narrative — agent's human-readable understanding
"""

# In _spawn_deepthink()
# After Hermes completes, read diagram_insights.json
insights_path = f"/tmp/riptide-diagram-insights-{owner}-{repo}-{pr_number}.json"
if Path(insights_path).exists():
    diagram_insights = json.loads(Path(insights_path).read_text())
    body = assemble_review_body(
        findings=findings,
        diagram_url=diagram_insights["diagram_url"],
        diagram_insights=diagram_insights,
        ...
    )
```

### assemble_review.py Changes

```python
# Add diagram_insights parameter
def assemble_review_body(
    findings: list[dict],
    owner: str,
    repo: str,
    pr_number: int,
    diagram_url: Optional[str] = None,
    diagram_insights: Optional[dict] = None,  # NEW
    model: Optional[str] = None,
    provider: Optional[str] = None,
    pr_created_at: Optional[str] = None,
    triggered_at: Optional[str] = None,
) -> str:
    # ... existing code ...
    
    # Replace diagram section with insights
    parts.append("\n## 🔗 Diagram\n")
    if diagram_url:
        parts.append(f"[Visual Review Diagram]({diagram_url})")
        
        if diagram_insights:
            narrative = diagram_insights.get("narrative", {})
            if isinstance(narrative, dict):
                summary = narrative.get("summary", "")
            else:
                summary = narrative
            if summary:
                parts.append(f"\n**📊 Agent Understanding:** {summary}")
            
            confidence = narrative.get("confidence") if isinstance(narrative, dict) else None
            if confidence is not None:
                parts.append(f"\n**Confidence:** {int(confidence * 100)}%")
            
            gaps = narrative.get("gaps", []) if isinstance(narrative, dict) else []
            if gaps:
                parts.append(f"\n**Knowledge Gaps:** {', '.join(gaps)}")
    else:
        parts.append("(No diagram generated)")
```

### state.py Changes

```sql
-- New tables for Worker 6: Review Memory
CREATE TABLE IF NOT EXISTS review_memory (
  id TEXT PRIMARY KEY,
  pr_key TEXT NOT NULL,
  pr_number INTEGER,
  owner TEXT,
  repo TEXT,
  head_sha TEXT,
  findings_count INTEGER,
  critical_count INTEGER,
  warning_count INTEGER,
  verdict TEXT,
  user_feedback INTEGER,
  created_at TEXT,
  metadata TEXT
);

CREATE TABLE IF NOT EXISTS review_profiles (
  repo TEXT PRIMARY KEY,
  total_reviews INTEGER DEFAULT 0,
  common_findings TEXT DEFAULT '[]',
  last_review_at TEXT,
  updated_at TEXT
);
```

---

## 8. Open PRs & Merge Status

### Current Open PRs

| # | Title | Branch | Status | Gates | Action |
|---|-------|--------|--------|-------|--------|
| **174** | feat(ci-verifier): CI verification + cleanliness pipeline stages | `feat/ci-verifier-pipeline` | ✅ Review posted | ⏳ Pending | Ready for review |
| **172** | feat(queue): Huey task queue + state machine fix | `feat/queue-huey` | ✅ Review posted | ⏳ Pending | Behind #174 |
| **171** | fix(webhook): durable work queue with startup recovery | `fix/webhook-work-queue` | ✅ Review posted | ⏳ Pending | Behind #174 |

### Recently Merged

| # | Title | Description |
|---|-------|-------------|
| **136** | wire diagram_url + triggered_at | Timing metric fix |
| **130** | remove duplicate auto-deploy | Race condition fix |
| **173** | fix(grafiphy): pass repo_tree, file_tree to diagram | Diagram fix |

### Closed (Superseded)

| # | Title | Reason |
|---|-------|--------|
| **128** | longcat provider (original) | Superseded by #137 |
| **123** | docs simplify (original) | Superseded by #138 |
| **126** | inline review comments | Superseded by merge |
| **131-135** | various duplicates | Superseded by #136 |

### Draft PRs to Open

| Worker | Branch | Depends On |
|--------|--------|------------|
| Worker 7 | `worker/interaction-handler` | #137, #138 |
| Worker 4 | `worker/diagram-analyst` | #137, #138 |
| Skills + Wiring | `worker/skills-and-wiring` | Worker 7, Worker 4 |

---

## 9. Success Metrics

### Review Quality Metrics

| Metric | Current | Phase 1 Target | Phase 2 Target |
|--------|---------|----------------|----------------|
| Reviews with diagrams | 0% | 80% | 95% |
| Reviews with test results | 0% | 0% | 60% |
| Reviews referencing history | 0% | 0% | 40% |
| Manual command success rate | ~50% | 95% | 99% |
| User interaction (replies) | 0 | 10/PR | 20/PR |
| Findings per review (avg) | 7+ | ≤5 | ≤5 |
| Time estimates included | 0% | 80% | 95% |

### ADHD Compliance

| Rule | Current Compliance | Target |
|------|-------------------|--------|
| 1. Lead with next action | 0% | 100% |
| 2. Number multi-step tasks | 20% | 90% |
| 3. End with one next action | 10% | 100% |
| 4. Suppress tangents | 50% | 80% |
| 5. Restate state | 0% | 80% |
| 6. Specific time estimates | 0% | 90% |
| 7. Make wins visible | 30% | 90% |
| 8. Matter-of-fact errors | 90% | 95% |
| 9. Cap lists at 5 | 0% | 100% |
| 10. No preamble/recap/closer | 10% | 95% |

### System Health

| Metric | Current | Target |
|--------|---------|--------|
| Proofshotter success rate | ~68% | 90% |
| Bot state isolation | JSON files | All in SQLite |
| Cross-bot dedup | None | Full |
| Deploy race condition | Duplicate | Single |

---

## 10. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Hermes session timeout | Medium | Diagram generation fails | Fallback to deterministic rendering |
| Upload fails | Low | No diagram in review | Log error, continue without |
| State migration issues | Medium | Lost review history | Backup before migration, atomic writes |
| Findings.json schema mismatch | Low | Worker 4 crashes | Validate schema, fall back to base |
| Excalidraw rate limiting | Low | Upload fails | Cache locally, retry with backoff |
| ADHD format reduces detail | Medium | Less context in reviews | A/B test, user feedback |
| Test Oracle false positives | Medium | Noise in reviews | Confidence thresholds, learning |
| Interaction Handler routing bugs | Low | Wrong bot triggered | Comprehensive tests, logging |
| Proofshotter /tmp exhaustion | Medium | Disk full | Cleanup after processing |
| GitHub API rate limits | Low | Skipped operations | Retry with backoff, queue |

---

## 11. Immediate Next Steps

### Today

1. ✅ **Merge #138** — docs update, gates pass
2. ⏳ **Merge #137** — provider fix, gates flaky but review posted
3. ⏳ **Open draft PRs:**
   - Worker 7: Interaction Handler (`worker/interaction-handler`)
   - Worker 4: Diagram Analyst (`worker/diagram-analyst`)
   - Skills + Wiring (`worker/skills-and-wiring`)

### This Week

4. ⏳ **Wire Interaction Handler into webhook.py** — replace inline command parsing
5. ⏳ **Wire Diagram Analyst into deepthink.py** — spawn after findings
6. ⏳ **Update assemble_review.py** — accept diagram_insights parameter
7. ⏳ **Add ADHD-friendly templates** — cap findings at 5, add time estimates
8. ⏳ **Add tests** — unit + integration for all new workers

### Next Week

9. ⏳ **Implement Worker 5: Test Oracle** — targeted test execution
10. ⏳ **Implement Worker 6: Review Memory** — SQLite schema + injection
11. ⏳ **Implement Worker 8: Documentarian** — graphify updates on merge
12. ⏳ **Fix Proofshotter integration** — state migration, manual route

---

## Appendix A: File Structure

```
riptide/
├── webhook.py                     # Updated: Interaction Handler routing
├── companion.py                   # Updated: ADHD-friendly templates
├── deepthink.py                   # Updated: Diagram Analyst spawn
├── fixer.py                       # Updated: ADHD-friendly templates
├── proofshotter.py                # Updated: State migration, before/after
├── assemble_review.py             # Updated: diagram_insights parameter
├── state.py                       # Updated: review_memory tables
├── diagram_analyst.py             # NEW: Worker 4
├── interaction_handler.py         # NEW: Worker 7
├── test_oracle.py                 # NEW: Worker 5
├── documentarian.py               # NEW: Worker 8
├── grafiphy/
│   ├── orchestrator.py            # Updated: spawn_diagram_analyst()
│   ├── excalidraw_renderer.py     # Updated: annotation support
│   └── diagram_enricher.py        # NEW: annotation overlay
├── skills/
│   ├── diagram_analyst.py         # NEW: Hermes skill
│   ├── test_oracle.py             # NEW: Hermes skill
│   └── interaction_handler.py     # NEW: Hermes skill
├── scripts/
│   ├── deploy.sh                  # Unchanged
│   └── ephemeral-test.sh          # From #137
├── .dockerignore                  # From #137
└── tests/
    ├── test_diagram_analyst.py    # NEW
    ├── test_interaction_handler.py # NEW
    ├── test_test_oracle.py        # NEW
    ├── test_review_memory.py      # NEW
    └── test_adhd_formatting.py    # NEW
```

---

## Appendix B: Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `RIPTIDE_DEEPTHINK_MODEL` | `LongCat-2.0` | Model for deep-think sessions |
| `RIPTIDE_DEEPTHINK_PROVIDER` | `longcat` | Provider for deep-think |
| `RIPTIDE_FIX_MODEL` | `LongCat-2.0` | Model for fix sessions |
| `RIPTIDE_FIX_PROVIDER` | `longcat` | Provider for fix |
| `RIPTIDE_WORKSPACE_ROOT` | `/home/sc/workspace` | Spawned session PYTHONPATH |
| `RIPTIDE_OUR_USERNAME` | `ChonSong` | GitHub username for auth |
| `RIPTIDE_OUR_ORG` | `ChonSong` | GitHub org for ownership |
| `RIPTIDE_WATCHED_REPOS` | (list) | Comma-separated repos to poll |
| `RIPTIDE_DEPLOY_BRANCH` | `main` | Branch that triggers auto-deploy |
| `RIPTIDE_PROOFSHOT_TIMEOUT` | `180` | Proofshot watchdog timeout (s) |
| `RIPTIDE_INTERACTION_COOLDOWN` | `300` | Command cooldown (s) |

---

## Appendix C: Database Schema

```sql
-- Existing tables (unchanged)
CREATE TABLE deliveries (
  delivery_id TEXT PRIMARY KEY,
  pr_number INTEGER,
  action TEXT,
  processed_at TEXT
);

CREATE TABLE pr_heuristics (
  pr_key TEXT PRIMARY KEY,
  head_sha TEXT,
  reviewed_at TEXT,
  skip INTEGER DEFAULT 0
);

CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  pr_number INTEGER,
  tier TEXT,
  status TEXT,
  created_at TEXT,
  completed_at TEXT
);

-- New: Worker 6 Review Memory
CREATE TABLE review_memory (
  id TEXT PRIMARY KEY,
  pr_key TEXT NOT NULL,
  pr_number INTEGER,
  owner TEXT,
  repo TEXT,
  head_sha TEXT,
  findings_count INTEGER,
  critical_count INTEGER,
  warning_count INTEGER,
  verdict TEXT,
  user_feedback INTEGER,
  created_at TEXT,
  metadata TEXT
);

CREATE TABLE review_profiles (
  repo TEXT PRIMARY KEY,
  total_reviews INTEGER DEFAULT 0,
  common_findings TEXT DEFAULT '[]',
  last_review_at TEXT,
  updated_at TEXT
);

-- New: Worker 4 Diagram Insights
CREATE TABLE diagram_insights (
  id TEXT PRIMARY KEY,
  pr_number INTEGER,
  owner TEXT,
  repo TEXT,
  diagram_url TEXT,
  narrative TEXT,
  confidence REAL,
  gaps TEXT,  -- JSON array
  annotations TEXT,  -- JSON array
  created_at TEXT
);
```

---

*Generated from analysis of Riptide codebase + competitor research + i-have-adhd principles*
*Last updated: 2026-08-18*
