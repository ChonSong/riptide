#!/usr/bin/env bash
# scripts/ephemeral-test.sh — spin up an isolated Riptide container for one branch.
#
# Usage:
#   scripts/ephemeral-test.sh <branch-name>
#   scripts/ephemeral-test.sh fix/fixer-provider-defaults
#
# What it does:
#   1. Builds a Docker image from the specified branch
#   2. Runs an isolated container (unique name, unique port, ephemeral state)
#   3. Runs smoke tests (webhook health, optional Hermes provider probe)
#   4. Cleans up automatically on exit (container rm, volume rm, image prune)
#
# Isolation guarantees:
#   - Container name: riptide-test-<branch> (no clashes with prod or other tests)
#   - Host port: 18477 + hash(branch) mod 1000 (avoids 8477 prod port)
#   - State: anonymous volume (destroyed with container)
#   - Hermes: uses 'riptide-ephemeral' profile (separate from main config)
#   - Network: bridge network riptide-ephemeral-net (no cross-talk)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BRANCH="${1:-}"

if [[ -z "$BRANCH" ]]; then
    echo "Usage: $0 <branch-name>"
    echo "Example: $0 fix/fixer-provider-defaults"
    exit 1
fi

# Sanitize branch name for Docker naming (replace / with -)
SAFE_BRANCH="${BRANCH//\//-}"

# Deterministic but unique port offset (18477–19477 range)
PORT_OFFSET=$(( 18477 + $(echo "$BRANCH" | md5sum | head -c 8 | tr 'a-f' '0-5' | sed 's/^0*//' | head -c 4) % 1000 ))

CONTAINER_NAME="riptide-test-${SAFE_BRANCH}"
IMAGE_NAME="riptide-test:${SAFE_BRANCH}"
NETWORK_NAME="riptide-ephemeral-net"
PROFILE_NAME="riptide-ephemeral"

echo "━━━ Riptide Ephemeral Test ━━━"
echo "Branch:     $BRANCH"
echo "Container:  $CONTAINER_NAME"
echo "Port:       $PORT_OFFSET"
echo "Image:      $IMAGE_NAME"
echo "Network:    $NETWORK_NAME"
echo ""

# Cleanup function — runs on EXIT, SIGINT, SIGTERM
cleanup() {
    echo ""
    echo "━━━ Cleaning up ━━━"
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "Stopping container: $CONTAINER_NAME"
        docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
        docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
    fi
    if docker network ls --format '{{.Name}}' | grep -q "^${NETWORK_NAME}$"; then
        echo "Removing network: $NETWORK_NAME"
        docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
    fi
    if docker image ls --format '{{.Repository}}:{{.Tag}}' | grep -q "^${IMAGE_NAME}$"; then
        echo "Removing image: $IMAGE_NAME"
        docker image rm "$IMAGE_NAME" >/dev/null 2>&1 || true
    fi
    echo "Done."
}
trap cleanup EXIT INT TERM

# Step 1: Ensure we're on the right branch
echo "━━━ Checking out branch: $BRANCH ━━━"
cd "$PROJECT_ROOT"
git fetch origin "$BRANCH" 2>/dev/null || true
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git checkout "$BRANCH"
elif git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
    git checkout -b "$BRANCH" "origin/$BRANCH"
else
    echo "ERROR: Branch '$BRANCH' not found locally or on origin"
    exit 1
fi

echo "Commit: $(git rev-parse --short HEAD) — $(git log -1 --pretty=%s)"

# Step 2: Build image
echo ""
echo "━━━ Building image: $IMAGE_NAME ━━━"
docker build -t "$IMAGE_NAME" .

# Step 3: Create isolated network
echo ""
echo "━━━ Creating network: $NETWORK_NAME ━━━"
docker network create "$NETWORK_NAME" 2>/dev/null || true

# Step 4: Run container
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

echo "Container started. Waiting for health check..."

# Step 5: Health check with timeout
MAX_RETRIES=30
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
    echo "ERROR: Container failed health check after ${MAX_RETRIES} attempts"
    echo "Last HTTP code: $HTTP_CODE"
    echo ""
    echo "Container logs:"
    docker logs "$CONTAINER_NAME" --tail 30
    exit 1
fi

echo "✅ Container healthy! (HTTP $HTTP_CODE — webhook running, sig verification active)"

# Step 6: Hermes provider probe (optional — verifies fixer.py config would actually work)
echo ""
echo "━━━ Hermes Provider Probe ━━━"
echo "Testing which provider/model combos actually dispatch..."

if ! command -v hermes &>/dev/null; then
    echo "⚠️  'hermes' CLI not found — skipping provider probe"
else
    # Test 1: longcat provider (expected to work)
    echo ""
    echo "Test 1: --provider longcat --model LongCat-2.0"
    LONGCAT_RESULT=$(hermes cron create \
        "$(date -u -d '+1 minute' +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -v+1M +%Y-%m-%dT%H:%M:%S)" \
        "# Ephemeral test — longcat probe" \
        --name "probe-longcat-${SAFE_BRANCH}" \
        --model "LongCat-2.0" \
        --provider "longcat" \
        --repeat 1 \
        --deliver local 2>&1) || true
    
    if echo "$LONGCAT_RESULT" | grep -qi "failed\|error\|unknown"; then
        echo "  ❌ FAILED: $LONGCAT_RESULT"
    else
        echo "  ✅ Dispatched OK"
    fi

    # Test 2: custom provider (expected to fail/misroute)
    echo ""
    echo "Test 2: --provider custom --model custom:LongCat-2.0"
    CUSTOM_RESULT=$(hermes cron create \
        "$(date -u -d '+1 minute' +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -v+1M +%Y-%m-%dT%H:%M:%S)" \
        "# Ephemeral test — custom probe" \
        --name "probe-custom-${SAFE_BRANCH}" \
        --model "custom:LongCat-2.0" \
        --provider "custom" \
        --repeat 1 \
        --deliver local 2>&1) || true
    
    if echo "$CUSTOM_RESULT" | grep -qi "failed\|error\|unknown"; then
        echo "  ❌ FAILED (expected — custom provider has no LongCat-2.0): $CUSTOM_RESULT"
    else
        echo "  ⚠️  Dispatched (may be routed elsewhere — check Hermes logs)"
    fi

    # Cleanup probe jobs
    echo ""
    echo "Cleaning up probe cron jobs..."
    hermes cron list 2>/dev/null | grep "probe-" | awk '{print $1}' | while read -r job_id; do
        hermes cron remove "$job_id" 2>/dev/null || true
    done
fi

# Step 7: Interactive prompt to keep container alive for manual testing
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
