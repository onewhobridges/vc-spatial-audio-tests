#!/usr/bin/env python3
"""Connect to Vesktop via Chrome DevTools Protocol and stream console logs to a file."""
import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime

import websockets


async def capture(ws_url: str, log_path: str) -> None:
    print(f"Saving to {log_path}", flush=True)
    with open(log_path, "w") as f:
        async with websockets.connect(ws_url, max_size=None) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
            await ws.send(json.dumps({"id": 2, "method": "Log.enable"}))
            print("Connected — capturing console output. Ctrl+C to stop.", flush=True)
            async for raw in ws:
                msg = json.loads(raw)
                method = msg.get("method", "")

                if method == "Runtime.consoleAPICalled":
                    params = msg["params"]
                    parts = []
                    for arg in params.get("args", []):
                        parts.append(str(arg.get("value", arg.get("description", ""))))
                    line = " ".join(parts)
                    stack = params.get("stackTrace", {}).get("callFrames", [])
                    for frame in stack[:8]:
                        url = frame.get("url", "").split("/")[-1]
                        line += f"\n    at {frame.get('functionName','(anon)')} ({url}:{frame.get('lineNumber','?')}:{frame.get('columnNumber','?')})"
                    f.write(line + "\n")
                    f.flush()

                elif method == "Log.entryAdded":
                    entry = msg["params"]["entry"]
                    f.write(f"[{entry.get('level','')}] {entry.get('text','')}\n")
                    f.flush()


async def main() -> None:
    cdp_port = os.environ.get("CDP_PORT", "9222")
    print(f"Waiting for Vesktop debug port on :{cdp_port} ...", flush=True)
    targets = None
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"http://localhost:{cdp_port}/json", timeout=2) as r:
                targets = json.loads(r.read())
            break
        except Exception:
            await asyncio.sleep(1)

    if not targets:
        sys.exit(f"Could not reach Vesktop debug port {cdp_port} after 30 s")

    page = next(
        (t for t in targets if t.get("type") == "page" and "discord.com" in t.get("url", "")),
        next((t for t in targets if t.get("type") == "page"), None),
    )
    if not page:
        sys.exit(f"No page target found. Targets: {[t.get('type') for t in targets]}")

    channel_id = os.environ.get("VESKTOP_TEST_CHANNEL_ID", "unknown")
    os.makedirs("/home/bridger/sabbatical/logs", exist_ok=True)
    ts = int(datetime.now().timestamp() * 1000)
    log_path = f"/home/bridger/sabbatical/logs/discord.com-{channel_id}-{ts}.log"

    await capture(page["webSocketDebuggerUrl"], log_path)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
