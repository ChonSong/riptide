#!/usr/bin/env python3
"""
session_spawner.py — Spawn stratified Hermes sessions for each worker role.

Each worker (probe, judge, artisan, engine, warden, scribe, ci_verifier,
test_oracle, review_memory, documentarian) gets its own Hermes session with:
- Role-specific system prompt
- Role-specific skills
- Isolated context window
- Role-specific memory (via Hermes memory)

Sessions communicate via work-state.json and temp files.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("riptide.session_spawner")

# ── Role configurations ────────────────────────────────────────────────────
# Each role defines what tools, skills, and context it needs.
# This is the stratification — probe doesn't get write_file, artisan doesn't need gh CLI, etc.

ROLE_CONFIGS = {
    "probe": {
        "description": "Gathers deterministic context via Riptide tools",
        "skills": ["terminal", "file"],
        "tools": ["terminal", "read_file"],
        "output_format": "json",
        "output_path_key": "context_path",
        "system_prompt": """You are the PROBE worker in the Riptide review pipeline.

Your role: Gather deterministic context about this PR.
- Fetch the diff, run graphify blast-radius analysis, build context bundle
- Use `python -m riptide.diff_analyzer` for deterministic diff analysis
- Use `python -m riptide.context_bundle` to build context bundle
- Output structured JSON to the specified path
- Do NOT evaluate code quality or post comments

Tools available: terminal, read_file
Output: JSON file at {output_path}

Pipeline position: First stage — your output feeds into the JUDGE worker.""",
    },
    "judge": {
        "description": "Evaluates diffs against acceptance criteria, dedups findings",
        "skills": ["deep-think", "code-review", "coding-standards"],
        "tools": ["read_file", "write_file"],
        "output_format": "json",
        "output_path_key": "findings_path",
        "system_prompt": """You are the JUDGE worker in the Riptide review pipeline.

Your role: Evaluate the PR diff against acceptance criteria.
- Read the context gathered by PROBE
- Analyze code quality, security, performance, maintainability
- Deduplicate findings (don't repeat the same issue)
- Output structured findings JSON to the specified path

Tools available: read_file, write_file
Output: JSON file at {output_path}

Pipeline position: After PROBE, before ARTISAN.
Key facts from probe: {key_facts}""",
    },
    "artisan": {
        "description": "Creates/modifies files with exact content",
        "skills": ["excalidraw", "diagram-generation"],
        "tools": ["read_file", "write_file", "patch", "terminal"],
        "output_format": "json",
        "output_path_key": "artifact_path",
        "system_prompt": """You are the ARTISAN worker in the Riptide review pipeline.

Your role: Generate visual artifacts (architecture diagrams) from review findings.
- Read the findings from JUDGE
- Use `python -m riptide.diagram_analyst` to generate Excalidraw diagram
- Upload the diagram and return the URL

Tools available: read_file, write_file, patch, terminal
Output: JSON file at {output_path}

Pipeline position: After JUDGE, before ENGINE.
Findings summary: {findings_summary}""",
    },
    "engine": {
        "description": "Executes exact shell commands, captures exit code",
        "skills": ["terminal"],
        "tools": ["terminal"],
        "output_format": "json",
        "output_path_key": "result_path",
        "system_prompt": """You are the ENGINE worker in the Riptide review pipeline.

Your role: Execute shell commands and capture results.
- Run the specified command
- Capture exit code, stdout, stderr
- Output structured result JSON to the specified path

Tools available: terminal
Output: JSON file at {output_path}

Pipeline position: After ARTISAN, before WARDEN.
Command to execute: {command}""",
    },
    "warden": {
        "description": "Verifies outputs meet acceptance criteria",
        "skills": ["code-review"],
        "tools": ["read_file", "terminal"],
        "output_format": "json",
        "output_path_key": "verification_path",
        "system_prompt": """You are the WARDEN worker in the Riptide review pipeline.

Your role: Verify that worker outputs meet acceptance criteria.
- Check that output files exist and are valid JSON
- Validate findings structure (severity, title, file, line)
- Verify diagram was uploaded successfully
- Output verification result to the specified path

Tools available: read_file, terminal
Output: JSON file at {output_path}

Pipeline position: After ENGINE, before SCRIBE.
Acceptance criteria: {acceptance}""",
    },
    "scribe": {
        "description": "Updates work-state.json and posts GitHub comments",
        "skills": ["github-pr-lifecycle"],
        "tools": ["read_file", "write_file", "terminal"],
        "output_format": "json",
        "output_path_key": "record_path",
        "system_prompt": """You are the SCRIBE worker in the Riptide review pipeline.

Your role: Assemble and post the final review comment.
- Read all worker outputs (context, findings, diagram URL, verification)
- Use `python -m riptide.assemble_review` to format the review
- Post the comment to GitHub via `gh` CLI
- Use `python -m riptide.interaction_handler` for command routing
- Update work-state.json to mark the track complete

Tools available: read_file, write_file, terminal
Output: JSON file at {output_path}

Pipeline position: Final stage — assembles everything into the posted review.
Owner: {owner}, Repo: {repo}, PR: {pr_number}
Assembly command:
```python
python -m riptide.assemble_review \
  --findings {findings_path} \
  --owner {owner} --repo {repo} --pr {pr_number} \
  --diagram-url "{diagram_url}" \
  --model "{model}" --provider "{provider}"
```""",
    },
    "ci_verifier": {
        "description": "Polls GitHub CI checks, classifies failures, returns verdict",
        "skills": ["github-pr-lifecycle"],
        "tools": ["terminal"],
        "output_format": "json",
        "output_path_key": "ci_result_path",
        "system_prompt": """You are the CI VERIFIER worker in the Riptide review pipeline.

Your role: Poll GitHub CI checks and classify failures.
- Poll `gh pr checks {pr_number} --repo {owner}/{repo}`
- Wait for CI to complete (timeout: {timeout}s)
- Classify each failure as fixable/non-fixable
- Output structured CI result JSON to the specified path

Tools available: terminal
Output: JSON file at {output_path}

Pipeline position: After ENGINE (for fix pipelines), before SCRIBE.
Owner: {owner}, Repo: {repo}, PR: {pr_number}""",
    },
    "test_oracle": {
        "description": "Runs targeted tests based on PR diff",
        "skills": ["terminal", "file"],
        "tools": ["terminal", "read_file"],
        "output_format": "json",
        "output_path_key": "test_result_path",
        "system_prompt": """You are the TEST ORACLE worker in the Riptide review pipeline.

Your role: Run targeted tests based on PR diff changed files.
- Use `python -m riptide.test_oracle` to map files to tests and run them
- Identify missing test coverage
- Output structured test result JSON

Tools available: terminal, read_file
Output: JSON file at {output_path}

Pipeline position: After JUDGE, before WARDEN.""",
    },
    "review_memory": {
        "description": "Stores review outcomes and retrieves historical context",
        "skills": ["terminal", "file"],
        "tools": ["terminal", "read_file", "write_file"],
        "output_format": "json",
        "output_path_key": "memory_result_path",
        "system_prompt": """You are the REVIEW MEMORY worker in the Riptide review pipeline.

Your role: Store review outcomes and retrieve historical context.
- Use `python -m riptide.review_memory` to store review outcome
- Retrieve common-finding patterns for future reviews
- Update review_profiles aggregate

Tools available: terminal, read_file, write_file
Output: JSON file at {output_path}

Pipeline position: Post-merge, final stage.""",
    },
    "documentarian": {
        "description": "Updates graphify and changelog on PR merge",
        "skills": ["terminal"],
        "tools": ["terminal"],
        "output_format": "json",
        "output_path_key": "documentarian_result_path",
        "system_prompt": """You are the DOCUMENTARIAN worker in the Riptide review pipeline.

Your role: Update graphify and changelog on PR merge.
- Use `python -m riptide.documentarian` to:
  1. Run `graphify update .` to refresh knowledge graph
  2. Generate changelog entry from PR description + findings
  3. Update review_profiles table

Tools available: terminal
Output: JSON file at {output_path}

Pipeline position: Post-merge, parallel to REVIEW MEMORY.""",
    },
}

# ── Inference config ────────────────────────────────────────────────────────
# Pin the model/provider to prevent config drift (Hermes #44585)
DEFAULT_MODEL = os.environ.get("RIPTIDE_DEEPTHINK_MODEL", "LongCat-2.0")
DEFAULT_PROVIDER = os.environ.get("RIPTIDE_DEEPTHINK_PROVIDER", "longcat")


def _is_cron_available() -> bool:
    """Check that `hermes cron create` works."""
    result = subprocess.run(
        ["which", "hermes"], capture_output=True, text=True, timeout=5
    )
    return bool(result.returncode == 0 and result.stdout.strip())


def _hermes_blocked(stdout: str, stderr: str) -> bool:
    """Check if Hermes blocked the cron job creation."""
    combined = (str(stdout) + "\n" + str(stderr)).lower()
    return "failed to create job" in combined


def spawn_worker_session(
    role: str,
    track_id: str,
    workstream_id: str,
    inputs: dict,
    acceptance: dict,
    key_facts: dict | None = None,
    model: str = DEFAULT_MODEL,
    provider: str = DEFAULT_PROVIDER,
) -> bool:
    """
    Spawn a stratified Hermes session for a single worker role.

    Each session gets:
    - Role-specific system prompt (stratified context)
    - Role-specific skills (no bloated tool access)
    - Isolated context window (only what this role needs)
    - Hermes-native memory (persists across sessions for same role)

    Args:
        role: Worker role (probe, judge, artisan, engine, warden, scribe, ci_verifier)
        track_id: Conductor track ID for state correlation
        workstream_id: Workstream ID within the track
        inputs: Role-specific input parameters
        acceptance: Acceptance criteria for output verification
        key_facts: Facts from upstream workers (passed to downstream prompts)
        model: Model to use for this session
        provider: Provider to use for this session

    Returns:
        True if session was scheduled successfully
    """
    if role not in ROLE_CONFIGS:
        raise ValueError(f"Unknown role: {role}. Valid: {list(ROLE_CONFIGS.keys())}")

    config = ROLE_CONFIGS[role]
    max_retries = 3
    base_delay = 5

    # Build the role-specific prompt
    prompt = _build_worker_prompt(role, config, inputs, acceptance, key_facts)

    # Write prompt to temp file (security: 0600 permissions, sanitized)
    fd, prompt_file = tempfile.mkstemp(suffix=".txt", prefix=f"riptide-{role}-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(prompt)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(prompt_file)
        except OSError:
            pass
        raise

    # Build the hermes cron create command
    name = f"riptide-{role}-{track_id}"
    run_at = (datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")

    cmd_prompt = (
        f"Read the prompt from {prompt_file} and execute it. "
        f"After execution, delete the file {prompt_file}."
    )

    cmd = [
        "hermes", "cron", "create", run_at,
        cmd_prompt,
        "--name", name,
        "--model", model,
        "--provider", provider,
        "--deliver", "local",  # Workers don't need to deliver to origin
    ]

    # Add role-specific skills
    for skill in config["skills"]:
        cmd.extend(["--skill", skill])

    scheduled = False
    try:
        for attempt in range(max_retries):
            if attempt > 0:
                delay = base_delay * (2 ** attempt)
                log.info(f"Retry {attempt+1}/{max_retries} for {role} in {track_id} in {delay}s...")
                time.sleep(delay)

            if not _is_cron_available():
                log.warning(f"hermes not available on attempt {attempt+1} for {role}")
                continue

            log.info(f"Spawning {role} session for {track_id}: {name}")
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=15
                )
                if result.returncode == 0 and not _hermes_blocked(result.stdout, result.stderr):
                    log.info(f"✓ Spawned {role} for {track_id}")
                    scheduled = True
                    return True
                else:
                    log.error(f"✗ Spawn failed (attempt {attempt+1}): stdout={result.stdout[:300]} stderr={result.stderr[:300]}")
            except subprocess.TimeoutExpired:
                log.warning(f"Timeout spawning {role} (attempt {attempt+1})")
            except Exception as e:
                log.error(f"Error spawning {role} (attempt {attempt+1}): {e}")

        log.error(f"All {max_retries} attempts failed for {role} in {track_id}")
        return False
    finally:
        if not scheduled:
            try:
                os.unlink(prompt_file)
            except OSError:
                pass


def _build_worker_prompt(
    role: str,
    config: dict,
    inputs: dict,
    acceptance: dict,
    key_facts: dict | None = None,
) -> str:
    """Build a role-specific prompt for the worker session."""
    output_path = inputs.get("output_path", f"/tmp/riptide-{role}-output.json")

    # Base prompt from role config
    prompt = config["system_prompt"].format(
        output_path=output_path,
        key_facts=json.dumps(key_facts or {}, indent=2),
        findings_summary=inputs.get("findings_summary", "N/A"),
        command=inputs.get("command", "N/A"),
        acceptance=json.dumps(acceptance, indent=2),
        owner=inputs.get("owner", "ChonSong"),
        repo=inputs.get("repo", "riptide"),
        pr_number=inputs.get("pr_number", 0),
        findings_path=inputs.get("findings_path", "/tmp/findings.json"),
        diagram_url=inputs.get("diagram_url", ""),
        model=DEFAULT_MODEL,
        provider=DEFAULT_PROVIDER,
    )

    # Add structured input data
    prompt += f"\n\n## Inputs\n```json\n{json.dumps(inputs, indent=2, default=str)}\n```"

    # Add acceptance criteria
    prompt += f"\n\n## Acceptance Criteria\n```json\n{json.dumps(acceptance, indent=2)}\n```"

    # Add pipeline context
    prompt += f"\n\n## Pipeline Context\n- Track: {inputs.get('track_id', 'unknown')}\n- Workstream: {inputs.get('workstream_id', 'unknown')}\n- Role: {role}\n- Output path: {output_path}"

    # Add completion callback — when this worker finishes, it must call
    # the conductor resume endpoint to dispatch the next worker
    resume_url = inputs.get("resume_url", "")
    if resume_url:
        prompt += f"\n\n## Completion Callback\nAfter you finish your task and write the output to `{output_path}`, call this URL to dispatch the next worker:\n```\n{resume_url}\n```\nUse `curl` or `requests` to make a GET request to this URL."

    # Add role-specific instructions
    prompt += _get_role_instructions(role, inputs)

    return prompt


def _get_role_instructions(role: str, inputs: dict) -> str:
    """Get role-specific execution instructions with template substitution."""
    output_path = inputs.get("output_path", f"/tmp/riptide-{role}-output.json")
    
    # Build format kwargs from inputs
    fmt = {
        "output_path": output_path,
        "pr_number": inputs.get("pr_number", 0),
        "owner": inputs.get("owner", "ChonSong"),
        "repo": inputs.get("repo", "riptide"),
        "context_path": inputs.get("context_path", "/tmp/context.json"),
        "findings_path": inputs.get("findings_path", "/tmp/findings.json"),
        "diagram_url": inputs.get("diagram_url", ""),
        "command": inputs.get("command", "N/A"),
        "timeout": inputs.get("timeout", 600),
        "model": DEFAULT_MODEL,
        "provider": DEFAULT_PROVIDER,
    }
    
    instructions = {
        "probe": """
## Your Task: Gather Context

1. Fetch the PR diff using `gh pr diff {pr_number} --repo {owner}/{repo}`
2. Run graphify blast-radius analysis on the changed files
3. Build a context bundle with:
   - PR metadata (title, author, files changed)
   - Diff summary (files, additions, deletions)
   - Graphify analysis (blast radius, affected nodes)
   - Already-reviewed status (check if this PR was reviewed before)

Write the context bundle to: {output_path}

Output format:
```json
{{
  "pr_data": {{...}},
  "diff_report": {{...}},
  "bundle": {{...}},
  "graphify": {{...}},
  "already_reviewed": false,
  "previous_findings": [],
  "key_facts": {{...}}
}}
```""",
        "judge": """
## Your Task: Evaluate Code Quality

1. Read the context from: {context_path}
2. Analyze the diff for:
   - Code quality issues (complexity, readability, maintainability)
   - Security vulnerabilities (injection, auth, secrets)
   - Performance problems (N+1 queries, unnecessary allocations)
   - Test coverage gaps
3. Deduplicate findings (one issue per problem, not per occurrence)
4. Output structured findings

Write findings to: {output_path}

Output format:
```json
{{
  "findings": [
    {{
      "severity": "critical|warning|suggestion|info|approved",
      "title": "Short description",
      "detail": "Detailed explanation with code references",
      "file": "path/to/file.py",
      "line": 42,
      "suggestion": "How to fix it"
    }}
  ]
}}
```

Rules:
- Max 3 inline comments, real issues only
- Do not invent problems or pad the review
- Severity must be one of: critical, warning, suggestion, info, approved""",
        "artisan": """
## Your Task: Generate Architecture Diagram

1. Read the findings from: {findings_path}
2. Create an Excalidraw diagram showing:
   - The architectural impact of the changes
   - Which components are affected
   - Data flow changes (if any)
3. Upload the diagram and get the URL

Write the result to: {output_path}

Output format:
```json
{{
  "diagram_url": "https://...",
  "diagram_path": "/tmp/review.excalidraw",
  "uploaded": true
}}
```""",
        "engine": """
## Your Task: Execute Command

1. Run the specified command
2. Capture exit code, stdout, stderr
3. Output the result

Command: {command}

Write result to: {output_path}

Output format:
```json
{{
  "command": "{command}",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "...",
  "success": true
}}
```""",
        "warden": """
## Your Task: Verify Output Quality

1. Check that output files exist and are valid JSON
2. Validate findings structure:
   - Each finding has required fields (severity, title)
   - Severity is one of: critical, warning, suggestion, info, approved
   - File paths are valid
3. Verify diagram was uploaded (if applicable)
4. Output verification result

Write result to: {output_path}

Output format:
```json
{{
  "pass": true,
  "checks": [
    {{"method": "check_file_exists", "passed": true}},
    {{"method": "validate_findings", "passed": true}}
  ],
  "issues": []
}}
```""",
        "scribe": """
## Your Task: Assemble and Post Review

1. Read all worker outputs:
   - Context: {context_path}
   - Findings: {findings_path}
   - Diagram: from artisan output
   - Verification: from warden output
2. Run the assembly script to post the review:
```bash
python -m riptide.assemble_review \
  --findings {findings_path} \
  --owner {owner} --repo {repo} --pr {pr_number} \
  --diagram-url "{diagram_url}" \
  --model "{model}" --provider "{provider}"
```
3. Verify the comment was posted successfully

Write result to: {output_path}

Output format:
```json
{{
  "posted": true,
  "comment_url": "https://github.com/...",
  "body_length": 1234
}}
```""",
        "ci_verifier": """
## Your Task: Verify CI Status

1. Poll GitHub CI checks: `gh pr checks {pr_number} --repo {owner}/{repo}`
2. Wait for CI to complete (timeout: {timeout}s)
3. Classify each failure:
   - fixable: test failures, lint errors, build errors
   - non_fixable: infrastructure issues, rate limits
4. Output CI result

Write result to: {output_path}

Output format:
```json
{{
  "status": "success|failure|pending",
  "passed": [...],
  "failed": [...],
  "fixable": [...],
  "non_fixable": [...]
}}
```""",
        "test_oracle": """
## Your Task: Run Targeted Tests

1. Map changed files to test files using `python -m riptide.test_oracle`
2. Run targeted tests (not full suite)
3. Identify missing test coverage
4. Output test results

Write result to: {output_path}

Output format:
```json
{{
  "tests_run": 23,
  "passed": 21,
  "failed": 2,
  "missing_tests": ["webhook.deploy.*"],
  "duration_s": 45.2
}}
```""",
        "review_memory": """
## Your Task: Store Review Outcome

1. Store review outcome using `python -m riptide.review_memory`
2. Retrieve common-finding patterns from historical reviews
3. Output memory context for future reviews

Write result to: {output_path}

Output format:
```json
{{
  "stored": true,
  "patterns": ["common finding 1", "common finding 2"],
  "history_count": 42
}}
```""",
        "documentarian": """
## Your Task: Update Knowledge Graph

1. Run `python -m riptide.documentarian` to:
   a. Update graphify with merged changes
   b. Generate changelog entry
   c. Update review_profiles
2. Verify all operations succeeded

Write result to: {output_path}

Output format:
```json
{{
  "graphify_updated": true,
  "changelog_entry": "...",
  "profile_updated": true
}}
```""",
    }

    return instructions.get(role, "## Your Task\nExecute your role and write output to {output_path}").format(**fmt)


if __name__ == "__main__":
    # Quick test: print all role configs
    for role, config in ROLE_CONFIGS.items():
        print(f"{role}: {config['description']}")
