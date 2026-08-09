# CI Validation Triage

<!-- Trigger: CI fails on PR — determine if it's the PR's fault or pre-existing -->

## Is Master Also Red?

```bash
gh run list --repo O/R --workflow CI --branch master --limit 6
```

If master has been failing for weeks, the failure is pre-existing — scope it as context, not a PR regression.

## Fetch Job Logs

```bash
# gh run view --log-failed may show nothing — fetch job logs directly
gh run view <ID> --json jobs --jq '.jobs[] | {id, name, conclusion}'
gh api repos/O/R/actions/jobs/<JOB_ID>/logs
```

## Reproduce Locally

```bash
python -m pytest --tb=short -q
# INTERNALERROR during collection → module-import side effects
# "47/47 passed" before crash → tell-tale of module-level sys.exit(0)
```

## Third-Party Action Failures

When a NEW workflow fails on the PR that adds it, the failure is inside the action, not your YAML.

```bash
# Clone exact pinned SHA
git clone --quiet --depth 1 https://github.com/<owner>/<action> /tmp/action-repro
cd /tmp/action-repro && git checkout <pinned-sha>
bash src/scanner.sh --project-dir ~/workspace/<repo>
```

**AgentLint S6 false positive:** YAML data files containing `-----BEGIN ... [REDACTED PRIVATE KEY]` placeholder doc text trigger "no hardcoded secrets" check, then CRASH because `git grep` only covers source extensions (`*.js *.ts *.py`), NOT `*.yaml`. Fix: redact placeholder text to `[REDACTED]`.
