#!/usr/bin/env python3
"""Simulate a drag on the spatial audio participant node via CDP Input domain.
Gets the node's bounding rect, then dispatches mousedown → mousemove series → mouseup.
"""
import asyncio
import json
import sys
import urllib.request
import websockets


async def run(ws_url: str) -> None:
    async with websockets.connect(ws_url, max_size=None) as ws:
        msg_id = 0

        async def send(method, params=None):
            nonlocal msg_id
            msg_id += 1
            _id = msg_id
            await ws.send(json.dumps({"id": _id, "method": method, "params": params or {}}))
            async for raw in ws:
                m = json.loads(raw)
                if m.get("id") == _id:
                    if "error" in m:
                        raise RuntimeError(f"{method} error: {m['error']}")
                    return m.get("result", {})

        # Get the node's bounding rect via JS
        result = await send("Runtime.evaluate", {
            "expression": """
                (() => {
                    const node = document.querySelector('[class*="vc-spatial-node"]');
                    const canvas = document.querySelector('[class*="vc-spatial-canvas"]');
                    if (!node || !canvas) return JSON.stringify({error: 'not found'});
                    const nr = node.getBoundingClientRect();
                    const cr = canvas.getBoundingClientRect();
                    return JSON.stringify({
                        nx: nr.left + nr.width/2,
                        ny: nr.top + nr.height/2,
                        cx: cr.left,
                        cy: cr.top,
                        cw: cr.width,
                        ch: cr.height,
                    });
                })()
            """,
            "returnByValue": True,
        })
        data = json.loads(result["result"]["value"])
        print(f"Node center: ({data['nx']:.0f}, {data['ny']:.0f})")
        print(f"Canvas: ({data['cx']:.0f}, {data['cy']:.0f}) {data['cw']:.0f}x{data['ch']:.0f}")

        if "error" in data:
            print(f"Error: {data['error']}")
            return

        nx, ny = data["nx"], data["ny"]

        # Dispatch pointer/mouse events to simulate drag
        # Move target: 60px right, 60px down from current node position
        dx, dy = 60, 60

        print(f"Simulating drag: ({nx:.0f},{ny:.0f}) → ({nx+dx:.0f},{ny+dy:.0f})")

        async def mouse(type_, x, y, button="none", buttons=0, click_count=0):
            await send("Input.dispatchMouseEvent", {
                "type": type_,
                "x": x, "y": y,
                "button": button,
                "buttons": buttons,
                "clickCount": click_count,
                "pointerType": "mouse",
            })

        # mousedown
        await mouse("mousePressed", nx, ny, button="left", buttons=1, click_count=1)
        await asyncio.sleep(0.05)

        # mousemove in small steps
        steps = 20
        for i in range(1, steps + 1):
            px = nx + dx * i / steps
            py = ny + dy * i / steps
            await mouse("mouseMoved", px, py, buttons=1)
            await asyncio.sleep(0.02)

        # mouseup
        await mouse("mouseReleased", nx + dx, ny + dy, button="left", buttons=0, click_count=1)
        await asyncio.sleep(0.1)

        # Read console output via JS
        result = await send("Runtime.evaluate", {
            "expression": """
                (() => {
                    const node = document.querySelector('[class*="vc-spatial-node"]');
                    const canvas = document.querySelector('[class*="vc-spatial-canvas"]');
                    if (!node) return 'node not found';
                    const nr = node.getBoundingClientRect();
                    const cr = canvas.getBoundingClientRect();
                    return JSON.stringify({
                        nodeTag: node._debugTag,
                        left: node.style.left,
                        top: node.style.top,
                        posRelCanvas: {
                            x: nr.left - cr.left,
                            y: nr.top - cr.top,
                        }
                    });
                })()
            """,
            "returnByValue": True,
        })
        print(f"\nPost-drag node state: {result['result']['value']}")


async def main():
    with urllib.request.urlopen("http://localhost:9222/json", timeout=5) as r:
        targets = json.loads(r.read())

    page = next(
        (t for t in targets if t.get("type") == "page" and "discord.com" in t.get("url", "")),
        next((t for t in targets if t.get("type") == "page"), None),
    )
    if not page:
        sys.exit("No page target found")

    await run(page["webSocketDebuggerUrl"])


if __name__ == "__main__":
    asyncio.run(main())
