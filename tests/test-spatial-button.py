#!/usr/bin/env python3
"""Integration test: verify the vc-spatial-audio button appears in a voice call.

Preconditions:
  1. You are connected to a voice channel in Vesktop.

Usage:
  python3 scripts/test-spatial-button.py
"""
import subprocess
import sys
from pathlib import Path

EVAL_SCRIPT = Path(__file__).parent.parent / "scripts" / "devtools-eval.py"
BUTTON_SELECTOR = ".vc-spatial-button"


def button_present() -> bool:
    expression = f"!!document.querySelector('{BUTTON_SELECTOR}')"
    result = subprocess.run(
        [sys.executable, str(EVAL_SCRIPT), expression],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"devtools-eval failed: {result.stderr.strip()}")
    return result.stdout.strip() == "True"


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
