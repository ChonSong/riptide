# test/e2e-pipeline-verify

Test PR to verify the full Riptide review pipeline end-to-end:

1. Webhook fires via tunnel (riptide.codeovertcp.com)
2. T0 orchestrator classifies the PR
3. Companion posts TL;DR + GIF
4. Deepthink gathers data (repo tree, code chunks, graphify) and dispatches
5. Subagents run inline review + Excalidraw diagram
6. Summary review posted with all required sections

This change adds `riptide/test_utils.py` (`format_elapsed`, `retry_until`)
and tests, then verifies the review pipeline runs end-to-end on a real PR.
