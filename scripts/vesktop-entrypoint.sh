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
elif [[ -f "$STATE_FILE" ]]; then
    # /vencord-dist isn't mounted this run (e.g. vesktop-test-login.sh never
    # mounts it), but state.json may still have a stale vencordDir from a
    # previous run that did mount it. Clear it so Vesktop doesn't try to load
    # a Vencord build from a path that doesn't exist in this container.
    jq 'del(.vencordDir)' "$STATE_FILE" > "$STATE_FILE.tmp"
    mv "$STATE_FILE.tmp" "$STATE_FILE"
fi

# SpatialAudio is the plugin under test in this harness; Vencord defaults it to
# disabled and doesn't persist its default-false state to disk until something
# writes it. Force it enabled unconditionally so tests can run without manual
# configuration, regardless of whether /vencord-dist was mounted (login flow
# doesn't mount it, but still needs the setting for test runs).
SETTINGS_FILE="$CONFIG_DIR/settings/settings.json"
mkdir -p "$CONFIG_DIR/settings"
if [[ -f "$SETTINGS_FILE" ]]; then
    jq '.plugins.SpatialAudio.enabled = true' "$SETTINGS_FILE" > "$SETTINGS_FILE.tmp"
else
    jq -n '{plugins: {SpatialAudio: {enabled: true}}}' > "$SETTINGS_FILE.tmp"
fi
mv "$SETTINGS_FILE.tmp" "$SETTINGS_FILE"

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

# Vesktop's bundled Chromium always binds its DevTools/CDP server to
# 127.0.0.1 and ignores --remote-debugging-address, so a plain `docker run
# -p host::9222` can't reach it directly (the port-publish path delivers
# packets addressed to the container's own interface, and Vesktop refuses
# anything but true loopback). Have Vesktop listen loopback-only on an
# internal port, then relay the container-facing port 9222 to it with socat
# so external CDP clients (via `-p 127.0.0.1::9222`) can connect.
socat TCP-LISTEN:9222,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9223 &

# dbus-run-session becomes PID 1 here, but it does not forward SIGTERM to the
# vesktop child it launches — `docker stop` would otherwise sit out the full
# grace period and then SIGKILL the whole container, hard-killing vesktop
# before Chromium's Local Storage commit timer flushes session data (e.g. a
# freshly completed login) to disk. Vesktop itself quits cleanly on SIGTERM,
# so run it in the background and forward the signal explicitly.
dbus-run-session -- vesktop \
    --no-sandbox \
    --start-minimized \
    --remote-debugging-port=9223 \
    --remote-allow-origins='*' \
    --disable-gpu &
VESKTOP_WRAPPER_PID=$!

forward_term() {
    pkill -TERM -f 'vesktop --no-sandbox --start-minimized' 2>/dev/null || true
    wait "$VESKTOP_WRAPPER_PID"
    exit $?
}
trap forward_term TERM INT

wait "$VESKTOP_WRAPPER_PID"
