"""Capture web-console screenshots via the Chrome DevTools Protocol.

Runs against a locally-served Bank Nifty web console (offline demo data only).
No external dependencies beyond `websocket-client`, which the project already
pins.
"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

import websocket

URL = "http://127.0.0.1:8090/"
DEBUG = "http://127.0.0.1:9222"
OUT = Path("pdf_assets/screenshots")
WIDTH = 1600
VIEW_H = 1000


def http_json(url: str):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_for(predicate, timeout=40, interval=0.5):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = predicate()
            if last:
                return last
        except Exception as exc:  # pragma: no cover - diagnostics
            last = exc
        time.sleep(interval)
    raise RuntimeError(f"timed out waiting: {last!r}")


class CDP:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, suppress_origin=True, timeout=60)
        self._id = 0

    def send(self, method: str, params: dict | None = None):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expression: str, await_promise: bool = False):
        return self.send(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": await_promise, "returnByValue": True},
        ).get("result", {}).get("value")

    def shot(self, name: str):
        metrics = self.send("Page.getLayoutMetrics")
        height = int(metrics.get("cssContentSize", {}).get("height", VIEW_H))
        height = max(VIEW_H, min(height + 40, 7000))
        self.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": WIDTH, "height": height, "deviceScaleFactor": 1, "mobile": False},
        )
        time.sleep(0.5)
        data = self.send(
            "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True}
        )["data"]
        path = OUT / f"{name}.png"
        path.write_bytes(base64.b64decode(data))
        print(f"  saved {path}  ({path.stat().st_size//1024} KB, h={height})")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    print("waiting for web console + chrome...")
    wait_for(lambda: http_json(f"{DEBUG}/json/version"))
    pages = wait_for(
        lambda: [t for t in http_json(f"{DEBUG}/json/list") if t.get("type") == "page"]
    )
    ws_url = pages[0]["webSocketDebuggerUrl"]

    cdp = CDP(ws_url)
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("Network.enable")
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    cdp.send("Network.clearBrowserCache")
    cdp.send("Emulation.setDeviceMetricsOverride",
             {"width": WIDTH, "height": VIEW_H, "deviceScaleFactor": 1, "mobile": False})

    print("navigating...")
    cdp.send("Page.navigate", {"url": URL})
    wait_for(lambda: cdp.eval("document.readyState") == "complete", timeout=30)
    time.sleep(3)

    # Make the dashboard show a live session: start an offline demo and let the
    # WebSocket push state. A gentle pace keeps it running while we capture.
    print("starting offline demo session for live dashboard numbers...")
    cdp.eval(
        "fetch('/api/session/start',{method:'POST',headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({kind:'demo',start:'2025-09-01',end:'2026-03-01',pace_s:0.35})}).then(r=>r.json())",
        await_promise=True,
    )
    time.sleep(6)

    print("capturing tabs...")
    cdp.shot("01_dashboard")

    for tab, name in [
        ("trades", "02_trades"),
        ("events", "03_events"),
        ("strategy", "04_strategy"),
        ("backtest", "05_backtest"),
        ("settings", "06_settings"),
    ]:
        cdp.eval(f"document.querySelector('#tabs button[data-tab=\"{tab}\"]').click()")
        time.sleep(2.5)
        if tab == "strategy":
            cdp.eval("openStrategy('v1_baseline')", await_promise=True)
            time.sleep(1.5)
        cdp.shot(name)

    # Populate the backtest panel with a real synthetic run result.
    print("running an in-console synthetic backtest...")
    cdp.eval(f"document.querySelector('#tabs button[data-tab=\"backtest\"]').click()")
    time.sleep(1)
    try:
        cdp.eval(
            "runBacktest()",
            await_promise=True,
        )
    except Exception as exc:  # pragma: no cover
        print("  backtest result not captured:", exc)
    time.sleep(2)
    cdp.shot("05b_backtest_result")

    # Trip and reset the kill switch to show the emergency state.
    print("capturing kill-switch state...")
    cdp.eval(
        "fetch('/api/kill',{method:'POST',headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({reason:'guide screenshot'})}).then(r=>r.json())",
        await_promise=True,
    )
    time.sleep(2)
    cdp.eval("document.querySelector('#tabs button[data-tab=\"dashboard\"]').click()")
    time.sleep(2.5)
    cdp.shot("07_dashboard_killswitch")

    cdp.eval(
        "fetch('/api/unkill',{method:'POST'}).then(r=>r.json())", await_promise=True
    )
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
