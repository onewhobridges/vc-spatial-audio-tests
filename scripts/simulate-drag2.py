#!/usr/bin/env python3
"""Simulate a small inward drag on the spatial node, then read diagnostic log."""
import asyncio
import json
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

        # Get node/canvas geometry
        r = await send("Runtime.evaluate", {
            "expression": """
                JSON.stringify((() => {
                    const node = document.querySelector('[class*="vc-spatial-node"]');
                    const canvas = document.querySelector('[class*="vc-spatial-canvas"]');
                    if (!node || !canvas) return {error: 'not found'};
                    const nr = node.getBoundingClientRect();
                    const cr = canvas.getBoundingClientRect();
                    return {
                        nx: nr.left + nr.width/2,
                        ny: nr.top + nr.height/2,
                        nodeLeft: node.style.left,
                        nodeTop: node.style.top,
                        canvasLeft: cr.left,
                        canvasTop: cr.top,
                        canvasRight: cr.right,
                        canvasBottom: cr.bottom,
                        tag: node._debugTag,
                    };
                })())
            """,
            "returnByValue": True,
        })
        data = json.loads(r["result"]["value"])
        if "error" in data:
            print(f"Error: {data['error']}")
            return

        nx, ny = data["nx"], data["ny"]
        cx, cy = data["canvasLeft"], data["canvasTop"]
        cr, cb = data["canvasRight"], data["canvasBottom"]
        print(f"Node #{data['tag']} center=({nx:.0f},{ny:.0f}) style=({data['nodeLeft']},{data['nodeTop']})")
        print(f"Canvas: ({cx:.0f},{cy:.0f}) to ({cr:.0f},{cb:.0f})")

        # Drag toward canvas center, 40px step, 8 steps total
        canvas_cx = (cx + cr) / 2
        canvas_cy = (cy + cb) / 2
        import math
        angle = math.atan2(canvas_cy - ny, canvas_cx - nx)
        total_dist = 40
        dx = math.cos(angle) * total_dist
        dy = math.sin(angle) * total_dist
        steps = 8

        print(f"Dragging: ({nx:.0f},{ny:.0f}) → ({nx+dx:.0f},{ny+dy:.0f})")

        async def mouse(type_, x, y, button="none", buttons=0, click_count=0):
            await send("Input.dispatchMouseEvent", {
                "type": type_, "x": x, "y": y,
                "button": button, "buttons": buttons,
                "clickCount": click_count,
                "pointerType": "mouse",
            })

        # Clear previous log
        await send("Runtime.evaluate", {"expression": "window.__dragLog && (window.__dragLog.length = 0); 'cleared'", "returnByValue": True})

        # mousedown
        await mouse("mousePressed", nx, ny, button="left", buttons=1, click_count=1)
        await asyncio.sleep(0.1)

        # move in steps
        for i in range(1, steps + 1):
            px = nx + dx * i / steps
            py = ny + dy * i / steps
            await mouse("mouseMoved", px, py, buttons=1)
            await asyncio.sleep(0.05)  # 50ms between moves — give React time to re-render

        await asyncio.sleep(0.1)

        # mouseup
        await mouse("mouseReleased", nx + dx, ny + dy, button="left", buttons=0, click_count=1)
        await asyncio.sleep(0.2)

        # Read diagnostic log
        r2 = await send("Runtime.evaluate", {
            "expression": """
                JSON.stringify({
                    log: window.__dragLog || [],
                    postDrag: (() => {
                        const node = document.querySelector('[class*="vc-spatial-node"]');
                        const canvas = document.querySelector('[class*="vc-spatial-canvas"]');
                        if (!node) return {error: 'node not found'};
                        return {
                            tag: node._debugTag,
                            left: node.style.left,
                            top: node.style.top,
                        };
                    })(),
                })
            """,
            "returnByValue": True,
        })
        result = json.loads(r2["result"]["value"])
        print(f"\nPost-drag: {result['postDrag']}")
        print(f"\n=== Diagnostic Log ({len(result['log'])} entries) ===")
        for entry in result["log"]:
            print(entry)


async def main():
    with urllib.request.urlopen("http://localhost:9222/json", timeout=5) as r:
        targets = json.loads(r.read())
    page = next(
        (t for t in targets if t.get("type") == "page" and "discord.com" in t.get("url", "")),
        next((t for t in targets if t.get("type") == "page"), None),
    )
    await run(page["webSocketDebuggerUrl"])

if __name__ == "__main__":
    asyncio.run(main())
