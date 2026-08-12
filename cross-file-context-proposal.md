# Cross-File Context Beyond Graphify

## Problem Statement

The Riptide review pipeline (PR #89) currently relies on graphify to provide codebase-wide context to the Hermes deep-think session. Graphify provides **structural reachability** — it identifies files connected to the changed files via AST-level relationships (imports, function calls, class hierarchy).

However, graphify misses several categories of code relationships that are critical for thorough review:

### What Graphify Catches (AST-level)

- `utils.py` imports from `models.py` → connected
- `api.py` calls `utils.helper()` → connected
- Class inheritance chains

### What Graphify Misses

| Category | Description | Example |
|----------|-------------|---------|
| **Shared data structures** | Changing a type in one file breaks consumers | `types.py` defines `UserDict`; `api.py` and `tasks.py` both import it, but graphify may not show both edges clearly |
| **Config/constant coupling** | Changing a constant affects all users | `config.py` defines `MAX_RETRIES=3`; 5 files read it via `from config import MAX_RETRIES` |
| **Interface/protocol coupling** | Multiple files implement the same interface | `backend.py` implements `StorageInterface`; `tests/mock.py` implements it too |
| **Schema migration coupling** | DB schema change requires code migration | Migration file + model file + query file all need review together |
| **Test-to-code relationships** | Test files map to source files | `test_api.py` ↔ `api.py` |
| **Temporal coupling** | Files that historically change together | `routes.py` + `views.py` + `serializers.py` in a REST refactor |
| **Cross-cutting concerns** | Auth, logging, error handling patterns | A PR adding auth middleware affects every route file |
| **String-based references** | Dynamic imports, reflection, registry patterns | `importlib.import_module(f"plugins.{name}")` |

### Why This Matters

A review session that lacks this context will:
- Miss cross-file consistency issues (interface not updated everywhere)
- Fail to flag breaking changes to shared types/constants
- Not recognize when a migration requires code changes
- Miss test coverage gaps for the changed files

## Proposed Approaches

The following approaches could be combined to provide richer context. This PR does not propose implementing any specific approach — it's a design discussion.

### Approach 1: Import Expansion

**Idea**: For each changed file, also fetch files that share imports (transitive closure of depth 2).

**How it works**:
1. Parse changed files for imports
2. Find all files that import the same modules
3. Include those files in the context bundle

**Pros**: Catches shared type/constant usage
**Cons**: May include too many files (overhead), doesn't catch string-based references

### Approach 2: Grep-Based Symbol Usage

**Idea**: For each new symbol introduced in the diff (function, class, constant), grep the repo for usages outside the changed files.

**How it works**:
1. Extract added function/class/constant names from diff
2. Run `grep -r "symbol_name" --include="*.py"` on the repo
3. Include matching files in context

**Pros**: Catches all references including dynamic ones (if symbol is distinctive)
**Cons**: False positives on common names, language-specific tooling needed

### Approach 3: Temporal Co-occurrence (Git History)

**Idea**: Use git history to identify files that historically change together.

**How it works**:
1. Query git log: "what other files appeared in commits with these files?"
2. Rank by co-occurrence frequency
3. Include top-N co-occurring files in context

**Pros**: Captures project-specific coupling patterns, language-agnostic
**Cons**: Historical coupling ≠ current coupling, may be stale

### Approach 4: Test-to-Source Mapping

**Idea**: Always include corresponding test files in the review context.

**How it works**:
1. For each changed source file, find its test file (convention-based: `test_X.py` ↔ `X.py`, `X_test.py` ↔ `X.py`)
2. Include the test file in the context bundle

**Pros**: Ensures reviewer sees test coverage
**Cons**: Not all projects follow naming conventions, some files have no tests

### Approach 5: Enhanced Graphify (Cross-Cutting Edge Types)

**Idea**: Extend graphify with additional edge types beyond AST imports.

**New edge types to consider**:
- `shared_type`: both files import/use the same type definition
- `shared_constant`: both files reference the same constant
- `implements_interface`: both files implement the same abstract class/protocol
- `test_of`: test file exercises source file
- `migrations_of`: migration file modifies schema used by model file

**Pros**: Builds on existing infrastructure, precise
**Cons**: Requires graphify modifications, may be complex to implement

## Open Questions

1. **Which approaches to prioritize?** — Probably start with the simplest (Approach 4: test mapping) and add complexity as needed.

2. **Context budget** — Hermes sessions have finite context windows. How many additional files can we include before quality degrades?

3. **False positive tolerance** — Including irrelevant files is worse than including none. What's the acceptable noise level?

4. **Language support** — Python is the primary target, but the approach should be extensible to TS/Go/Rust.

5. **When to compute** — Pre-gather in Python (like current graphify), or let the agent discover dynamically via tools?

## Success Criteria

A successful implementation would:
- Catch at least 80% of the missed categories above
- Add ≤20% overhead to context bundle size
- Not introduce significant latency to review spawning
- Work across Python, TypeScript, Go, and Rust codebases

## References

- PR #89: Rule-based classification + conditional skill loading (introduces the review pipeline)
- `riptide/review_pipeline.py`: `ReviewDepth` enum and `classify_review_depth()`
- `riptide/deepthink.py`: `_gather_review_data()` — current context gathering
