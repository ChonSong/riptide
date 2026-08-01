# Problem: Spawned Hermes Sessions Can't Report Their Runtime Model

## The Gap

When Bot 2 (`deepthink.py`) spawns a Hermes cron session for a PR review, the generated prompt tells the agent to sign off with a **fixed, model-agnostic footer**:

```python
# deepthink.py line 231
f"<sub>Riptide Review via Hermes</sub>\n"
```

The companion has a similar but inverted problem:

```python
# companion.py line 489
parts.append(f"... PR review via local Ollama ({self.model}) ...")
```

Companion hardcodes the **provider** (Ollama) but gets the **model** right. Deepthink gets **neither**.

Meanwhile, the Hermes session that actually runs the review uses whatever model the user's Hermes profile is configured with — which could be `big-pickle` via `custom:freellmapi`, `deepseek-v4-flash`, `gpt-4o`, or others. There is zero runtime introspection.

**Concrete failure observed:** The spawned `riptide-review-ChonSong-riptide-16` session ran under the same model as this session (deepseek-v4-flash via opencode-go), but the prompt carried a sign-off hinting at a different model. This is misleading for human readers who rely on the sign-off to gauge the review's nature.

## Why This Matters

1. **Truthful provenance** — A review produced by `deepseek-v4-flash` has different characteristics from one by `big-pickle` or `gpt-4o`. The sign-off is the only record of what system generated it.
2. **Debugging** — If a review is poor, knowing the model explains why and what to change.
3. **Determinism** — Hardcoded strings are a liar's contract. The bot should always truthfully report its own identity.

## Affected Code Paths

| Path | File | Line | Current |
|------|------|------|---------|
| Bot 2 cron spawn | `riptide/deepthink.py` | 231 | `<sub>Riptide Review via Hermes</sub>` — no model, no provider |
| Companion's own PR comments | `riptide/companion.py` | 489 | `PR review via local Ollama ({self.model})` — provider hardcoded |
| `@riptide-bot review` (PR #14) | `riptide/webhook.py` *(future)* | — | Will inherit deepthink's pattern unless fixed |

The companion's sign-off is *technically correct* — it does use Ollama and `self.model` reflects `RIPTIDE_COMPANION_MODEL`. But if the companion ever routes through Hermes instead of Ollama, it breaks too.

## Context Map (via graphify)

The graph shows three communities involved:

- **Companion** (`companion.py`) — owns `self.model` from env var, calls Ollama directly via `requests.post`. Self-contained model detection.
- **deepthink.py** — builds prompt strings, spawns `hermes cron create`. Has zero model-awareness because at the time of prompt construction the runtime model isn't known.
- **webhook.py** — routes `issue_comment` events (currently only `companion skip/resume`; PR #14 adds `@riptide-bot review`).

The key graph edge is: `deepthink._spawn_deepthink() → subprocess.run(hermes cron create…)`. The spawned process runs with full Hermes config but that config is opaque to the spawner and not referenced in the prompt.

## Solution Leads (Not Yet Evaluated)

### Lead A: Hermes-Side Environment Variables
**What:** Have Hermes Agent export `HERMES_ACTIVE_MODEL` and `HERMES_ACTIVE_PROVIDER` as env vars in every spawned session.

The spawned agent can read `os.environ.get("HERMES_ACTIVE_MODEL", "unknown")` and the prompt instructs it to include that in its sign-off.

**Pro:** Most deterministic — the truth comes from the runtime itself. Works for all spawned sessions, not just deepthink. No changes to deepthink.py's prompt logic — just add a templating instruction.

**Con:** Requires a change in the Hermes Agent codebase (outside this repo). The env var doesn't exist today.

### Lead B: Runtime Detection in Prompt Instructions
**What:** Instead of a fixed sign-off string, the prompt tells the spawned agent: *"Run `hermes config get model --json` to detect your active model and provider, then include them in your sign-off."*

The agent would execute this during its review and produce accurate sign-off.

**Pro:** Zero changes to Hermes Agent. Self-contained within this repo's prompt logic. Works with any Hermes version.

**Con:** Relies on the agent correctly executing the shell command and parsing the output. Adds a step to the prompt that could fail if `hermes` CLI is unavailable or the command changes. Less deterministic than env vars.

### Lead C: Spawner-Side Model Detection
**What:** Have `_spawn_deepthink()` run `hermes config get model --json` at spawn time and inject the result into the prompt string.

**Pro:** The spawner controls the prompt entirely. No burden on the spawned session.

**Con:** The spawner might be in a different Hermes profile/config than the spawned session (e.g., spawner runs under profile A, cron session under profile B). The detected model at spawn time is what the cron job *will* use, but if the agent defers execution, the config could differ. Also, `hermes config get` doesn't expose `provider` as a key — workaround needed.

### Lead D: Hybrid — Env Var + Prompt Fallback
**What:** Combine A and B. Add `HERMES_ACTIVE_MODEL` env var in Hermes (A), but also update deepthink's prompt to fall back to `hermes config get model` if the env var is absent (B). This is the belt-and-suspenders approach.

**Pro:** Works today (B) and becomes cleaner once Hermes ships the env var (A). Graceful degradation.

**Con:** Most lines changed across two codebases.

## Acceptance Criteria

A PR fixing this must:
1. Remove all hardcoded model/provider references from generated prompt strings
2. Ensure every review comment from Bot 1 (companion) and Bot 2 (deepthink) carries accurate `model` and `provider` in its sign-off
3. Handle the case where model detection fails gracefully (fallback to `"unknown"`, not empty)
4. The `@riptide-bot review` command (PR #14) must match the same behavior before it ships

## What Not to Do

- Do **not** re-parse a hardcoded model name from a config file — the whole point is that the runtime model is whatever `hermes` actually runs, not what a file says.
- Do **not** add a new env var in `.env` — that couples the bot's identity to its build-time config, which is the same class of bug as hardcoding.
- Do **not** rely on `HERMES_MODEL` as an env name — it conflicts with the user's request for Hermes metadata rather than ad-hoc variables.
