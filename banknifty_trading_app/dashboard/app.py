"""Minimal read-only dashboard (FastAPI).

Deliberately decoupled from trading: it *reads* runtime state and never blocks
the engine. Runs on a background thread so the asyncio loop is untouched.
"""

from __future__ import annotations

import datetime as _dt
import threading

from ..config import Settings
from ..logging import get_logger

log = get_logger("dashboard")

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
except Exception:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Bank Nifty Trading</title>
<meta http-equiv="refresh" content="3">
<style>
 body{{font-family:system-ui,Segoe UI,Arial;margin:24px;background:#0f172a;color:#e2e8f0}}
 h1{{font-size:18px}} table{{border-collapse:collapse;margin-top:8px}}
 td,th{{border:1px solid #334155;padding:6px 12px;text-align:left;font-size:14px}}
 .ok{{color:#4ade80}} .bad{{color:#f87171}} .muted{{color:#94a3b8}}
</style></head><body>
<h1>Bank Nifty Futures - {mode} mode</h1>
<table>
 <tr><th>Strategy</th><td>{strategy}</td></tr>
 <tr><th>Kill switch</th><td class="{kill_cls}">{kill}</td></tr>
 <tr><th>Feed</th><td>{feed}</td></tr>
 <tr><th>Side / Lots</th><td>{side} {lots}</td></tr>
 <tr><th>Avg / Mark</th><td>{avg} / {mark}</td></tr>
 <tr><th>Stop (spot)</th><td>{stop}</td></tr>
 <tr><th>Realized</th><td>{realized:.2f}</td></tr>
 <tr><th>Unrealized</th><td>{unrealized:.2f}</td></tr>
 <tr><th>Trades today</th><td>{trades}</td></tr>
</table>
<p class="muted">Updated {now}. Auto-refresh every 3s. Read-only.</p>
</body></html>"""


def create_app(runtime):
    if FastAPI is None:  # pragma: no cover
        raise RuntimeError("fastapi/uvicorn not installed")

    app = FastAPI(title="Bank Nifty Trading Dashboard", docs_url=None, redoc_url=None)

    def _snapshot() -> dict:
        snap = runtime.book.snapshot()
        return {
            "mode": runtime.settings.mode,
            "strategy": runtime.spec.version,
            "kill_switch": runtime.kill_switch.is_active(),
            "kill_reason": runtime.kill_switch.reason,
            "feed_connected": bool(
                getattr(runtime, "feed", None)
                and runtime.store.latest(getattr(runtime.spot, "token", ""))
            ),
            "side": snap.side.value if snap.side else None,
            "lots": snap.lots,
            "avg_price": snap.avg_price,
            "mark_price": snap.mark_price,
            "stop": runtime.engine.state.stop_level,
            "realized": snap.realized_money,
            "unrealized": snap.unrealized_money,
            "total": snap.total_money,
            "trades_today": runtime.risk.trades_today,
            "daily_pnl": runtime.risk.daily_pnl,
            "cycle": runtime.engine.state.cycle_id,
            "time": _dt.datetime.now().isoformat(timespec="seconds"),
        }

    @app.get("/status")
    def status():
        return JSONResponse(_snapshot())

    @app.get("/", response_class=HTMLResponse)
    def index():
        s = _snapshot()
        return _PAGE.format(
            mode=s["mode"],
            strategy=s["strategy"],
            kill="ACTIVE" if s["kill_switch"] else "not active",
            kill_cls="bad" if s["kill_switch"] else "ok",
            feed="connected" if s["feed_connected"] else "waiting",
            side=s["side"] or "-",
            lots=s["lots"],
            avg=f"{s['avg_price']:.2f}" if s["avg_price"] else "-",
            mark=f"{s['mark_price']:.2f}" if s["mark_price"] else "-",
            stop=f"{s['stop']:.2f}" if s["stop"] else "-",
            realized=s["realized"],
            unrealized=s["unrealized"],
            trades=s["trades_today"],
            now=s["time"],
        )

    return app


def start_dashboard(settings: Settings, bus, runtime):
    if FastAPI is None:  # pragma: no cover
        raise RuntimeError("fastapi/uvicorn not installed")
    import uvicorn

    app = create_app(runtime)
    config = uvicorn.Config(app, host=settings.dashboard_host, port=settings.dashboard_port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="dashboard", daemon=True)
    thread.start()
    log.info("Dashboard at http://%s:%d", settings.dashboard_host, settings.dashboard_port)
    return server
