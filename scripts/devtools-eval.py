#!/usr/bin/env python3
"""Evaluate a JS expression in the live Vesktop page via CDP and print the result.

Usage:
  devtools-eval.py "expression"       # expression as argument
  devtools-eval.py -f script.js       # read from file
  devtools-eval.py                    # read from stdin
"""
import asyncio
import json
import os
import sys
import urllib.request

import websockets


async def evaluate(ws_url: str, expression: str) -> bool:
    """Send a Runtime.evaluate over the given CDP websocket and print the result.

    Returns False (having already printed an error) if the connection closes
    before a matching response arrives — happens when the chosen page target
    is torn down mid-request, e.g. an early splash/login window during
    Vesktop startup. websockets treats a clean close (code 1000/1001) as a
    normal end of iteration rather than an exception, so that case needs
    explicit handling here or it fails silently with no output at all.
    """
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        }))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("id") != 1:
                continue
            result = msg.get("result", {})
            exc = result.get("exceptionDetails")
            if exc:
                text = exc.get("exception", {}).get("description") or exc.get("text", "unknown error")
                print(f"ERROR: {text}", file=sys.stderr)
                return False
            rv = result.get("result", {})
            if rv.get("type") == "undefined":
                print("(undefined)")
            elif "value" in rv:
                v = rv["value"]
                print(json.dumps(v, indent=2) if isinstance(v, (dict, list)) else v)
            else:
                print(rv.get("description", "(no value)"))
            return True
    print(f"ERROR: CDP connection to {ws_url} closed before Runtime.evaluate responded", file=sys.stderr)
    return False


def find_page(targets):
    return next(
        (t for t in targets if t.get("type") == "page" and "discord.com" in t.get("url", "")),
        next((t for t in targets if t.get("type") == "page"), None),
    )


async def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "-f":
        expression = open(sys.argv[2]).read()
    elif len(sys.argv) >= 2 and sys.argv[1] != "-f":
        expression = " ".join(sys.argv[1:])
    else:
        expression = sys.stdin.read()

    cdp_port = os.environ.get("CDP_PORT", "9222")
    targets = None
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"http://localhost:{cdp_port}/json", timeout=2) as r:
                targets = json.loads(r.read())
            break
        except Exception:
            await asyncio.sleep(1)

    if not targets:
        sys.exit(f"Could not reach Vesktop debug port {cdp_port} after 30 s — is Vesktop running with remote debugging enabled?")

    for attempt in range(2):
        page = find_page(targets)
        if not page:
            sys.exit(f"No page target found. Targets: {[t.get('type') for t in targets]}")
        if await evaluate(page["webSocketDebuggerUrl"], expression):
            return
        if attempt == 0:
            print("Retrying with a fresh target list...", file=sys.stderr)
            with urllib.request.urlopen(f"http://localhost:{cdp_port}/json", timeout=2) as r:
                targets = json.loads(r.read())
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
