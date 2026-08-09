# Inline PR Comments

<!-- Trigger: Posting inline review comments on specific lines -->

## API

```bash
gh api repos/O/R/pulls/N/comments \
  -f body='**🔴 Critical:** ...' \
  -f commit_id='<full-sha>' \
  -f path='file.py' \
  -F line=42 \
  -f side='RIGHT'
```

**Required fields:**

| Field | Type | Flag |
|-------|------|------|
| body | string | `-f body='...'` |
| commit_id | string (full SHA) | `-f commit_id='...'` |
| path | string | `-f path='...'` |
| line | **integer** | `-F line=N` |
| side | `RIGHT`/`LEFT` | `-f side='RIGHT'` |

**LEFT** = old file (deleted lines). **RIGHT** = new file (added/modified).

## Multi-line

Add `start_line` and `start_side`:

```bash
gh api repos/O/R/pulls/N/comments \
  -f body='...' -f commit_id='...' -f path='...' \
  -F line=199 -F start_line=186 -f side='RIGHT' -f start_side='RIGHT'
```

## Markdown Bodies

Shell mangles backticks, `$`, quotes. Use `--input` with JSON file:

```bash
cat > /tmp/comment.json << 'EOF'
{"body": "**🔴 Critical:** `code`\n\n```suggestion\nreplacement\n```", "commit_id": "...", "path": "...", "line": 42, "side": "RIGHT"}
EOF
gh api repos/O/R/pulls/N/comments --input /tmp/comment.json
```

## One-Click Apply Suggestions

Use ` ```suggestion ` fenced code block in comment body:

````markdown
**🟡 Warning:** Use actual suffix matching.

```suggestion
        suffix = Path(name).suffix
        action = by_suffix.get(suffix)
        return f"{action} via `{name}`" if action else f"Provides `{name}`"
```
````

**Pitfall:** Suggestion replaces entire line range (start_line to line), not just shown lines.

## Getting Line Numbers

```bash
# From diff
gh pr diff $N --repo O/R | grep -E "^@@" 

# From file (exact PR-head lines)
git fetch origin pull/$N/head:pr-$N
git show pr-$N:README.md | grep -n "search term"
```

**Pitfall:** `git show origin/main:file` returns BASE version, not PR version — wrong line numbers. Fetch PR head first.
