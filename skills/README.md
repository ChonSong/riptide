# Riptide Skills

This directory contains the Hermes agent skills that define Riptide's behavior. These skills are loaded by the Hermes agent when reviewing PRs and define:

- **riptide** — Overview, triggers, 3-bullet rule, configuration
- **deep-think** — Reasoning loop structure for complex analysis
- **github-pr-lifecycle** — PR workflow (create/edit/merge) and pitfalls
- **riptide-development** — Development principles and patterns

## Why Repo-Local?

The review prompt template (`riptide/deepthink.py`) references these skills via `skill_view()`. Keeping them in the repo ensures:

1. **Version control** — skill changes are tracked alongside code changes
2. **Deployability** — skills deploy with the bot, not separately
3. **Reproducibility** — any checkout of the repo has the correct skills

## Loading

Hermes loads skills from `~/.hermes/skills/`. To use these repo-local skills, symlink them:

```bash
ln -s /home/sc/workspace/riptide/skills/riptide ~/.hermes/skills/riptide
ln -s /home/sc/workspace/riptide/skills/deep-think ~/.hermes/skills/deep-think
ln -s /home/sc/workspace/riptide/skills/github-pr-lifecycle ~/.hermes/skills/github-pr-lifecycle
ln -s /home/sc/workspace/riptide/skills/riptide-development ~/.hermes/skills/riptide-development
```

Or configure Hermes to search this directory in addition to `~/.hermes/skills/`.
