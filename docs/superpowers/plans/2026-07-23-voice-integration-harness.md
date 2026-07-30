# Voice Integration Test Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A script that launches Vesktop, gets an already-authenticated test account into a pre-designated voice channel, hands control back to the caller to run integration tests (e.g. `tests/test-spatial-button.py`), then tears everything down.

**Architecture:** A bash orchestrator (`scripts/voice-integration-harness.sh`) drives the existing CDP harness (`scripts/vesktop-debug.sh` + `scripts/devtools-eval.py`, see `project-vesktop-cdp-debugging` memory) exactly the way `drag-debug*.js` + `simulate-drag*.py` already do: inject a JS helper file that hangs functions off `window`, then call those functions via one-shot `devtools-eval.py` invocations. The helper file (`scripts/voice-actions.js`) wraps four Vencord webpack internals — `UserStore`, `ChannelStore`, `PermissionStore`/`PermissionsBits`, and a `findByProps("selectVoiceChannel")` module — grounded against actual call sites in `Vencord/src/plugins/userVoiceShow/components.tsx` and `vc-spatial-audio/index.tsx` (see Task 1 notes). This harness does **not** perform Discord login — it assumes a human has already authenticated Vesktop with a test account that has channel access; the harness only checks for and uses that existing session.

**Tech Stack:** bash, the existing `scripts/devtools-eval.py` (Python + `websockets`, unmodified), vanilla JS injected via CDP `Runtime.evaluate`.

## Global Constraints

- Do not modify `scripts/devtools-eval.py`, `scripts/vesktop-debug.sh`, or `scripts/capture-logs.py` — reuse them as-is via subprocess/background job, same rule the sibling `2026-07-23-spatial-audio-integration-test.md` plan already established.
- Target channel is configured via required env var `VESKTOP_TEST_CHANNEL_ID` (a Discord channel-ID string). No CLI-arg parsing, no config file — YAGNI, this is a single-purpose dev/test script.
- "Exit cleanly" means: print a one-line reason to stdout, exit code `0`, and still tear down every process this script started (no orphaned `vesktop`/`vesktop-debug.sh`/`capture-logs.py`). A genuine launch failure (CDP port never comes up) is a different condition — exit code `1`, since that's not the documented guard case.
- "Prints to console" means the harness's own stdout (`echo`), not a `console.log` inside the Discord renderer — the whole point of the print is to hand control back to a human/CI runner watching the terminal.
- `selectVoiceChannel(null)` as the "leave channel" call is **not** confirmed anywhere in this codebase (only the join direction, `selectVoiceChannel(channelId)`, is — see Task 1). Task 3 includes an explicit manual verification step for this; if it doesn't disconnect, the fallback is noted there.

## File Structure

- `scripts/voice-actions.js` (new) — injected instrumentation, same role as `drag-debug3.js`: pure functions hung off `window.__voiceIntegration`, no side effects until called.
- `scripts/voice-integration-harness.sh` (new) — orchestrator: launch, wait-for-ready, inject, guard, join, print, leave, teardown.

---

### Task 1: Injected helper library + launch/readiness wait

**Files:**
- Create: `scripts/voice-actions.js`
- Create: `scripts/voice-integration-harness.sh` (launch + readiness + injection portion only)

**Interfaces:**
- Produces: `window.__voiceIntegration = { hasLoggedInUserWithAccess(channelId), joinVoiceChannel(channelId), leaveVoiceChannel(), currentVoiceChannelId() }`, installed by evaluating `scripts/voice-actions.js` through `devtools-eval.py -f`.
- Consumes: `scripts/vesktop-debug.sh` (unmodified, backgrounded), `scripts/devtools-eval.py` (unmodified, invoked per-call via `python3`).

- [x] **Step 1: Write the injected helper library**

```js
// scripts/voice-actions.js
// Injected via: python3 devtools-eval.py -f scripts/voice-actions.js
// Hangs helper functions off window.__voiceIntegration for the orchestrator
// to call one at a time via one-shot devtools-eval invocations.
(function () {
    const { UserStore, ChannelStore, PermissionStore, PermissionsBits, SelectedChannelStore, ChannelRouter } =
        Vencord.Webpack.Common;
    const { selectVoiceChannel } = Vencord.Webpack.findByProps("selectVoiceChannel", "selectChannel");

    function hasLoggedInUserWithAccess(channelId) {
        const user = UserStore && UserStore.getCurrentUser && UserStore.getCurrentUser();
        if (!user) return false;
        const channel = ChannelStore && ChannelStore.getChannel && ChannelStore.getChannel(channelId);
        if (!channel) return false;
        return !!(
            PermissionStore.can(PermissionsBits.VIEW_CHANNEL, channel) &&
            PermissionStore.can(PermissionsBits.CONNECT, channel)
        );
    }

    function joinVoiceChannel(channelId) {
        ChannelRouter.transitionToChannel(channelId);
        selectVoiceChannel(channelId);
        return true;
    }

    function leaveVoiceChannel() {
        selectVoiceChannel(null);
        return true;
    }

    function currentVoiceChannelId() {
        return SelectedChannelStore.getVoiceChannelId() || null;
    }

    window.__voiceIntegration = { hasLoggedInUserWithAccess, joinVoiceChannel, leaveVoiceChannel, currentVoiceChannelId };
    return true;
})();
```

- [x] **Step 2: Write the orchestrator's launch + readiness-wait + injection portion**

```bash
#!/bin/bash
# scripts/voice-integration-harness.sh
# Launches Vesktop, joins VESKTOP_TEST_CHANNEL_ID if an already-logged-in
# user has access, hands off to the caller, then tears everything down.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVAL="$SCRIPT_DIR/devtools-eval.py"

if [[ -z "${VESKTOP_TEST_CHANNEL_ID:-}" ]]; then
    echo "VESKTOP_TEST_CHANNEL_ID must be set to the target channel's ID" >&2
    exit 1
fi
CHANNEL_ID="$VESKTOP_TEST_CHANNEL_ID"

VESKTOP_DEBUG_PID=""
cleanup() {
    if [[ -n "$VESKTOP_DEBUG_PID" ]]; then
        PGID="$(ps -o pgid= -p "$VESKTOP_DEBUG_PID" 2>/dev/null | tr -d ' ')"
        [[ -n "$PGID" ]] && kill -TERM "-$PGID" 2>/dev/null
    fi
    pkill -x vesktop 2>/dev/null
}
trap cleanup EXIT

echo "Launching vesktop-debug.sh..."
setsid "$SCRIPT_DIR/vesktop-debug.sh" >/tmp/vesktop-debug-harness.log 2>&1 &
VESKTOP_DEBUG_PID=$!

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
```

- [x] **Step 3: Run it standalone to verify launch + readiness + injection**

Manual precondition: Vesktop must not already be logged out — a normal, previously-authenticated Vesktop profile is fine (no voice channel join needed yet for this step).

Run:
```bash
VESKTOP_TEST_CHANNEL_ID=123 bash scripts/voice-integration-harness.sh
```

Expected stdout, in order:
```
Launching vesktop-debug.sh...
Waiting for Vencord to finish loading in the page...
Injecting voice-actions helpers...
voice-actions.js injected; window.__voiceIntegration is ready.
```
Then, since the script has no further steps yet, it exits and `cleanup` fires — confirm with `pgrep -x vesktop` (should print nothing) within a couple seconds of the script exiting.

- [x] **Step 4: Commit**

```bash
git add scripts/voice-actions.js scripts/voice-integration-harness.sh
git commit -m "feat: add voice integration harness launch/readiness/injection"
```

---

### Task 2: Login/access guard with clean-exit path

**Files:**
- Modify: `scripts/voice-integration-harness.sh` (append guard check after injection)

**Interfaces:**
- Consumes: `window.__voiceIntegration.hasLoggedInUserWithAccess(channelId)` from Task 1, called as `python3 devtools-eval.py "window.__voiceIntegration.hasLoggedInUserWithAccess('$CHANNEL_ID')"`.

- [x] **Step 1: Append the guard check**

Add immediately after the "voice-actions.js injected" line from Task 1 Step 2:

```bash
GUARD="$(python3 "$EVAL" "window.__voiceIntegration.hasLoggedInUserWithAccess('$CHANNEL_ID')")"
if [[ "$GUARD" != "True" ]]; then
    echo "No logged-in user with access to channel $CHANNEL_ID — exiting cleanly."
    exit 0
fi
echo "Logged-in user has access to channel $CHANNEL_ID."
```

- [x] **Step 2: Verify the guard's "no access" path**

Manual setup: log Vesktop out (or leave it on an account that isn't a member of the target channel's server), then run with a channel ID that account cannot see:

```bash
VESKTOP_TEST_CHANNEL_ID=<channel your test account cannot see> bash scripts/voice-integration-harness.sh
echo "exit code: $?"
```

Expected: last line of output is `No logged-in user with access to channel ... — exiting cleanly.`, exit code `0`, and `pgrep -x vesktop` prints nothing shortly after.

- [x] **Step 3: Verify the guard's "has access" path**

Manual setup: log Vesktop in with the test account, on a server it belongs to. Use that server's real channel ID.

```bash
VESKTOP_TEST_CHANNEL_ID=<real accessible channel id> bash scripts/voice-integration-harness.sh
```

Expected: last line is `Logged-in user has access to channel <id>.`, script then falls off the end (Task 3 not implemented yet) and exits 0 via cleanup.

- [x] **Step 4: Commit**

```bash
git add scripts/voice-integration-harness.sh
git commit -m "feat: guard voice join on logged-in user with channel access"
```

---

### Task 3: Join, hand off, leave, full teardown

**Files:**
- Modify: `scripts/voice-integration-harness.sh` (append join/print/leave after the guard)

**Interfaces:**
- Consumes: `window.__voiceIntegration.joinVoiceChannel(channelId)`, `.currentVoiceChannelId()`, `.leaveVoiceChannel()` from Task 1.

- [x] **Step 1: Append join, wait-for-join, print, leave**

Add after the `echo "Logged-in user has access..."` line from Task 2:

```bash
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

echo "Run integration tests here"

echo "Leaving voice channel..."
python3 "$EVAL" "window.__voiceIntegration.leaveVoiceChannel()" >/dev/null
```

`cleanup` (already installed via `trap` in Task 1) runs automatically after this on script exit — no explicit teardown call needed here.

- [x] **Step 2: Verify the full happy path**

Manual setup: Vesktop logged in with a test account that has access to a real voice channel it isn't currently connected to.

```bash
VESKTOP_TEST_CHANNEL_ID=<real accessible channel id> bash scripts/voice-integration-harness.sh
```

Expected stdout, in order (with the readiness/injection lines from Task 1 preceding them):
```
Logged-in user has access to channel <id>.
Joining voice channel <id>...
Run integration tests here
Leaving voice channel...
```
While the script is paused between "Joining" and "Run integration tests here" is printed, visually confirm in the Vesktop window that it actually joined the target voice channel (participant list, connected indicator).

After "Leaving voice channel..." prints and the script exits, confirm in the Vesktop window that it has disconnected from voice. **If it has not disconnected**, `selectVoiceChannel(null)` is not sufficient in the currently-installed Discord/Vencord version — as flagged in Global Constraints, this call site is unverified in this codebase. Fall back to `Vencord.Webpack.findByProps("toggleSelfDeaf", "disconnect").disconnect()` (search for a module exporting a bare `disconnect` alongside voice-settings toggles) and re-verify.

Finally confirm teardown: `pgrep -x vesktop` prints nothing within a couple seconds of the script exiting.

- [x] **Step 3: Verify the timeout path (join never confirmed)**

Manual setup: temporarily set `CHANNEL_ID` to a syntactically-valid but nonexistent channel ID that still passes the guard check (e.g. reuse a real accessible channel ID for the guard, but this is hard to fake cleanly — alternatively, disconnect network/voice momentarily during the join window) — if this is impractical to trigger manually, code-review the loop logic instead: confirm it exits 1 with a clear stderr message after 10s and that `cleanup` still runs (`pgrep -x vesktop` empty afterward).

- [x] **Step 4: Commit**

```bash
git add scripts/voice-integration-harness.sh
git commit -m "feat: join/print/leave the target voice channel in the integration harness"
```
