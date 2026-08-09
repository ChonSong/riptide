# Patch Tool Reformat Recovery

<!-- Trigger: patch tool reformats whole file on small edit -->

## Problem

The `patch` tool can reformat the entire file on a small edit (4-line fix → 59 lines of noise; PR #63: 483 lines for a ~20-line edit).

## Fix

```bash
# Restore original
git checkout -- <file>

# Surgical replacement via write_file + terminal
write_file(path='/tmp/fix.py', content="""
import sys
path = sys.argv[1]
old = sys.argv[2]
new = sys.argv[3]
with open(path) as f:
    src = f.read()
assert src.count(old) == 1, f"Expected 1 occurrence, found {src.count(old)}"
with open(path, 'w') as f:
    f.write(src.replace(old, new))
""")
terminal(f'python3 /tmp/fix.py <file> "<old>" "<new>"')
```

## Prevention

For small edits, prefer `write_file`→`terminal` over `patch` when:
- The file is large (>200 lines)
- The change is a simple string replacement
- You need exact control over formatting
