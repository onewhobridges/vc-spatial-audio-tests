#!/bin/bash
# scripts/voice-integration-harness.sh
# Launches a containerized Vesktop, joins VESKTOP_TEST_CHANNEL_ID if an
# already-logged-in user has access, hands off to run tests/*.py, then tears
# the container down. See
# docs/superpowers/specs/2026-07-29-containerized-voice-test-harness-design.md
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVAL="$SCRIPT_DIR/devtools-eval.py"
VESKTOP_TEST_IMAGE="${VESKTOP_TEST_IMAGE:-vesktop-test:1.6.5}"

if [[ -z "${VESKTOP_TEST_CHANNEL_ID:-}" ]]; then
    echo "VESKTOP_TEST_CHANNEL_ID must be set to the target channel's ID" >&2
    exit 1
fi
if [[ -z "${VESKTOP_TEST_USER_ID:-}" ]]; then
    echo "VESKTOP_TEST_USER_ID must be set to the test user's ID" >&2
    exit 1
fi
if [[ -z "${VESKTOP_TEST_VENCORD_DIST_DIR:-}" ]]; then
    echo "VESKTOP_TEST_VENCORD_DIST_DIR must be set to an absolute path to a Vencord/dist build" >&2
    exit 1
fi
if [[ ! -f "$VESKTOP_TEST_VENCORD_DIST_DIR/vencordDesktopMain.js" ]]; then
    echo "$VESKTOP_TEST_VENCORD_DIST_DIR is not a built Vencord dist (no vencordDesktopMain.js) — run pnpm build" >&2
    exit 1
fi
CHANNEL_ID="$VESKTOP_TEST_CHANNEL_ID"
USER_ID="$VESKTOP_TEST_USER_ID"
PROFILE_VOLUME="vesktop-test-profile-${USER_ID}"
CONTAINER_NAME="vesktop-test-${CHANNEL_ID}"

if ! docker volume inspect "$PROFILE_VOLUME" >/dev/null 2>&1; then
    echo "Login volume $PROFILE_VOLUME not found — run: scripts/vesktop-test-login.sh $USER_ID" >&2
    exit 1
fi

# Only set once docker run actually succeeds, so a name-collision failure
# (someone else's container) never gets stopped by our cleanup.
CONTAINER_NAME_TO_STOP=""
CAPTURE_LOG_PID=""
cleanup() {
    [[ -n "$CAPTURE_LOG_PID" ]] && kill "$CAPTURE_LOG_PID" >/dev/null 2>&1
    [[ -n "$CONTAINER_NAME_TO_STOP" ]] && docker stop -t 5 "$CONTAINER_NAME_TO_STOP" >/dev/null 2>&1
}
trap cleanup EXIT

echo "Launching $VESKTOP_TEST_IMAGE as $CONTAINER_NAME..."
RUN_ERR="$(mktemp)"
if ! docker run -d --rm --name "$CONTAINER_NAME" \
        -p 127.0.0.1::9222 \
        -v "${PROFILE_VOLUME}:/home/vesktop/.config/vesktop" \
        -v "${VESKTOP_TEST_VENCORD_DIST_DIR}:/vencord-dist:ro" \
        --shm-size=1gb \
        "$VESKTOP_TEST_IMAGE" >/dev/null 2>"$RUN_ERR"; then
    if grep -q "is already in use" "$RUN_ERR"; then
        echo "channel $CHANNEL_ID already has a test running" >&2
    else
        echo "docker run failed:" >&2
        cat "$RUN_ERR" >&2
    fi
    rm -f "$RUN_ERR"
    exit 1
fi
rm -f "$RUN_ERR"
CONTAINER_NAME_TO_STOP="$CONTAINER_NAME"

CDP_PORT=""
for _ in $(seq 1 30); do
    CDP_PORT="$(docker port "$CONTAINER_NAME" 9222/tcp 2>/dev/null | head -1 | cut -d: -f2)"
    [[ -n "$CDP_PORT" ]] && break
    sleep 1
done
if [[ -z "$CDP_PORT" ]]; then
    echo "Container never published the CDP port" >&2
    exit 1
fi
export CDP_PORT
echo "Container $CONTAINER_NAME publishing CDP on port $CDP_PORT"

python3 "$SCRIPT_DIR/capture-logs.py" >/tmp/vesktop-capture-logs-"$CHANNEL_ID".log 2>&1 &
CAPTURE_LOG_PID=$!

# devtools-eval.py itself retries the CDP port for ~30s.
if [[ "$(python3 "$EVAL" "true" 2>/tmp/vesktop-debug-eval-err.log)" != "True" ]]; then
    echo "Vesktop never came up on the CDP port — see /tmp/vesktop-debug-eval-err.log" >&2
    exit 1
fi

echo "Waiting for Vencord to finish loading in the page..."
READY=""
for _ in $(seq 1 30); do
    OUT="$(python3 "$EVAL" "typeof window.Vencord !== 'undefined' && typeof Vencord.Webpack.Common.UserStore?.getCurrentUser === 'function'" 2>/dev/null)"
    if [[ "$OUT" == "True" ]]; then
        READY=1
        break
    fi
    sleep 1
done
if [[ -z "$READY" ]]; then
    echo "Vencord never finished loading in the page after 30s" >&2
    exit 1
fi

echo "Injecting voice-actions helpers..."
if [[ "$(python3 "$EVAL" -f "$SCRIPT_DIR/voice-actions.js")" != "True" ]]; then
    echo "Failed to inject scripts/voice-actions.js" >&2
    exit 1
fi

echo "voice-actions.js injected; window.__voiceIntegration is ready."

echo "Waiting for a logged-in user with access to channel $CHANNEL_ID..."
GUARD=""
for _ in $(seq 1 15); do
    OUT="$(python3 "$EVAL" "window.__voiceIntegration.hasLoggedInUserWithAccess('$CHANNEL_ID')" 2>/dev/null)"
    if [[ "$OUT" == "True" ]]; then
        GUARD=1
        break
    fi
    sleep 1
done
if [[ -z "$GUARD" ]]; then
    echo "No logged-in user with access to channel $CHANNEL_ID — exiting cleanly."
    exit 0
fi
echo "Logged-in user has access to channel $CHANNEL_ID."

echo "Joining voice channel $CHANNEL_ID..."
python3 "$EVAL" "window.__voiceIntegration.joinVoiceChannel('$CHANNEL_ID')" >/dev/null

JOINED=""
for _ in $(seq 1 10); do
    CUR="$(python3 "$EVAL" "window.__voiceIntegration.currentVoiceChannelId()" 2>/dev/null)"
    if [[ "$CUR" == "$CHANNEL_ID" ]]; then
        JOINED=1
        break
    fi
    sleep 1
done
if [[ -z "$JOINED" ]]; then
    echo "Never confirmed joining channel $CHANNEL_ID (last seen: $CUR)" >&2
    exit 1
fi

TESTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/tests"
TEST_FAILURES=0
echo "Running integration tests in $TESTS_DIR..."
for TEST_FILE in "$TESTS_DIR"/*; do
    [[ -f "$TEST_FILE" && -x "$TEST_FILE" ]] || continue
    echo "--- $(basename "$TEST_FILE") ---"
    if "$TEST_FILE"; then
        echo "--- $(basename "$TEST_FILE") PASSED ---"
    else
        echo "--- $(basename "$TEST_FILE") FAILED ---"
        TEST_FAILURES=$((TEST_FAILURES + 1))
    fi
done

echo "Leaving voice channel..."
python3 "$EVAL" "window.__voiceIntegration.leaveVoiceChannel()" >/dev/null

if [[ "$TEST_FAILURES" -gt 0 ]]; then
    echo "$TEST_FAILURES test(s) failed." >&2
    exit 1
fi
exit 0
