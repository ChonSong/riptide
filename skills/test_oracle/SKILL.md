---
name: test-oracle
description: Targeted test execution from PR diff changed files. Maps source files to test files, runs pytest, and reports results.
triggers:
  - "run tests for PR"
  - "test oracle"
  - "targeted tests"
  - "which tests to run"
---

# Test Oracle

Maps changed source files in a PR to their corresponding test files and runs targeted pytest.

## Usage

```python
from riptide.test_oracle import generate_test_report

report = generate_test_report(
    owner="ChonSong",
    repo="riptide",
    pr_number=42,
    files_changed=["riptide/webhook.py", "riptide/state.py"],
    root=".",
)
# Returns: {tests_run, passed, failed, missing_tests, duration_s, status}
```

## Functions

- `map_files_to_tests(files_changed, root)` — returns test file paths
- `find_missing_tests(files_changed, root)` — source files without tests
- `run_tests(test_files, cwd)` — executes pytest, returns structured result
- `generate_test_report(owner, repo, pr_number, files_changed, root)` — full pipeline

## Configuration

Edit `FILE_TEST_MAP` in `riptide/test_oracle.py` to add new source→test mappings.