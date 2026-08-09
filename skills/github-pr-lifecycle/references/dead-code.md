# Dead Code Verification

<!-- Trigger: PR removes functions, modules, CSS classes, or files -->

## Detection Protocol

1. Extract function/class/CSS names being deleted from diff
2. `grep -rn` across ALL source and test directories (not just changed files)
3. Classify each hit:
   - Zero hits → truly dead, safe to remove
   - Hits in test files also being deleted → matched cleanup
   - Hits in any file NOT modified → STOP — still has live callers
4. Also check CSS classes — referenced from JS/HTML not in diff

## Commands

```bash
# Search for deleted names across codebase
grep -rn "_activityFullClockLabel\|_timestampSeconds" \
  static/ tests/ api/ --include="*.js" --include="*.py" --include="*.html" --include="*.css"

# Verify no stale .pyc
find . -type d -name __pycache__ -exec rm -rf {} +
```

## Dynamic References (JS)

```javascript
// window.fnName — grep won't find string references
window.__PROOFSHOT__
```

Check for these before approving deletion.
