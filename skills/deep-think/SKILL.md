---
name: deep-think
description: Loop-based structured reasoning — surface, explore, challenge, synthesize, converge. Use for complex analysis, architectural decisions, risk assessment, and any problem where a single-pass answer would be oversimplified.
---

# DeepThink — Loop-Based Reasoned Analysis

## Loop Structure

```
Loop 1 (Surface)    Initial hypothesis, fast answer
Loop 2 (Explore)    Counterevidence, alternatives, edge cases
Loop 3 (Challenge)  What could be wrong? What did I assume?
Loop 4 (Synthesize) Refined conclusion integrating all perspectives
Loop 5 (Validate)   Empirical test on running system — mandatory for runtime analysis
Loop N (Converge)   Stop when insight stabilizes
```

**Key principle:** Re-inject the original problem at every loop to prevent drift.

## When to Use

- Architectural decisions, multi-step reasoning, risk assessment
- Comparing competing approaches (Option A vs B vs C)
- System state analysis (software health, extension bugs, deployment status)

**Don't use for:** simple lookups, single-step tasks, execution-only work.

## Loop Steps

1. **Re-inject** the original problem in one sentence
2. **State** where reasoning stands after previous loop
3. **Generate** new reasoning (evidence, alternatives, challenges)
4. **Integrate** — confirm, contradict, or refine previous loops
5. **Validate** (mandatory for running systems) — dispatch parallel empirical tests
6. **Converge** — stop when insight stabilizes, no new contradictions

## Empirical Validation (Step 5)

For running systems, code-reading alone over-reports bugs ~5x. Always test:

- Dispatch parallel subagents (one per component) to write and run tests
- Collect results, classify: real bug / false negative / stale analysis / undercount
- Recalibrate conclusion to what the system actually does

## Anti-Patterns

1. **Don't loop for the sake of it** — converge when stable
2. **Don't contradict without evidence** — challenge with substance
3. **Don't drift from the original problem** — re-inject at every loop
4. **Don't hide uncertainty** — say "I don't know" when you don't
5. **Don't use DeepThink for quick answers** — respect user's time
6. **Don't trust analysis without empirical validation** — run the code
7. **Don't prescribe system prompt edits without verification** — search first

## Reasoning Depth

| Depth | Loops | When |
|-------|-------|------|
| Quick | 1-2 | Straightforward decisions |
| Standard | 3-4 | Most architectural choices |
| Deep | 5-6 | High-stakes, novel problems |
| Maximum | 7+ | Existential/hard problems |
