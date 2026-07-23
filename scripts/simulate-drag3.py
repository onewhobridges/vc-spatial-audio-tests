#!/usr/bin/env python3
"""Simulate a fast drag with no delays between moves (realistic mouse speed)."""
import asyncio
import json
import math
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
                    return m.get("result", {})

        # Get node/canvas geometry
        r = await send("Runtime.evaluate", {
            "expression": """JSON.stringify((() => {
                const node = document.querySelector('[class*="vc-spatial-node"]');
                const canvas = document.querySelector('[class*="vc-spatial-canvas"]');
                if (!node || !canvas) return {error: 'not found'};
                const nr = node.getBoundingClientRect();
                const cr = canvas.getBoundingClientRect();
                return { nx: nr.left+nr.width/2, ny: nr.top+nr.height/2,
                         initLeft: node.style.left, initTop: node.style.top,
                         canvasLeft: cr.left, canvasTop: cr.top, tag: node._debugTag };
            })())""",
            "returnByValue": True,
        })
        data = json.loads(r["result"]["value"])
        if "error" in data:
            print(data["error"]); return

        nx, ny = data["nx"], data["ny"]
        print(f"Node #{data['tag']} center=({nx:.0f},{ny:.0f}) style=({data['initLeft']},{data['initTop']})")

        # Clear log
        await send("Runtime.evaluate", {"expression": "window.__dragLog && (window.__dragLog.length = 0); 'ok'", "returnByValue": True})

        # 60 moves of 1px each (=60px travel) — like a real fast drag at ~60Hz
        # No async delay between moves to stress test batching
        steps = 60
        total_dist = 60
        angle = math.atan2(data['canvasTop'] + 150 - ny, data['canvasLeft'] + 150 - nx)  # toward center
        dx = math.cos(angle) * total_dist / steps
        dy = math.sin(angle) * total_dist / steps

        async def mouse(type_, x, y, button="none", buttons=0, click_count=0):
            await send("Input.dispatchMouseEvent", {
                "type": type_, "x": x, "y": y,
                "button": button, "buttons": buttons,
                "clickCount": click_count, "pointerType": "mouse",
            })

        await mouse("mousePressed", nx, ny, button="left", buttons=1, click_count=1)
        await asyncio.sleep(0.01)

        # Fire all 60 moves with NO sleep between them
        for i in range(1, steps + 1):
            await mouse("mouseMoved", nx + dx * i, ny + dy * i, buttons=1)

        await asyncio.sleep(0.2)  # let React render
        await mouse("mouseReleased", nx + dx * steps, ny + dy * steps, button="left", click_count=1)
        await asyncio.sleep(0.2)

        # Read results
        r2 = await send("Runtime.evaluate", {
            "expression": """JSON.stringify((() => {
                const node = document.querySelector('[class*="vc-spatial-node"]');
                if (!node) return {error: 'not found'};
                return { tag: node._debugTag, left: node.style.left, top: node.style.top };
            })())""",
            "returnByValue": True,
        })
        post = json.loads(r2["result"]["value"])
        print(f"Post-drag: {post}")

        r3 = await send("Runtime.evaluate", {
            "expression": "JSON.stringify({log: (window.__dragLog || []), styleChanges: (window.__dragLog || []).filter(l => l.startsWith('STYLE')).length})",
            "returnByValue": True,
        })
        result = json.loads(r3["result"]["value"])
        print(f"\nStyle changes: {result['styleChanges']} out of 60 moves")
        print("\n=== Log ===")
        for entry in result["log"]:
            print(entry)


async def main():
    with urllib.request.urlopen("http://localhost:9222/json", timeout=5) as r:
        targets = json.loads(r.read())
    page = next((t for t in targets if t.get("type") == "page" and "discord.com" in t.get("url", "")), None)
    await run(page["webSocketDebuggerUrl"])

if __name__ == "__main__":
    asyncio.run(main())
