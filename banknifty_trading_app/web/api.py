"""Full read-write web application (FastAPI).

This is the control panel: it can start/stop trading sessions, edit and switch
strategy versions, change settings, store credentials, trip/reset the kill
switch, run backtests and download reports - plus a live WebSocket feed for the
dashboard.

Bound to localhost by default. If you expose it, set ``WEB_TOKEN`` and the API
will require an ``X-Auth-Token`` header (the UI stores it locally).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
from pathlib import Path

import yaml

from ..config import Settings
from ..logging import get_logger
from ..rules.spec import load_spec
from .controller import Controller

log = get_logger("web.api")

try:
    from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
except Exception:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]

STATIC_DIR = Path(__file__).parent / "static"

EXPOSED_SETTINGS = [
    "MODE",
    "LIVE_TRADING",
    "ACTIVE_STRATEGY",
    "PRODUCT_TYPE",
    "ORDER_TYPE",
    "MARKET_PROTECT_BUFFER_POINTS",
    "PAPER_SLIPPAGE_POINTS",
    "MAX_LOTS",
    "MAX_DAILY_LOSS",
    "MAX_TRADES_PER_DAY",
    "MARKET_OPEN",
    "MARKET_CLOSE",
    "EXPIRY_SQUAREOFF_TIME",
    "STALE_TICK_TIMEOUT_S",
    "BACKTEST_INTRABAR",
    "WEB_HOST",
    "WEB_PORT",
    "DASHBOARD_ENABLED",
]


def create_app(settings: Settings | None = None) -> "FastAPI":
    if FastAPI is None:  # pragma: no cover
        raise RuntimeError("fastapi/uvicorn not installed")

    controller = Controller()
    app = FastAPI(title="Bank Nifty Trading Console", docs_url="/api/docs", redoc_url=None)

    # ------------------------------------------------------------- security
    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next):
        required = settings.secret(settings.web_token) if settings else ""
        if (
            required
            and request.url.path.startswith("/api")
            and request.headers.get("x-auth-token") != required
        ):
            return JSONResponse({"detail": "invalid or missing X-Auth-Token"}, status_code=401)
        return await call_next(request)

    # ---------------------------------------------------------------- status
    @app.get("/api/status")
    async def status():
        return JSONResponse(controller.snapshot())

    @app.post("/api/session/start")
    async def session_start(payload: dict = Body(...)):
        try:
            return JSONResponse(await controller.start(
                kind=str(payload.get("kind", "demo")),
                strategy=payload.get("strategy") or None,
                start=payload.get("start") or None,
                end=payload.get("end") or None,
                pace_s=float(payload.get("pace_s", 0.1)),
                confirm_live=bool(payload.get("confirm_live", False)),
            ))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/session/stop")
    async def session_stop():
        return JSONResponse(await controller.stop())

    # ------------------------------------------------------------ kill switch
    @app.post("/api/kill")
    async def kill(payload: dict = Body(default={})):
        controller.trip_kill(str(payload.get("reason") or "manual (web)"))
        return JSONResponse({"ok": True, "kill_switch": True})

    @app.post("/api/unkill")
    async def unkill():
        controller.reset_kill()
        return JSONResponse({"ok": True, "kill_switch": False})

    # ------------------------------------------------------------- strategies
    @app.get("/api/strategies")
    async def list_strategies():
        return JSONResponse(controller.list_strategies())

    @app.get("/api/strategies/{name}")
    async def get_strategy(name: str):
        try:
            return JSONResponse({"name": name, "yaml": controller.read_strategy(name)})
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.put("/api/strategies/{name}")
    async def put_strategy(name: str, payload: dict = Body(...)):
        try:
            return JSONResponse(controller.save_strategy(name, str(payload.get("yaml", ""))))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/strategies/{name}/activate")
    async def activate_strategy(name: str):
        try:
            controller.activate_strategy(name)
            return JSONResponse({"ok": True, "active": name})
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # ----------------------------------------------------------------- config
    @app.get("/api/config")
    async def get_config():
        return JSONResponse(controller.read_config())

    @app.put("/api/config")
    async def put_config(payload: dict = Body(...)):
        return JSONResponse(controller.write_config(payload))

    @app.post("/api/credentials")
    async def put_credentials(payload: dict = Body(...)):
        return JSONResponse(controller.write_credentials(payload))

    # --------------------------------------------------------------- data
    @app.get("/api/trades")
    async def trades(limit: int = 500):
        return JSONResponse(controller.trades(limit))

    @app.get("/api/events")
    async def events(limit: int = 500):
        return JSONResponse(controller.events(limit))

    @app.get("/api/levels")
    async def levels():
        return JSONResponse(controller.levels())

    # -------------------------------------------------------------- backtest
    @app.post("/api/backtest")
    async def backtest(payload: dict = Body(...)):
        try:
            result = await controller.run_backtest(
                start=payload.get("start") or None,
                end=payload.get("end") or None,
                synthetic=bool(payload.get("synthetic", True)),
            )
            return JSONResponse(result)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/reports")
    async def list_reports():
        return JSONResponse(controller.list_reports())

    @app.get("/api/reports/{name}")
    async def download_report(name: str):
        path = controller.report_path(name)
        if path is None:
            raise HTTPException(status_code=404, detail="report not found")
        return FileResponse(path, filename=path.name)

    # ---------------------------------------------------------------- websocket
    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        required = settings.secret(settings.web_token) if settings else ""
        token = sock.query_params.get("token") or sock.headers.get("x-auth-token")
        if required and token != required:
            await sock.close(code=4401)
            return
        try:
            while True:
                await sock.send_text(json.dumps(controller.snapshot(), default=str))
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            return
        except Exception:  # pragma: no cover
            return

    # ---------------------------------------------------------------- static
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return HTMLResponse(index_file.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>UI not found</h1>")

    app.state.controller = controller
    return app


def run_web(settings: Settings) -> None:  # pragma: no cover - process entry
    import uvicorn

    app = create_app(settings)
    uvicorn.run(app, host=settings.web_host, port=settings.web_port, log_level="info")
