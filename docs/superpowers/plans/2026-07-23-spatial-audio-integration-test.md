# Spatial Audio Button Integration Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write the first integration test for `vc-spatial-audio`, verifying the spatial audio button appears in the account container UI while the user is in a voice call.

**Architecture:** A standalone Python script attaches to the live, already-running Vesktop instance via Chrome DevTools Protocol (reusing the existing `scripts/devtools-eval.py` CDP helper as a subprocess) and queries the real DOM for the button's CSS class. This is a true end-to-end check against the live client, not a mocked/jsdom unit test — there is currently no jest/vitest infrastructure anywhere in Vencord or this plugin, and Discord's webpack modules (`VoiceStateStore`, `UserStore`, etc.) only exist inside the live renderer.

**Tech Stack:** Python 3 stdlib (`subprocess`, `sys`, `pathlib`) only. No new dependencies — `scripts/devtools-eval.py` already handles the CDP/`websockets` connection and is reused unmodified.

## Global Constraints

- Do not modify `scripts/devtools-eval.py` or `scripts/vesktop-debug.sh` — reuse them as-is via subprocess.
- No new pip packages. The new script must only use the Python standard library.
- This test can assume that Vesktop was already launched via `scripts/vesktop-debug.sh` (remote debugging on port 9222), that the `SpatialAudio` plugin is enabled, and that the Vesktop user has successfully connected to a voice channel. It is not headless/CI-safe — this is a documented limitation, not a bug to fix in this plan.
- Button selector under test: `.vc-spatial-button` (from `cl("button")` in `vc-spatial-audio/state.ts`, where `cl = classNameFactory("vc-spatial-")`).

---

### Task 1: Live-client button-presence check script

**Files:**
- Create: `tests/test-spatial-button.py`

**Interfaces:**
- Produces: a CLI script, `python3 tests/test-spatial-button.py`, exit code `0` + `PASS: ...` on stdout when the button is found in the live DOM, exit code `1` + `FAIL: ...` when not found.
- Consumes: `scripts/devtools-eval.py` (unmodified) as a subprocess, passing it a JS boolean expression and reading its stdout (`"true"` / `"false"`).

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Integration test: verify the vc-spatial-audio button appears in a voice call.

Preconditions (manual, one-time per run):
  1. Vesktop is running with remote debugging enabled:
       scripts/vesktop-debug.sh
  2. The SpatialAudio plugin is enabled in Vencord settings.
  3. You are connected to a voice channel in Vesktop.

Usage:
  python3 tests/test-spatial-button.py
"""
import subprocess
import sys
from pathlib import Path

EVAL_SCRIPT = Path(__file__).parent / "devtools-eval.py"
BUTTON_SELECTOR = ".vc-spatial-button"


def button_present() -> bool:
    expression = f"!!document.querySelector('{BUTTON_SELECTOR}')"
    result = subprocess.run(
        [sys.executable, str(EVAL_SCRIPT), expression],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"devtools-eval failed: {result.stderr.strip()}")
    return result.stdout.strip() == "true"


def main() -> None:
    if button_present():
        print("PASS: spatial audio button found in voice call")
        sys.exit(0)
    else:
        print(
            "FAIL: spatial audio button not found — "
            "are you in a voice call with SpatialAudio enabled?"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x tests/test-spatial-button.py`

- [ ] **Step 3: Verify the "red" case — button correctly reported absent**

Manual setup: launch `scripts/vesktop-debug.sh`, log in, enable the `SpatialAudio` plugin, but do **not** join any voice channel.

Run: `python3 tests/test-spatial-button.py`
Expected output: `FAIL: spatial audio button not found — are you in a voice call with SpatialAudio enabled?`
Expected exit code: `1` (check with `echo $?`)

- [ ] **Step 4: Verify the "green" case — button correctly reported present**

Manual setup: in the same running Vesktop, join any voice channel.

Run: `python3 tests/test-spatial-button.py`
Expected output: `PASS: spatial audio button found in voice call`
Expected exit code: `0` (check with `echo $?`)
