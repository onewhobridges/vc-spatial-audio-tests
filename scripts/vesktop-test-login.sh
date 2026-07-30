#!/bin/bash
# scripts/vesktop-test-login.sh
# One-time interactive Discord login for a test user, run headed against the
# host's X server. The resulting profile volume is reusable headlessly by
# scripts/voice-integration-harness.sh from any worktree afterward.
# Usage: scripts/vesktop-test-login.sh <user-id>
set -uo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <user-id>" >&2
    exit 1
fi
USER_ID="$1"
VOLUME="vesktop-test-profile-${USER_ID}"
CONTAINER="vesktop-test-login-${USER_ID}"
IMAGE="${VESKTOP_TEST_IMAGE:-vesktop-test:1.6.5}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVAL="$SCRIPT_DIR/devtools-eval.py"

if [[ -z "${DISPLAY:-}" ]]; then
    echo "DISPLAY must be set to the host's X display for interactive login (e.g. :0)" >&2
    exit 1
fi

docker volume inspect "$VOLUME" >/dev/null 2>&1 || docker volume create "$VOLUME" >/dev/null

CONTAINER_TO_STOP=""
cleanup() {
    [[ -n "$CONTAINER_TO_STOP" ]] && docker stop -t 5 "$CONTAINER_TO_STOP" >/dev/null 2>&1
}
trap cleanup EXIT

echo "Launching $IMAGE for interactive login (user-id: $USER_ID)..."
if ! docker run -d --rm --name "$CONTAINER" \
        -e DISPLAY="$DISPLAY" \
        -v /tmp/.X11-unix:/tmp/.X11-unix \
        -v "${VOLUME}:/home/vesktop/.config/vesktop" \
        -p 127.0.0.1::9222 \
        --shm-size=1gb \
        "$IMAGE" >/dev/null; then
    echo "Failed to launch $IMAGE — is another login already running for user-id $USER_ID?" >&2
    exit 1
fi
CONTAINER_TO_STOP="$CONTAINER"

PORT=""
for _ in $(seq 1 30); do
    PORT="$(docker port "$CONTAINER" 9222/tcp 2>/dev/null | head -1 | cut -d: -f2)"
    [[ -n "$PORT" ]] && break
    sleep 1
done
if [[ -z "$PORT" ]]; then
    echo "Container never published the CDP port" >&2
    exit 1
fi
export CDP_PORT="$PORT"

echo "A Vesktop window should now be visible on your display — log in with the test Discord account."
echo "Waiting for login to complete (up to 5 minutes)..."
for _ in $(seq 1 300); do
    OUT="$(python3 "$EVAL" "typeof window.Vencord !== 'undefined' && !!Vencord.Webpack.Common.UserStore?.getCurrentUser?.()" 2>/dev/null)"
    if [[ "$OUT" == "True" ]]; then
        echo "Login detected for user-id $USER_ID. Volume $VOLUME is now authenticated."
        exit 0
    fi
    sleep 1
done

echo "Timed out waiting for login after 5 minutes." >&2
exit 1
