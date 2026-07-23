#!/usr/bin/env python3
"""Evaluate a JS expression in the live Vesktop page via CDP and print the result.

Usage:
  devtools-eval.py "expression"       # expression as argument
  devtools-eval.py -f script.js       # read from file
  devtools-eval.py                    # read from stdin
"""
import asyncio
import json
import sys
import urllib.request

import websockets


async def evaluate(ws_url: str, expression: str) -> None:
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
                sys.exit(1)
            rv = result.get("result", {})
            if rv.get("type") == "undefined":
                print("(undefined)")
            elif "value" in rv:
                v = rv["value"]
                print(json.dumps(v, indent=2) if isinstance(v, (dict, list)) else v)
            else:
                print(rv.get("description", "(no value)"))
            break


async def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "-f":
        expression = open(sys.argv[2]).read()
    elif len(sys.argv) >= 2 and sys.argv[1] != "-f":
        expression = " ".join(sys.argv[1:])
    else:
        expression = sys.stdin.read()

    targets = None
    for _ in range(30):
        try:
            with urllib.request.urlopen("http://localhost:9222/json", timeout=2) as r:
                targets = json.loads(r.read())
            break
        except Exception:
            await asyncio.sleep(1)

    if not targets:
        sys.exit("Could not reach Vesktop debug port after 30 s — is vesktop-debug.sh running?")

    page = next(
        (t for t in targets if t.get("type") == "page" and "discord.com" in t.get("url", "")),
        next((t for t in targets if t.get("type") == "page"), None),
    )
    if not page:
        sys.exit(f"No page target found. Targets: {[t.get('type') for t in targets]}")

    await evaluate(page["webSocketDebuggerUrl"], expression)


if __name__ == "__main__":
    asyncio.run(main())
