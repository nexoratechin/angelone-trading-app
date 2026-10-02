"""Full read-write web application (FastAPI).

This is the control panel: it can start/stop trading sessions, edit and switch
strategy versions, change settings, store credentials, trip/reset the kill
switch, run backtests and download reports - plus a live WebSocket feed for the
dashboard.

Bound to localhost by default. Set ``APP_PASSWORD`` to require a login, and/or
let operators self-register at ``/register`` (the first account becomes an
administrator; later sign-ups need ``REGISTER_CODE``). For scripts, setting
``WEB_TOKEN`` still allows an ``X-Auth-Token`` header instead of a session.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import hmac
import json
from pathlib import Path

import yaml

from urllib.parse import quote

from ..config import Settings
from ..logging import get_logger
from ..rules.spec import load_spec
from .auth import (
    DEFAULT_TTL_SECONDS,
    SESSION_COOKIE,
    create_session_token,
    credentials_ok,
    verify_session_token,
)
from .controller import Controller
from .users import UserStore

log = get_logger("web.api")

try:
    from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
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
    store = UserStore(settings.auth_db_path) if settings else None
    web_token = settings.secret(settings.web_token) if settings else ""
    env_password = settings.secret(settings.app_password) if settings else ""
    register_code = settings.secret(settings.register_code) if settings else ""
    # Sign cookies with the env password plus a persisted random store key, so
    # rotating the password or pointing at a fresh store invalidates old sessions.
    session_secret = f"{env_password}|{store.secret()}" if store else env_password

    def _login_required() -> bool:
        return bool(env_password or (store and store.count() > 0))

    def _registration_policy() -> dict:
        if store is None:
            return {"open": False, "first_run": False, "requires_code": False, "has_accounts": False}
        has_accounts = store.count() > 0
        first_run = not has_accounts and not env_password
        is_open = first_run or bool(register_code)
        return {
            "open": is_open,
            "first_run": first_run,
            "requires_code": is_open and not first_run,
            "has_accounts": has_accounts,
        }

    def _signed_in(request: Request) -> bool:
        return bool(verify_session_token(session_secret, request.cookies.get(SESSION_COOKIE)))

    def _set_session(response, username: str, request: Request):
        response.set_cookie(
            SESSION_COOKIE,
            create_session_token(session_secret, username),
            max_age=DEFAULT_TTL_SECONDS,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/",
        )
        return response

    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next):
        path = request.url.path
        login_on = _login_required()

        # Already signed in? never show the login/register forms again.
        if path in ("/login", "/register") and login_on:
            if _signed_in(request):
                return RedirectResponse(url="/", status_code=302)
            return await call_next(request)

        exempt = (
            path == "/favicon.ico"
            or path.startswith("/static/")
            or path in ("/api/login", "/api/logout", "/api/register", "/api/registration-status")
        )
        if exempt or not (login_on or web_token):
            return await call_next(request)

        token_ok = bool(web_token and request.headers.get("x-auth-token") == web_token)
        if _signed_in(request) or token_ok:
            return await call_next(request)

        if path.startswith(("/api", "/ws")):
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        return RedirectResponse(url="/login?next=" + quote(path or "/"), status_code=302)

    # ----------------------------------------------------------------- login
    @app.get("/login", response_class=HTMLResponse)
    async def login_page():
        login_file = STATIC_DIR / "login.html"
        if login_file.exists():
            return HTMLResponse(login_file.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Login UI not found</h1>")

    @app.post("/api/login")
    async def login(request: Request, payload: dict = Body(...)):
        if not _login_required():
            return JSONResponse({"ok": True, "login_enabled": False})
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        account = store.verify(username, password) if store else None
        if account is None and not (env_password and credentials_ok(settings, username, password)):
            raise HTTPException(status_code=401, detail="Invalid username or password")
        display = account["username"] if account else username
        role = account["role"] if account else "admin"
        response = JSONResponse({"ok": True, "username": display, "role": role})
        _set_session(response, display, request)
        log.info("Web login: user=%s", display)
        return response

    @app.post("/api/logout")
    async def logout():
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # ------------------------------------------------------------ register
    @app.get("/register", response_class=HTMLResponse)
    async def register_page():
        register_file = STATIC_DIR / "register.html"
        if register_file.exists():
            return HTMLResponse(register_file.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Register UI not found</h1>")

    @app.get("/api/registration-status")
    async def registration_status():
        return JSONResponse(_registration_policy())

    @app.post("/api/register")
    async def register(request: Request, payload: dict = Body(...)):
        policy = _registration_policy()
        if not policy["open"]:
            raise HTTPException(status_code=403, detail="registration is closed")
        if policy["requires_code"] and not hmac.compare_digest(str(payload.get("code") or ""), register_code):
            raise HTTPException(status_code=403, detail="invalid invite code")
        username = str(payload.get("username") or "")
        password = str(payload.get("password") or "")
        confirm = payload.get("confirm")
        if confirm is not None and confirm != password:
            raise HTTPException(status_code=400, detail="passwords do not match")
        try:
            account = store.create(username, password, role="admin" if policy["first_run"] else "user")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        response = JSONResponse({"ok": True, "username": account["username"], "role": account["role"]})
        _set_session(response, account["username"], request)
        log.info("Web register: user=%s role=%s", account["username"], account["role"])
        return response

    @app.get("/api/me")
    async def me(request: Request):
        username = verify_session_token(session_secret, request.cookies.get(SESSION_COOKIE))
        role = None
        if username:
            row = store.get(username) if store else None
            if row:
                role = row["role"]
            elif settings and username == settings.app_username:
                role = "admin"
        return JSONResponse({"login_enabled": _login_required(), "username": username, "role": role})

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
        login_on = _login_required()
        token = sock.query_params.get("token") or sock.headers.get("x-auth-token")
        token_ok = bool(web_token and token == web_token)
        cookie_ok = bool(
            login_on and verify_session_token(session_secret, sock.cookies.get(SESSION_COOKIE))
        )
        if (login_on or web_token) and not (token_ok or cookie_ok):
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
