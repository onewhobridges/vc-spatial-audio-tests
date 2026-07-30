# Containerized Voice Integration Test Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the system-wide Vesktop dependency in `scripts/voice-integration-harness.sh` with a containerized Vesktop, so multiple git worktrees can run voice integration tests concurrently, each against its own Discord test channel, test user, and plugin build.

**Architecture:** One Docker container per test run, launched and torn down by the existing bash orchestrator (swapping `docker run` in for the current `vesktop-debug.sh` launch). Tests keep running on the host; the container only publishes its CDP port (9222 in-container) to a dynamically-assigned host port, discovered via `docker port` and exported as `CDP_PORT`. Channel ID drives container naming (the concurrency-locking axis, via `docker run --name` collision); user ID drives which persistent login-profile volume gets mounted (the credentials axis); a worktree's `Vencord/dist` build is bind-mounted read-only and pointed to via a `state.json` rewrite in the container's entrypoint.

**Tech Stack:** bash, Docker (confirmed present on this host: `docker --version` → 29.6.2), the existing `scripts/devtools-eval.py` (Python + `websockets`), `debian:bookworm-slim` base image.

## Global Constraints

- Base image: `debian:bookworm-slim`. Build-arg `VESKTOP_VERSION` defaults to `1.6.5`.
- No docker-compose — one service, no dependency graph.
- Every `docker run` of the test image must pass `--shm-size=1gb` (Docker's 64MB default `/dev/shm` crashes Chromium-based apps).
- Container name is exactly `vesktop-test-${VESKTOP_TEST_CHANNEL_ID}` — this is the concurrency guard; do not add separate locking code.
- Profile volume name is exactly `vesktop-test-profile-${VESKTOP_TEST_USER_ID}`, mounted read-write at `/home/vesktop/.config/vesktop`.
- Vencord dist dir is bind-mounted read-only at the fixed in-container path `/vencord-dist`.
- CDP port is fixed at `9222` inside the container; published dynamically via `-p 127.0.0.1::9222` and discovered afterward via `docker port`.
- `devtools-eval.py` and `capture-logs.py` read an optional `CDP_PORT` env var, defaulting to `9222` — direct non-container use must remain unaffected.
- The harness never removes volumes (profile or otherwise) — they are durable state reused across runs.
- No pool-assignment automation or locking beyond the container-name collision and Electron's own profile-directory lock — this matches the existing informal convention of hand-picked, documented IDs.
- `scripts/vesktop-debug.sh` is removed — superseded by the container entrypoint.

## File Structure

- `docker/Dockerfile` (new) — builds the `vesktop-test` image.
- `scripts/vesktop-entrypoint.sh` (new) — copied into the image at build time, runs at every container start; rewrites `state.json`, starts pulseaudio/Xvfb (headless) or reuses a passed-through `DISPLAY` (login mode), execs `vesktop`.
- `scripts/docker-build.sh` (new) — wraps `docker build`, tags as `vesktop-test:<version>`.
- `scripts/vesktop-test-login.sh` (new) — one-time interactive login per test user, headed against the host's X server.
- `scripts/voice-integration-harness.sh` (modified) — container launch/teardown, port discovery, three required env vars, name-collision detection.
- `scripts/devtools-eval.py` (modified) — reads `CDP_PORT` env var, default `9222`.
- `scripts/capture-logs.py` (modified) — reads `CDP_PORT` env var, default `9222`; log filename folds in the channel ID.
- `scripts/vesktop-debug.sh` (removed) — superseded by the container entrypoint.
- `docs/voice-test-accounts.md` (new) — table of user-id ↔ test account and channel-id ↔ test channel conventions.

---

### Task 1: Docker image (Dockerfile, entrypoint, build script)

**Files:**
- Create: `docker/Dockerfile`
- Create: `scripts/vesktop-entrypoint.sh`
- Create: `scripts/docker-build.sh`

**Interfaces:**
- Produces: image tagged `vesktop-test:<version>` (default `vesktop-test:1.6.5`), containing a non-root `vesktop` user with a stable home dir, entrypoint that reads `/vencord-dist` (if mounted) and `DISPLAY` (if set) to decide headless-vs-login mode, and execs `vesktop --remote-debugging-port=9222` on container start.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the entrypoint script**

```bash
#!/bin/bash
# scripts/vesktop-entrypoint.sh
# Copied into the image, runs at every container start (not baked in) so it
# stays correct as mounted volumes (profile, vencord-dist) and DISPLAY vary.
set -euo pipefail

CONFIG_DIR="$HOME/.config/vesktop"
STATE_FILE="$CONFIG_DIR/state.json"

if [[ -d /vencord-dist ]]; then
    mkdir -p "$CONFIG_DIR"
    if [[ -f "$STATE_FILE" ]]; then
        jq '.vencordDir = "/vencord-dist"' "$STATE_FILE" > "$STATE_FILE.tmp"
    else
        jq -n '{vencordDir: "/vencord-dist"}' > "$STATE_FILE.tmp"
    fi
    mv "$STATE_FILE.tmp" "$STATE_FILE"
fi

pulseaudio --start --exit-idle-time=-1
pactl load-module module-null-sink sink_name=vesktop-null >/dev/null 2>&1 || true

# Normal headless runs get no DISPLAY, so we start our own Xvfb.
# vesktop-test-login.sh passes through the host's real DISPLAY + X11 socket
# for interactive login, so DISPLAY is already set in that case — skip Xvfb.
if [[ -z "${DISPLAY:-}" ]]; then
    export DISPLAY=:99
    Xvfb :99 -screen 0 1280x800x24 &
    for _ in $(seq 1 30); do
        [[ -e /tmp/.X11-unix/X99 ]] && break
        sleep 0.5
    done
fi

exec dbus-run-session -- vesktop \
    --no-sandbox \
    --start-minimized \
    --remote-debugging-port=9222 \
    --remote-allow-origins='*' \
    --disable-gpu
```

- [ ] **Step 2: Write the Dockerfile**

```dockerfile
# docker/Dockerfile
ARG VESKTOP_VERSION=1.6.5

FROM debian:bookworm-slim
ARG VESKTOP_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        wget \
        xvfb \
        dbus-x11 \
        pulseaudio \
        pulseaudio-utils \
        fonts-liberation \
        jq \
    && wget -q -O /tmp/vesktop.deb \
        "https://github.com/Vencord/Vesktop/releases/download/v${VESKTOP_VERSION}/vesktop_${VESKTOP_VERSION}_amd64.deb" \
    && apt-get install -y --no-install-recommends /tmp/vesktop.deb \
    && rm -f /tmp/vesktop.deb \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash vesktop
USER vesktop
WORKDIR /home/vesktop

COPY --chown=vesktop:vesktop scripts/vesktop-entrypoint.sh /home/vesktop/vesktop-entrypoint.sh
RUN chmod +x /home/vesktop/vesktop-entrypoint.sh

ENTRYPOINT ["/home/vesktop/vesktop-entrypoint.sh"]
```

Note: `apt-get install -y --no-install-recommends /tmp/vesktop.deb` resolves the `.deb`'s own dependencies (`libgtk-3-0`, `libnotify4`, `libnss3`, `libxss1`, `libxtst6`, `xdg-utils`, `libatspi2.0-0`, `libuuid1`, `libsecret-1-0`) from the already-updated apt cache — confirmed via `dpkg-deb -I` against the real `vesktop_1.6.5_amd64.deb` release asset, no need to list them explicitly.

- [ ] **Step 3: Write the build script**

```bash
#!/bin/bash
# scripts/docker-build.sh
# Builds and tags the Vesktop test image.
# Usage: scripts/docker-build.sh [vesktop-version]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

VESKTOP_VERSION="${1:-1.6.5}"
TAG="vesktop-test:${VESKTOP_VERSION}"

echo "Building $TAG (VESKTOP_VERSION=$VESKTOP_VERSION)..."
docker build \
    --build-arg "VESKTOP_VERSION=$VESKTOP_VERSION" \
    -f "$REPO_ROOT/docker/Dockerfile" \
    -t "$TAG" \
    "$REPO_ROOT"

echo "Built $TAG"
```

- [ ] **Step 4: Build the image**

```bash
chmod +x scripts/docker-build.sh scripts/vesktop-entrypoint.sh
scripts/docker-build.sh
```

Expected: build completes, ends with `Built vesktop-test:1.6.5`. (This downloads ~96MB and installs Vesktop — expect a couple of minutes.)

- [ ] **Step 5: Smoke-test the image standalone (headless, no profile/dist mounts)**

```bash
docker volume create vesktop-smoketest-profile
docker run -d --rm --name vesktop-smoketest \
    -p 127.0.0.1::9222 \
    -v vesktop-smoketest-profile:/home/vesktop/.config/vesktop \
    --shm-size=1gb \
    vesktop-test:1.6.5
sleep 5
PORT="$(docker port vesktop-smoketest 9222/tcp | cut -d: -f2)"
curl -s "http://localhost:$PORT/json" | head -c 300
echo
docker stop -t 5 vesktop-smoketest
docker volume rm vesktop-smoketest-profile
```

Expected: the `curl` prints JSON containing a `"type": "page"` target with a `webSocketDebuggerUrl` (this is Vesktop's CDP endpoint responding — login isn't required for this). `docker stop` succeeds; no error about the container still running afterward.

- [ ] **Step 6: Commit**

```bash
git add docker/Dockerfile scripts/vesktop-entrypoint.sh scripts/docker-build.sh
git commit -m "feat: add Vesktop test image (Dockerfile, entrypoint, build script)"
```

---

### Task 2: `CDP_PORT` support in `devtools-eval.py`

**Files:**
- Modify: `scripts/devtools-eval.py`

**Interfaces:**
- Produces: `devtools-eval.py` reads `os.environ.get("CDP_PORT", "9222")` and hits `http://localhost:<that port>/json` — used by Task 5's harness and by `tests/*.py` (unchanged) which inherit `CDP_PORT` from the harness's exported environment.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Add the env var read**

In `scripts/devtools-eval.py`, add `import os` to the imports and change the hardcoded port:

```python
import asyncio
import json
import os
import sys
import urllib.request
```

Replace:
```python
            with urllib.request.urlopen("http://localhost:9222/json", timeout=2) as r:
```
with:
```python
            with urllib.request.urlopen(f"http://localhost:{cdp_port}/json", timeout=2) as r:
```

And in `main()`, before the retry loop, add:
```python
    cdp_port = os.environ.get("CDP_PORT", "9222")
```

Also update the failure message so it no longer references the removed `vesktop-debug.sh`:
```python
    if not targets:
        sys.exit(f"Could not reach Vesktop debug port {cdp_port} after 30 s — is Vesktop running with remote debugging enabled?")
```

- [ ] **Step 2: Verify default behavior is unchanged**

Precondition: no container running, and a normal host Vesktop process is not required for this check — we're only testing the port-selection logic fails the same way as before when nothing is listening.

```bash
unset CDP_PORT
timeout 5 python3 scripts/devtools-eval.py "true" ; echo "exit: $?"
```

Expected: after the shortened 5s timeout kills it, no Python traceback — the script is still mid-retry-loop (this just confirms it didn't error out immediately trying to read a missing env var). `echo $?` will show `124` from `timeout`, which is fine — this step is only checking there's no `NameError`/`KeyError` on startup.

- [ ] **Step 3: Verify `CDP_PORT` is honored**

Reuse the smoke-test container pattern from Task 1 Step 5:

```bash
docker volume create vesktop-smoketest-profile
docker run -d --rm --name vesktop-smoketest \
    -p 127.0.0.1::9222 \
    -v vesktop-smoketest-profile:/home/vesktop/.config/vesktop \
    --shm-size=1gb \
    vesktop-test:1.6.5
sleep 5
PORT="$(docker port vesktop-smoketest 9222/tcp | cut -d: -f2)"
CDP_PORT="$PORT" python3 scripts/devtools-eval.py "1 + 1"
docker stop -t 5 vesktop-smoketest
docker volume rm vesktop-smoketest-profile
```

Expected: prints `2` (evaluated inside the container's Vesktop page, reached via the dynamically-published port).

- [ ] **Step 4: Commit**

```bash
git add scripts/devtools-eval.py
git commit -m "feat: read CDP_PORT env var in devtools-eval.py, default 9222"
```

---

### Task 3: `CDP_PORT` support + channel-scoped filename in `capture-logs.py`

**Files:**
- Modify: `scripts/capture-logs.py`

**Interfaces:**
- Produces: `capture-logs.py` reads `CDP_PORT` (default `9222`) the same way as Task 2, and names its output `discord.com-${VESKTOP_TEST_CHANNEL_ID}-<timestamp>.log` (falling back to `unknown` if that env var is unset, so direct non-harness use still works).
- Consumes: nothing from other tasks.

- [ ] **Step 1: Add the env var reads**

Add `import os` to imports:

```python
import asyncio
import json
import os
import sys
from datetime import datetime
```

Replace the hardcoded port lookup:
```python
            with urllib.request.urlopen("http://localhost:9222/json", timeout=2) as r:
```
with:
```python
            with urllib.request.urlopen(f"http://localhost:{cdp_port}/json", timeout=2) as r:
```

In `main()`, before the retry loop:
```python
    cdp_port = os.environ.get("CDP_PORT", "9222")
```

Update the failure message:
```python
    if not targets:
        sys.exit(f"Could not reach Vesktop debug port {cdp_port} after 30 s")
```

- [ ] **Step 2: Fold the channel ID into the log filename**

Replace:
```python
    os.makedirs("/home/bridger/sabbatical/logs", exist_ok=True)
    ts = int(datetime.now().timestamp() * 1000)
    log_path = f"/home/bridger/sabbatical/logs/discord.com-{ts}.log"
```
with:
```python
    channel_id = os.environ.get("VESKTOP_TEST_CHANNEL_ID", "unknown")
    os.makedirs("/home/bridger/sabbatical/logs", exist_ok=True)
    ts = int(datetime.now().timestamp() * 1000)
    log_path = f"/home/bridger/sabbatical/logs/discord.com-{channel_id}-{ts}.log"
```

- [ ] **Step 3: Verify against the smoke-test container**

```bash
docker volume create vesktop-smoketest-profile
docker run -d --rm --name vesktop-smoketest \
    -p 127.0.0.1::9222 \
    -v vesktop-smoketest-profile:/home/vesktop/.config/vesktop \
    --shm-size=1gb \
    vesktop-test:1.6.5
sleep 5
PORT="$(docker port vesktop-smoketest 9222/tcp | cut -d: -f2)"
CDP_PORT="$PORT" VESKTOP_TEST_CHANNEL_ID=999 timeout 3 python3 scripts/capture-logs.py
ls /home/bridger/sabbatical/logs/ | grep 'discord.com-999-'
docker stop -t 5 vesktop-smoketest
docker volume rm vesktop-smoketest-profile
```

Expected: a file named `discord.com-999-<timestamp>.log` exists in `/home/bridger/sabbatical/logs/`.

- [ ] **Step 4: Commit**

```bash
git add scripts/capture-logs.py
git commit -m "feat: read CDP_PORT env var and channel-scope the log filename in capture-logs.py"
```

---

### Task 4: `vesktop-test-login.sh` (one-time interactive login)

**Files:**
- Create: `scripts/vesktop-test-login.sh`

**Interfaces:**
- Consumes: image `vesktop-test:1.6.5` from Task 1 (overridable via `VESKTOP_TEST_IMAGE` env var); `scripts/devtools-eval.py`'s `CDP_PORT` support from Task 2.
- Produces: Docker named volume `vesktop-test-profile-<user-id>`, holding an authenticated Vesktop profile, reusable headlessly by Task 5's harness from any worktree.

- [ ] **Step 1: Write the login script**

```bash
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

docker volume inspect "$VOLUME" >/dev/null 2>&1 || docker volume create "$VOLUME" >/dev/null

cleanup() {
    docker stop -t 5 "$CONTAINER" >/dev/null 2>&1
}
trap cleanup EXIT

echo "Launching $IMAGE for interactive login (user-id: $USER_ID)..."
if ! docker run -d --rm --name "$CONTAINER" \
        -e DISPLAY="$DISPLAY" \
        -v /tmp/.X11-unix:/tmp/.X11-unix \
        -v "${VOLUME}:/home/vesktop/.config/vesktop" \
        --shm-size=1gb \
        "$IMAGE" >/dev/null; then
    echo "Failed to launch $IMAGE — is another login already running for user-id $USER_ID?" >&2
    exit 1
fi

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
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/vesktop-test-login.sh
```

- [ ] **Step 3: Verify the login flow end-to-end**

Precondition: an X server the container can attach to (a normal desktop session; `echo $DISPLAY` should print something like `:0` or `:1`), and a throwaway Discord test account's credentials in hand.

```bash
scripts/vesktop-test-login.sh smoketest
```

Expected: a real Vesktop window appears on screen. Complete Discord login in it (QR scan or email/password + 2FA). Within a few seconds of finishing, the script prints `Login detected for user-id smoketest. Volume vesktop-test-profile-smoketest is now authenticated.` and exits 0. Confirm cleanup: `docker ps | grep vesktop-test-login-smoketest` prints nothing.

- [ ] **Step 4: Verify the volume persists login across a subsequent headless run**

```bash
docker run -d --rm --name vesktop-verify-persist \
    -p 127.0.0.1::9222 \
    -v vesktop-test-profile-smoketest:/home/vesktop/.config/vesktop \
    --shm-size=1gb \
    vesktop-test:1.6.5
sleep 8
PORT="$(docker port vesktop-verify-persist 9222/tcp | cut -d: -f2)"
CDP_PORT="$PORT" python3 scripts/devtools-eval.py "!!Vencord.Webpack.Common.UserStore?.getCurrentUser?.()"
docker stop -t 5 vesktop-verify-persist
```

Expected: prints `True` — the headless container came up already logged in, with no interactive step. Leave the `vesktop-test-profile-smoketest` volume in place; Task 7 reuses it.

- [ ] **Step 5: Commit**

```bash
git add scripts/vesktop-test-login.sh
git commit -m "feat: add one-time interactive Discord login script for test users"
```

---

### Task 5: Rewrite `voice-integration-harness.sh` for containers

**Files:**
- Modify: `scripts/voice-integration-harness.sh` (full rewrite of the launch/teardown portions; readiness-wait, injection, guard, join, test-run, and leave logic carry over unchanged in substance)

**Interfaces:**
- Consumes: image `vesktop-test:1.6.5` from Task 1 (overridable via `VESKTOP_TEST_IMAGE`); `CDP_PORT` support from Task 2; profile volumes named `vesktop-test-profile-<user-id>` produced by Task 4; `window.__voiceIntegration.{hasLoggedInUserWithAccess,joinVoiceChannel,currentVoiceChannelId,leaveVoiceChannel}` from `scripts/voice-actions.js` (existing, unmodified).
- Produces: exports `CDP_PORT` into its own environment before running `tests/*.py`, so `subprocess.run` calls in those tests inherit it unchanged.

- [ ] **Step 1: Rewrite the script**

Replace the full contents of `scripts/voice-integration-harness.sh` with:

```bash
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
cleanup() {
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

PUBLISHED="$(docker port "$CONTAINER_NAME" 9222/tcp)"
CDP_PORT="${PUBLISHED##*:}"
export CDP_PORT
echo "Container $CONTAINER_NAME publishing CDP on port $CDP_PORT"

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
```

- [ ] **Step 2: Verify missing-env-var guards**

```bash
env -u VESKTOP_TEST_CHANNEL_ID -u VESKTOP_TEST_USER_ID -u VESKTOP_TEST_VENCORD_DIST_DIR \
    bash scripts/voice-integration-harness.sh; echo "exit: $?"
```
Expected: `VESKTOP_TEST_CHANNEL_ID must be set...`, `exit: 1`.

```bash
VESKTOP_TEST_CHANNEL_ID=999 \
    bash scripts/voice-integration-harness.sh; echo "exit: $?"
```
Expected: `VESKTOP_TEST_USER_ID must be set...`, `exit: 1`.

- [ ] **Step 3: Verify the missing-login-volume guard**

```bash
VESKTOP_TEST_CHANNEL_ID=999 VESKTOP_TEST_USER_ID=nonexistent-user \
VESKTOP_TEST_VENCORD_DIST_DIR=/tmp \
    bash scripts/voice-integration-harness.sh; echo "exit: $?"
```
Expected: `Login volume vesktop-test-profile-nonexistent-user not found — run: scripts/vesktop-test-login.sh nonexistent-user`, `exit: 1`.

- [ ] **Step 4: Verify a full run against the `smoketest` profile from Task 4**

Precondition: build a throwaway `Vencord/dist` dir to mount (its contents don't matter for this check — only that the entrypoint's `state.json` rewrite and mount succeed):

```bash
mkdir -p /tmp/fake-vencord-dist

VESKTOP_TEST_CHANNEL_ID=999 VESKTOP_TEST_USER_ID=smoketest \
VESKTOP_TEST_VENCORD_DIST_DIR=/tmp/fake-vencord-dist \
    bash scripts/voice-integration-harness.sh; echo "exit: $?"
```

Expected: since channel `999` is not a real channel the `smoketest` account has access to, output ends with `No logged-in user with access to channel 999 — exiting cleanly.` and `exit: 0` (this exercises launch, port discovery, readiness wait, injection, and the clean-exit guard path — everything short of an actual join, which needs a real accessible channel). Confirm teardown: `docker ps | grep vesktop-test-999` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add scripts/voice-integration-harness.sh
git commit -m "feat: containerize the voice integration harness"
```

---

### Task 6: Remove `vesktop-debug.sh`, add test-account docs table

**Files:**
- Delete: `scripts/vesktop-debug.sh`
- Create: `docs/voice-test-accounts.md`

**Interfaces:**
- Consumes: nothing from other tasks (pure cleanup + documentation).
- Produces: nothing consumed by other tasks — this is the last piece of file-structure cleanup.

- [ ] **Step 1: Remove the superseded script**

```bash
git rm scripts/vesktop-debug.sh
```

- [ ] **Step 2: Confirm nothing else still references it**

```bash
grep -rn "vesktop-debug" --include="*.sh" --include="*.py" --include="*.md" .
```

Expected: no matches (Task 5's harness rewrite already dropped the only reference, in `scripts/voice-integration-harness.sh`).

- [ ] **Step 3: Write the test-accounts doc table**

```markdown
# Voice Integration Test Accounts & Channels

Conventions for `VESKTOP_TEST_USER_ID` and `VESKTOP_TEST_CHANNEL_ID`. Pick an
unused pair before starting a test run — there's no automated allocation;
this table is the source of truth for what's already claimed.

Two independent axes:
- **User** selects which persistent, pre-authenticated Vesktop profile to
  use (`scripts/vesktop-test-login.sh <user-id>` sets one up once).
- **Channel** is the concurrency-locking axis — two unrelated runs must
  never share a channel ID at the same time, since `vesktop-test-<channel-id>`
  becomes the container name and Docker rejects the second `docker run`.

## Test users (`VESKTOP_TEST_USER_ID` → Discord account)

| user-id | Discord account | Notes |
|---|---|---|
| _(fill in)_ | _(fill in)_ | Logged in via `scripts/vesktop-test-login.sh <user-id>` |

## Test channels (`VESKTOP_TEST_CHANNEL_ID` → channel)

| channel-id | Server / channel name | Notes |
|---|---|---|
| _(fill in)_ | _(fill in)_ | |
```

This is a template to fill in with real Discord IDs/accounts once test accounts exist — automating that setup is an explicit non-goal of the design spec.

- [ ] **Step 4: Commit**

```bash
git add docs/voice-test-accounts.md
git commit -m "chore: remove superseded vesktop-debug.sh, add test-account docs table"
```

---

### Task 7: End-to-end verification (spec's testing strategy)

**Files:** none (verification only — no code changes)

**Interfaces:**
- Consumes: everything from Tasks 1–6, plus real Discord test accounts/channels recorded in `docs/voice-test-accounts.md` (outside this plan's scope to create).

This task requires at least two real, distinct Discord test accounts that are members of a server with a real voice channel, with `scripts/vesktop-test-login.sh` already run for both (Task 4's Step 3), and their IDs recorded in `docs/voice-test-accounts.md` (Task 6). Substitute your real IDs for the placeholders below.

- [ ] **Step 1: Confirm login persists across a headless run (repeat of Task 4 Step 4, now via the full harness)**

```bash
VESKTOP_TEST_CHANNEL_ID=<real accessible channel id> \
VESKTOP_TEST_USER_ID=<user-id 1> \
VESKTOP_TEST_VENCORD_DIST_DIR=/tmp/fake-vencord-dist \
    bash scripts/voice-integration-harness.sh
```
Expected: reaches `Logged-in user has access to channel <id>.`, joins, runs `tests/*.py`, leaves, exits 0.

- [ ] **Step 2: Two concurrent runs, different channel/user/dist-dir triples — confirm independence**

In two separate terminals (or `&` background both):
```bash
VESKTOP_TEST_CHANNEL_ID=<channel A> VESKTOP_TEST_USER_ID=<user 1> \
VESKTOP_TEST_VENCORD_DIST_DIR=/tmp/fake-vencord-dist \
    bash scripts/voice-integration-harness.sh > /tmp/run-a.log 2>&1 &

VESKTOP_TEST_CHANNEL_ID=<channel B> VESKTOP_TEST_USER_ID=<user 2> \
VESKTOP_TEST_VENCORD_DIST_DIR=/tmp/fake-vencord-dist \
    bash scripts/voice-integration-harness.sh > /tmp/run-b.log 2>&1 &

wait
cat /tmp/run-a.log /tmp/run-b.log
```
Expected: both logs show independent successful runs (or clean-exit guards, depending on account access); `docker ps` while both are running shows two containers, `vesktop-test-<channel A>` and `vesktop-test-<channel B>`.

- [ ] **Step 3: Two concurrent runs reusing the same channel ID — confirm the collision message**

```bash
VESKTOP_TEST_CHANNEL_ID=<channel A> VESKTOP_TEST_USER_ID=<user 1> \
VESKTOP_TEST_VENCORD_DIST_DIR=/tmp/fake-vencord-dist \
    bash scripts/voice-integration-harness.sh > /tmp/run-first.log 2>&1 &
FIRST_PID=$!
sleep 3

VESKTOP_TEST_CHANNEL_ID=<channel A> VESKTOP_TEST_USER_ID=<user 2> \
VESKTOP_TEST_VENCORD_DIST_DIR=/tmp/fake-vencord-dist \
    bash scripts/voice-integration-harness.sh; echo "second exit: $?"

wait $FIRST_PID
```
Expected: the second invocation prints `channel <channel A> already has a test running` and exits 1 — not the clean-exit-0 "no access" path. The first run is unaffected by the second's failure.

- [ ] **Step 4: Confirm `tests/test-spatial-button.py` passes unmodified against a containerized instance**

Already exercised implicitly in Steps 1–2 above (the harness's test-runner loop invokes every executable file under `tests/`, including `tests/test-spatial-button.py`, unchanged). Confirm by grepping the captured log:

```bash
grep "test-spatial-button.py" /tmp/run-a.log
```
Expected: shows the `--- test-spatial-button.py ---` / `PASSED` or `FAILED` markers from the harness's existing loop — same format as before containerization.

- [ ] **Step 5: Confirm teardown leaves no orphaned containers and both volumes intact**

```bash
docker ps
```
Expected: empty (no `vesktop-test-*` containers left running) after every run above completes.

```bash
docker volume ls | grep vesktop-test-profile
```
Expected: `vesktop-test-profile-<user 1>` and `vesktop-test-profile-<user 2>` both still present — the harness never removes them.

- [ ] **Step 6: Force a failure and re-confirm teardown**

```bash
VESKTOP_TEST_CHANNEL_ID=<channel A> VESKTOP_TEST_USER_ID=<user 1> \
VESKTOP_TEST_VENCORD_DIST_DIR=/nonexistent-path-does-not-exist \
    bash scripts/voice-integration-harness.sh; echo "exit: $?"
docker ps
```
Expected: `docker run` fails fast (Docker rejects the bind mount of a nonexistent host path), harness exits 1, `docker ps` shows no leftover container.

No commit for this task — it's verification only, confirming Tasks 1–6 meet the spec's testing strategy.
