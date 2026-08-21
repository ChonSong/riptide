#!/usr/bin/env bash
# scripts/ephemeral-test.sh — spin up an isolated Riptide container for one branch.
#
# Usage:
#   scripts/ephemeral-test.sh <branch-name> [--no-probe] [--keep-image] [--port <port>] [--timeout <seconds>]
#   scripts/ephemeral-test.sh fix/fixer-provider-defaults
#
# Flags:
#   --no-probe        Skip Hermes provider probe (useful if `hermes` CLI not installed)
#   --keep-image      Don't remove the built Docker image on cleanup (faster re-runs)
#   --port <port>     Override deterministic port selection with a specific host port
#   --timeout <secs>  Health check timeout in seconds (default: 60)
#   -h, --help        Show this help message
#
# Isolation guarantees:
#   - Container name: riptide-test-<branch>-<hash> (no clashes with prod or other tests)
#   - Host port: 18477 + hash(branch) mod 1000 (avoids 8477 prod port)
#   - State: anonymous volume (destroyed with container)
#   - Network: bridge network riptide-ephemeral-<hash> (per-branch isolation)
#
# Portability:
#   - Uses python3 for hash and datetime (works on Linux, macOS, BSD)
#   - No GNU coreutils dependencies (md5sum, GNU date -d)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Defaults ────────────────────────────────────────────────────────────────

BRANCH=""
DO_PROBE=true
KEEP_IMAGE=false
OVERRIDE_PORT=""
HEALTH_TIMEOUT=60

# ── Parse arguments ─────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-probe)
            DO_PROBE=false
            shift
            ;;
        --keep-image)
            KEEP_IMAGE=true
            shift
            ;;
        --port)
            if [[ -n "${2:-}" && "$2" != -* ]]; then
                OVERRIDE_PORT="$2"
                shift 2
            else
                echo "ERROR: --port requires an argument" >&2
                exit 1
            fi
            ;;
        --timeout)
            if [[ -n "${2:-}" && "$2" != -* ]]; then
                HEALTH_TIMEOUT="$2"
                shift 2
            else
                echo "ERROR: --timeout requires an argument" >&2
                exit 1
            fi
            ;;
        -h|--help)
            echo "Usage: $0 <branch-name> [--no-probe] [--keep-image] [--port <port>] [--timeout <seconds>]"
            echo ""
            echo "Spin up an isolated Riptide container for testing a branch."
            echo ""
            echo "Flags:"
            echo "  --no-probe        Skip Hermes provider probe"
            echo "  --keep-image      Don't remove the built Docker image on cleanup"
            echo "  --port <port>     Override automatic port selection"
            echo "  --timeout <secs>  Health check timeout (default: 60)"
            exit 0
            ;;
        -*)
            echo "ERROR: Unknown flag: $1" >&2
            exit 1
            ;;
        *)
            if [[ -z "$BRANCH" ]]; then
                BRANCH="$1"
                shift
            else
                echo "ERROR: Unexpected argument: $1" >&2
                exit 1
            fi
            ;;
    esac
done

if [[ -z "$BRANCH" ]]; then
    echo "Usage: $0 <branch-name> [--no-probe] [--keep-image] [--port <port>] [--timeout <seconds>]"
    exit 1
fi

# ── Pre-flight checks ───────────────────────────────────────────────────────

for cmd in docker git python3 curl; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: Required command '$cmd' not found in PATH" >&2
        exit 1
    fi
done

# ── Validate parsed values ──────────────────────────────────────────────────

# Validate port
if [[ -n "$OVERRIDE_PORT" ]]; then
    if ! [[ "$OVERRIDE_PORT" =~ ^[0-9]+$ ]] || [[ "$OVERRIDE_PORT" -lt 1 ]] || [[ "$OVERRIDE_PORT" -gt 65535 ]]; then
        echo "ERROR: --port must be a positive integer between 1 and 65535, got '$OVERRIDE_PORT'" >&2
        exit 1
    fi
fi

# Validate timeout
if ! [[ "$HEALTH_TIMEOUT" =~ ^[0-9]+$ ]] || [[ "$HEALTH_TIMEOUT" -lt 1 ]]; then
    echo "ERROR: --timeout must be a positive integer, got '$HEALTH_TIMEOUT'" >&2
    exit 1
fi

# ── Python helpers (portable) ──────────────────────────────────────────────

# Deterministic port from branch name using python (works on macOS/Linux/BSD)
if [[ -n "$OVERRIDE_PORT" ]]; then
    PORT_OFFSET="$OVERRIDE_PORT"
else
    PORT_OFFSET=$(python3 -c "
import hashlib, sys
branch = sys.argv[1]
h = hashlib.md5(branch.encode()).hexdigest()
offset = int(h[:8], 16) % 1000
print(18477 + offset)
" "$BRANCH")
fi

# Sanitize branch name for Docker naming (replace / with -)
SAFE_BRANCH="${BRANCH//\//-}"

# Deterministic Docker-safe hash from original BRANCH value
BRANCH_HASH=$(python3 -c "
import hashlib, sys
branch = sys.argv[1]
h = hashlib.sha256(branch.encode()).hexdigest()[:12]
print(h)
" "$BRANCH")

CONTAINER_NAME="riptide-test-${SAFE_BRANCH}-${BRANCH_HASH}"
IMAGE_NAME="riptide-test:${BRANCH_HASH}"
NETWORK_NAME="riptide-ephemeral-${BRANCH_HASH}"

# ── Cleanup function ────────────────────────────────────────────────────────

WORKTREE_DIR=""

cleanup() {
    echo ""
    echo "━━━ Cleaning up ━━━"
    if [[ -n "$WORKTREE_DIR" && -d "$WORKTREE_DIR" ]]; then
        echo "Removing worktree: $WORKTREE_DIR"
        git worktree remove "$WORKTREE_DIR" --force 2>/dev/null || true
        rm -rf "$WORKTREE_DIR" 2>/dev/null || true
    fi
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "Stopping container: $CONTAINER_NAME"
        docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
        docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
    fi
    if docker network ls --format '{{.Name}}' | grep -q "^${NETWORK_NAME}$"; then
        echo "Removing network: $NETWORK_NAME"
        docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
    fi
    if [[ -n "${IMAGE_NAME:-}" ]] && [[ "$KEEP_IMAGE" == "false" ]]; then
        if docker image ls --format '{{.Repository}}:{{.Tag}}' | grep -q "^${IMAGE_NAME}$"; then
            echo "Removing image: $IMAGE_NAME"
            docker image rm "$IMAGE_NAME" >/dev/null 2>&1 || true
        fi
    elif [[ "$KEEP_IMAGE" == "true" ]]; then
        echo "Keeping image: $IMAGE_NAME"
    fi
    echo "Done."
}

# Signal handlers — cleanup then exit with signal status
handle_sigint() {
    cleanup
    exit 130
}

handle_sigterm() {
    cleanup
    exit 143
}

trap cleanup EXIT
trap handle_sigint INT
trap handle_sigterm TERM

# ── Step 1: Fetch and create worktree ───────────────────────────────────────

echo "━━━ Riptide Ephemeral Test ━━━"
echo "Branch:     $BRANCH"
echo "Container:  $CONTAINER_NAME"
echo "Port:       $PORT_OFFSET"
echo "Image:      $IMAGE_NAME"
echo "Network:    $NETWORK_NAME"
echo "Probe:      $DO_PROBE"
echo "Keep image: $KEEP_IMAGE"
echo ""

echo "━━━ Fetching branch: $BRANCH ━━━"
cd "$PROJECT_ROOT"

# Resolve the ref: accept branch names (main, feature/x) or full refs (refs/heads/main, refs/tags/v1)
if [[ "$BRANCH" == refs/* ]]; then
    # Full ref — use directly
    REF="$BRANCH"
    # Fetch the specific ref
    git fetch origin "$REF" 2>/dev/null || {
        echo "ERROR: Could not fetch ref '$REF' from origin" >&2
        exit 1
    }
else
    # Branch name — fetch and resolve
    git fetch origin "$BRANCH" 2>/dev/null || {
        echo "ERROR: Could not fetch branch '$BRANCH' from origin" >&2
        exit 1
    }
fi

# Resolve the commit SHA (prefer remote, fall back to local)
if git show-ref --verify --quiet "refs/remotes/origin/$BRANCH" 2>/dev/null; then
    COMMIT_SHA=$(git rev-parse "origin/$BRANCH")
elif git show-ref --verify --quiet "refs/heads/$BRANCH" 2>/dev/null; then
    COMMIT_SHA=$(git rev-parse "$BRANCH")
elif [[ "$BRANCH" == refs/* ]] && git show-ref --verify --quiet "$BRANCH" 2>/dev/null; then
    COMMIT_SHA=$(git rev-parse "$BRANCH")
else
    echo "ERROR: Branch/ref '$BRANCH' not found locally or on origin" >&2
    exit 1
fi

echo "Commit: ${COMMIT_SHA:0:12} — $(git log -1 --pretty=%s "$COMMIT_SHA")"

# Create temporary detached worktree
WORKTREE_DIR=$(mktemp -d -t riptide-worktree-XXXXXX)
echo "Worktree:   $WORKTREE_DIR"
if ! git worktree add --detach "$WORKTREE_DIR" "$COMMIT_SHA"; then
    echo "ERROR: Failed to create worktree at '$WORKTREE_DIR'" >&2
    rm -rf "$WORKTREE_DIR"
    exit 1
fi

# ── Step 2: Build image ─────────────────────────────────────────────────────

echo ""
echo "━━━ Building image: $IMAGE_NAME ━━━"
if ! docker build -t "$IMAGE_NAME" "$WORKTREE_DIR"; then
    echo "ERROR: Docker build failed" >&2
    exit 1
fi

# ── Step 3: Create isolated network ─────────────────────────────────────────

echo ""
echo "━━━ Creating network: $NETWORK_NAME ━━━"
docker network create "$NETWORK_NAME" 2>/dev/null || true

# ── Step 4: Run container ───────────────────────────────────────────────────

echo ""
echo "━━━ Running container: $CONTAINER_NAME ━━━"
docker run -d \
    --name "$CONTAINER_NAME" \
    --network "$NETWORK_NAME" \
    -p "${PORT_OFFSET}:8477" \
    -e GITHUB_APP_ID=0 \
    -e GITHUB_PRIVATE_KEY_PATH=/dev/null \
    -e GITHUB_WEBHOOK_SECRET=test-secret \
    -e RIPTIDE_DATA_DIR=/tmp/riptide-data \
    -e HOST=0.0.0.0 \
    -e PORT=8477 \
    "$IMAGE_NAME" >/dev/null

echo "Container started. Waiting for health check (timeout: ${HEALTH_TIMEOUT}s)..."

# ── Step 5: Health check with timeout ───────────────────────────────────────

MAX_RETRIES=$((HEALTH_TIMEOUT / 2))
RETRY=0
HEALTHY=false

while [[ $RETRY -lt $MAX_RETRIES ]]; do
    RETRY=$((RETRY + 1))
    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${PORT_OFFSET}/webhook/github" -X POST -H "Content-Type: application/json" -d '{"test":1}' 2>/dev/null || echo "000")

    if [[ "$HTTP_CODE" == "401" ]]; then
        HEALTHY=true
        break
    fi

    sleep 2
done

if [[ "$HEALTHY" == "false" ]]; then
    echo "ERROR: Container failed health check after ${HEALTH_TIMEOUT}s"
    echo "Last HTTP code: $HTTP_CODE"
    echo ""
    echo "Container logs:"
    docker logs "$CONTAINER_NAME" --tail 30
    exit 1
fi

echo "✅ Container healthy! (HTTP $HTTP_CODE — webhook running, sig verification active)"

# ── Step 6: Hermes provider probe (optional) ───────────────────────────────

if [[ "$DO_PROBE" == "true" ]]; then
    echo ""
    echo "━━━ Hermes Provider Probe ━━━"
    echo "Testing which provider/model combos actually dispatch..."

    if ! command -v hermes &>/dev/null; then
        echo "⚠️  'hermes' CLI not found — skipping provider probe"
    else
        # Use python for datetime (portable)
        RUN_AT=$(python3 -c "from datetime import datetime, timedelta; print((datetime.utcnow() + timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%S'))")

        PROBE_JOB_IDS=()

        # Test 1: longcat provider (expected to work)
        echo ""
        echo "Test 1: --provider longcat --model LongCat-2.0"
        LONGCAT_OUTPUT=$(hermes cron create \
            "$RUN_AT" \
            "# Ephemeral test — longcat probe" \
            --name "probe-longcat-${BRANCH_HASH}" \
            --model "LongCat-2.0" \
            --provider "longcat" \
            --repeat 1 \
            --deliver local 2>&1) || true

        # Extract job ID from output (hermes cron create prints the job ID)
        LONGCAT_ID=$(echo "$LONGCAT_OUTPUT" | grep -oE '[a-f0-9-]{36}' | head -1 || true)
        if [[ -n "$LONGCAT_ID" ]]; then
            PROBE_JOB_IDS+=("$LONGCAT_ID")
            echo "  Created job: $LONGCAT_ID"
        fi

        if echo "$LONGCAT_OUTPUT" | grep -qi "failed\|error\|unknown"; then
            echo "  ❌ FAILED: $LONGCAT_OUTPUT"
        else
            echo "  ✅ Dispatched OK"
        fi

        # Test 2: custom provider (expected to fail/misroute)
        echo ""
        echo "Test 2: --provider custom --model custom:LongCat-2.0"
        CUSTOM_OUTPUT=$(hermes cron create \
            "$RUN_AT" \
            "# Ephemeral test — custom probe" \
            --name "probe-custom-${BRANCH_HASH}" \
            --model "custom:LongCat-2.0" \
            --provider "custom" \
            --repeat 1 \
            --deliver local 2>&1) || true

        CUSTOM_ID=$(echo "$CUSTOM_OUTPUT" | grep -oE '[a-f0-9-]{36}' | head -1 || true)
        if [[ -n "$CUSTOM_ID" ]]; then
            PROBE_JOB_IDS+=("$CUSTOM_ID")
            echo "  Created job: $CUSTOM_ID"
        fi

        if echo "$CUSTOM_OUTPUT" | grep -qi "failed\|error\|unknown"; then
            echo "  ❌ FAILED (expected — custom provider has no LongCat-2.0): $CUSTOM_OUTPUT"
        else
            echo "  ⚠️  Dispatched (may be routed elsewhere — check Hermes logs)"
        fi

        # Poll jobs until terminal state (max 30 seconds)
        if [[ ${#PROBE_JOB_IDS[@]} -gt 0 ]]; then
            echo ""
            echo "Polling probe jobs for terminal state..."
            POLL_RETRIES=15
            for job_id in "${PROBE_JOB_IDS[@]}"; do
                job_found=false
                for ((i=1; i<=POLL_RETRIES; i++)); do
                    JOB_LINE=$(hermes cron list 2>/dev/null | grep "$job_id" || true)
                    if [[ -z "$JOB_LINE" ]]; then
                        echo "  Job $job_id: removed"
                        job_found=true
                        break
                    fi
                    JOB_STATUS=$(echo "$JOB_LINE" | awk '{print $3}' || true)
                    if [[ "$JOB_STATUS" == "completed" || "$JOB_STATUS" == "failed" || "$JOB_STATUS" == "removed" ]]; then
                        echo "  Job $job_id: $JOB_STATUS"
                        job_found=true
                        break
                    fi
                    sleep 2
                done
                if [[ "$job_found" == "false" ]]; then
                    echo "  Job $job_id: timeout (still running)"
                fi
            done
        fi

        # Cleanup probe jobs (only the ones we created)
        if [[ ${#PROBE_JOB_IDS[@]} -gt 0 ]]; then
            echo ""
            echo "Cleaning up probe cron jobs..."
            for job_id in "${PROBE_JOB_IDS[@]}"; do
                hermes cron remove "$job_id" 2>/dev/null || true
            done
        fi
    fi
else
    echo ""
    echo "━━━ Skipping Hermes probe (--no-probe) ━━━"
fi

# ── Step 7: Interactive prompt to keep container alive ──────────────────────

echo ""
echo "━━━ Ephemeral Test Summary ━━━"
echo "Container:  $CONTAINER_NAME"
echo "URL:        http://localhost:${PORT_OFFSET}"
echo "Webhook:    http://localhost:${PORT_OFFSET}/webhook/github"
echo ""
echo "Press Ctrl+C to stop and clean up, or run manual tests now."

# Wait for user interrupt
while true; do
    sleep 1
done
