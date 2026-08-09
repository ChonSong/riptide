# Bot Automation Patterns

<!-- Trigger: Building cron-polled PR bots (like Riptide's Bot 2 / Bot 3) -->

## Filter Chain

Structure per-PR check as a sequence of gates where any can short-circuit:

```
Draft? → SHA dedup? → Staleness? → Scope match? → Already posted? → TRIGGER
  ❌        ❌           ❌          ❌           ❌         ✅
```

**Standard gates (in order):**
1. **Draft PR** — skip; not ready
2. **SHA dedup** — same `head_sha` already processed; skip
3. **Staleness** — too fresh (author may push more); wait N min
4. **Scope match** — PR must touch relevant files
5. **Output dedup** — bot already posted its output
6. **Trigger** — all filters passed

## SHA Dedup vs 24h Cooldown

**24h cooldown is redundant** — SHA dedup already prevents re-processing same revision. Remove it to allow multiple runs across iterative commits.

**Pitfall:** Cooldown + SHA dedup together create stale-lock. If bot missed a commit (e.g., was down), it blocks re-processing even though SHA is new.

## Record State AFTER Success

```python
# Wrong — records before action, transient failure permanently skips PR
state[pr_key] = {"head_sha": sha, "reviewed_at": now()}
spawn_expensive_session()

# Right — records only after success
if spawn_expensive_session():
    state[pr_key] = {"head_sha": sha, "reviewed_at": now()}
```

Combine with retry loop (3 attempts, exponential backoff 5s/10s/20s).

## Cron Wrapper Script

```bash
#!/bin/bash
cd /home/sc/workspace/riptide
set -a; . ./.env; set +a
exec /home/sc/.hermes/hermes-agent/venv/bin/python3 riptide/deepthink.py
```

## Optional Config Principle

Make config files optional with sensible defaults:

- **Default:** `proofshot.config.json` absent → use `localhost:8788` with standard captures
- **Opt-in:** `proofshot.config.json` present → use custom URL, seed, capture sequence
