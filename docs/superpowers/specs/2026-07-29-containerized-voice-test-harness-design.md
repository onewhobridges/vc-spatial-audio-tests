# Containerized Voice Integration Test Harness — Design

**Goal:** Replace the system-wide Vesktop dependency in `scripts/voice-integration-harness.sh` with a containerized Vesktop, so that multiple git worktrees (human or agent-driven) can run voice integration tests concurrently, each against its own Discord test channel, test user, and plugin build, without contending for one shared desktop install.

**Non-goals:** Automating creation of Discord test accounts. Verifying real audio playback/capture (see "Future: real audio verification" below — this design only carries the seam for it). Locking/coordination beyond the natural collisions described below.

## Architecture

One Docker container per test run, launched and torn down by a bash orchestrator, the same shape as today's script — just swapping a `docker run` for the current `vesktop-debug.sh` launch. No docker-compose: there's one service with no dependency graph, so compose's declarative features don't earn their keep here.

Tests continue to run **on the host**, not inside the container. The container's CDP debug port (9222) is published to a host port that Docker assigns dynamically (`-p 127.0.0.1::9222`), discovered afterward via `docker port`. `scripts/devtools-eval.py` and `scripts/capture-logs.py` keep working close to unchanged — they just read which port to hit from an environment variable instead of hardcoding `9222`. This means `tests/*.py` need no changes at all: the harness exports the port into its own environment before invoking test files, and `subprocess.run` calls inherit it.

## Identity and concurrency

Two independent axes, deliberately kept separate:

- **Channel (`VESKTOP_TEST_CHANNEL_ID`, existing)** is the concurrency-locking axis. Two *unrelated* test runs must never occupy the same channel at once — they'd corrupt each other's participant-list assertions. The container is named `vesktop-test-${VESKTOP_TEST_CHANNEL_ID}`, so `docker run --name` collision becomes the "this channel is already under test" guard, for free, no separate locking code needed. When multi-user-per-channel tests are added later (multiple containers, each a different user, all joining one channel on purpose — a likely future need per the user), naming extends to `vesktop-test-${VESKTOP_TEST_CHANNEL_ID}-${VESKTOP_TEST_USER_ID}`, still channel-anchored so the guard keeps catching cross-run collisions while permitting intentional same-run multi-user containers.
- **User (`VESKTOP_TEST_USER_ID`, new)** selects which persistent, pre-authenticated Vesktop profile to use — a Docker named volume, `vesktop-test-profile-${VESKTOP_TEST_USER_ID}`, mounted read-write at `/home/vesktop/.config/vesktop`. This is a credentials concern, orthogonal to which channel is being tested.

No pool-assignment automation or locking is built for either axis — this matches the existing informal convention where channel IDs are already picked and documented by hand. Two backstops exist without any code: the channel-keyed container name (above), and the fact that Electron writes a singleton lock into a profile directory, so two containers mounting the same user's volume read-write at once would fail to both start cleanly — the same way two Discord clients can't share one profile today. Docs get a small table mapping user-id ↔ test Discord account and channel-id ↔ test channel, for humans/agents to pick an unused pair from.

## Login/credentials flow

Discord login happens once per test user, interactively, outside of normal test runs:

`scripts/vesktop-test-login.sh <user-id>`:
1. Creates the named volume `vesktop-test-profile-<user-id>` if absent.
2. Runs the container with the host's X11 socket and `DISPLAY` passed through (instead of the headless `Xvfb` used for normal runs), so a human sees a real Vesktop window and completes Discord login (QR-code scan or email/password + 2FA) — whatever the app's own login UI supports.
3. Polls `UserStore.getCurrentUser()` via the existing CDP eval helper; once non-null, prints success and stops the container.
4. The volume now holds an authenticated session for that user-id, reusable headlessly from any worktree afterward — it isn't tied to the worktree that created it.

If a session later expires or gets logged out, re-running this script for that user-id refreshes it. There's no automatic re-auth.

The main harness checks `docker volume inspect vesktop-test-profile-${VESKTOP_TEST_USER_ID}` before launching and fails fast with a pointer to this script if the volume doesn't exist, rather than trying to proceed unauthenticated.

## Plugin build per worktree

New required env var: `VESKTOP_TEST_VENCORD_DIST_DIR`, an absolute host path to a worktree's `Vencord/dist` build output (produced by the existing, unchanged `pnpm build` workflow). The harness bind-mounts it read-only to a fixed in-container path, `/vencord-dist`.

Vesktop reads which custom Vencord build to load from `state.json` inside the profile (`vencordDir` field), and that file lives in the *shared, persistent* profile volume — so the container's entrypoint script rewrites `vencordDir` to `/vencord-dist` on every container start (cheap, idempotent). This means each worktree just points its own `VESKTOP_TEST_VENCORD_DIST_DIR` at its own build, independently — no shared state or collision risk between worktrees on this axis.

## Image

- **Base**: `debian:bookworm-slim`.
- **Build-time**: `VESKTOP_VERSION` build-arg (default `1.6.5`, matching the current host install). The Dockerfile downloads that pinned `.deb` from Vesktop's GitHub releases and installs it, rather than tracking a moving `latest` — reproducible builds; bump the arg deliberately to test against a newer Vesktop.
- **Extra packages**: `xvfb` (virtual display), `dbus-x11` (Electron apps commonly need a D-Bus session; the host has a system one, the container doesn't), `pulseaudio` (a dummy null sink so Discord's audio-device enumeration doesn't error on startup — presence only, not real audio I/O), plus the usual fonts/GTK/NSS/X11 shared libs Electron needs headless.
- **User**: non-root `vesktop`, fixed home dir, so the profile mount path is stable.
- **Entrypoint** (runs every container start, not baked in, so it stays correct as mounted volumes vary):
  1. If `/vencord-dist` is mounted, rewrite `~/.config/vesktop/state.json`'s `vencordDir` to `/vencord-dist`.
  2. Start `pulseaudio` with a null sink.
  3. Start `Xvfb :99`, wait for the socket.
  4. `exec dbus-run-session -- vesktop --no-sandbox --start-minimized --remote-debugging-port=9222 --remote-allow-origins='*' --disable-gpu` with `DISPLAY=:99`. `--no-sandbox` because Chromium's setuid sandbox doesn't work in an unprivileged container without extra seccomp/cap plumbing.
- **Run-time requirement** (not baked into the image): the harness's `docker run` must pass `--shm-size=1gb` — Docker's 64MB default `/dev/shm` reliably crashes Chromium-based apps.
- **Location**: `docker/Dockerfile`, with `scripts/docker-build.sh` wrapping `docker build` and tagging the image (e.g. `vesktop-test:1.6.5`).

**Implementation addendum** (two accepted deviations found during build, not
covered by the entrypoint description above): (a) Vesktop's bundled Chromium
ignores `--remote-debugging-address` and always binds its CDP server to
loopback only, so the entrypoint runs `vesktop` on an internal port and
relays it to the container-facing port 9222 with `socat`, rather than having
Vesktop publish 9222 directly. (b) Since this repo's whole purpose is
exercising the SpatialAudio plugin, and Vencord doesn't persist a plugin's
default-disabled state to disk, the entrypoint unconditionally force-enables
`plugins.SpatialAudio.enabled` in `settings.json` on every container start.

## Harness script changes

`scripts/voice-integration-harness.sh`:

1. Validates three env vars up front: `VESKTOP_TEST_CHANNEL_ID` (existing), `VESKTOP_TEST_USER_ID` (new), `VESKTOP_TEST_VENCORD_DIST_DIR` (new).
2. Checks the login volume exists (see above).
3. Launches: `docker run -d --rm --name vesktop-test-${VESKTOP_TEST_CHANNEL_ID} -p 127.0.0.1::9222 -v vesktop-test-profile-${VESKTOP_TEST_USER_ID}:/home/vesktop/.config/vesktop -v "$VESKTOP_TEST_VENCORD_DIST_DIR:/vencord-dist:ro" --shm-size=1gb vesktop-test:<tag>`. `--rm` means teardown is just a stop, no separate remove step.
4. If `docker run` fails on the name collision, that's a new, distinct failure mode from the existing "no access" guard: exit 1 with "channel $VESKTOP_TEST_CHANNEL_ID already has a test running" — not the clean exit-0 path, which stays specific to "no logged-in access."
5. Discovers the published port via `docker port vesktop-test-${VESKTOP_TEST_CHANNEL_ID} 9222/tcp` and exports it as `CDP_PORT`.
6. Everything downstream — readiness wait for Vencord to load, injecting `voice-actions.js`, the `hasLoggedInUserWithAccess` guard, join/leave, running `tests/*.py` — is unchanged in substance; those steps already go through `devtools-eval.py`, which now targets the discovered port.
7. Teardown: `trap cleanup EXIT` stays, swapping `pkill -x vesktop` for `docker stop -t 5 vesktop-test-${VESKTOP_TEST_CHANNEL_ID}`. Volumes are never removed by the harness — they're durable login state, reused across runs.

`scripts/devtools-eval.py` and `scripts/capture-logs.py`: both read an optional `CDP_PORT` env var (default `9222`, so direct non-container use is unaffected). This is a deliberate, narrow exception to the original plan's "don't modify these files" constraint — necessary to support concurrent containers. `capture-logs.py`'s output filename also folds in the channel ID (`discord.com-${CHANNEL_ID}-<timestamp>.log`) for readability across concurrent runs.

## File structure

- `docker/Dockerfile` (new)
- `scripts/docker-build.sh` (new) — builds and tags the image
- `scripts/vesktop-entrypoint.sh` (new) — copied into the image, runs at container start
- `scripts/vesktop-test-login.sh` (new) — one-time interactive login per test user
- `scripts/voice-integration-harness.sh` (modified) — container launch/teardown, port discovery, new env vars
- `scripts/devtools-eval.py` (modified) — `CDP_PORT` env var support
- `scripts/capture-logs.py` (modified) — `CDP_PORT` env var support, channel-scoped log filename
- `scripts/vesktop-debug.sh` (removed) — superseded by the container entrypoint
- Docs: a short table of user-id ↔ test account and channel-id ↔ test channel conventions

## Error handling summary

| Condition | Behavior |
|---|---|
| Required env var missing | exit 1, same as today |
| Login volume doesn't exist | exit 1, points at `vesktop-test-login.sh` |
| Channel already under test (container name collision) | exit 1, new distinct message |
| CDP port never comes up | exit 1, same as today (now reading from the discovered container port) |
| Vencord never finishes loading | exit 1, same as today |
| No logged-in user with channel access | exit 0, clean-exit guard, same as today |
| Join never confirmed | exit 1, same as today |
| Any exit path | container stopped via trap, profile/build volumes untouched |

## Testing strategy

Same manual-verification style as the original harness plan (this codebase has no automated test-of-the-test-harness):

- Build the image, run `vesktop-test-login.sh` once for a throwaway test user, confirm the profile volume persists login across a subsequent headless run.
- Run two concurrent invocations with different channel/user/dist-dir triples from two worktrees; confirm both complete independently and neither's container/volume interferes with the other.
- Run two concurrent invocations that intentionally reuse the same channel ID; confirm the second fails fast with the new collision message instead of silently joining the same channel twice.
- Confirm `tests/test-spatial-button.py` passes unmodified against a containerized instance.
- Confirm teardown leaves no orphaned containers (`docker ps` empty) and both volumes intact after a normal run, a guard clean-exit, and a forced failure.

## Future: real audio verification

Out of scope now, but the seam this design leaves: swap the dummy PulseAudio null sink for a named sink with a monitor source, and tests would `parec` (or similar) from it instead of only querying CDP/webpack state. Container naming, volumes, and port-discovery are all unaffected by that change.
