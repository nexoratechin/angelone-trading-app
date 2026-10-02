"""Session controller for the web app.

Owns the lifecycle of a running trading session (demo / paper / live) as an
asyncio task, and exposes everything the web UI needs: state snapshots,
strategy CRUD, settings, credentials, backtests and reports.

Both ``DemoRunner`` (offline) and ``TradingRuntime`` (broker) expose the same
attributes (book, engine, risk, kill_switch, store, spot, instrument, spec,
repo), so the controller treats them uniformly.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
from pathlib import Path

import yaml

from ..config import Settings
from ..credentials import read_env, update_env
from ..database.repository import Repository
from ..database.session import create_database
from ..logging import get_logger
from ..rules.spec import load_spec

log = get_logger("web.controller")

SETTING_KEYS = {
    "MODE", "LIVE_TRADING", "ACTIVE_STRATEGY", "PRODUCT_TYPE", "ORDER_TYPE",
    "MARKET_PROTECT_BUFFER_POINTS", "PAPER_SLIPPAGE_POINTS", "MAX_LOTS",
    "MAX_DAILY_LOSS", "MAX_TRADES_PER_DAY", "MARKET_OPEN", "MARKET_CLOSE",
    "EXPIRY_SQUAREOFF_TIME", "STALE_TICK_TIMEOUT_S", "BACKTEST_INTRABAR",
    "WEB_HOST", "WEB_PORT", "DASHBOARD_ENABLED",
}
CRED_KEYS = {
    "ANGEL_API_KEY": "angel_api_key",
    "ANGEL_CLIENT_CODE": "angel_client_code",
    "ANGEL_PIN": "angel_pin",
    "ANGEL_TOTP_SECRET": "angel_totp_secret",
}
# Shown on the settings page but deliberately NOT editable through the generic
# settings form. Arming live trading is a deliberate CLI action, not a stray
# click on a settings page.
READONLY_KEYS = {"LIVE_ARMED"}


def _load_settings() -> Settings:
    """Fresh Settings so .env edits take effect without restarting the server."""
    return Settings()


class Controller:
    def __init__(self) -> None:
        self.settings: Settings = _load_settings()
        self.session = None
        self.task: asyncio.Task | None = None
        self.kind: str | None = None
        self.spec = None
        self.error: str | None = None
        self.started_at: _dt.datetime | None = None

    # =============================================================== lifecycle
    async def start(
        self,
        kind: str,
        strategy: str | None = None,
        start: str | None = None,
        end: str | None = None,
        pace_s: float = 0.1,
        confirm_live: bool = False,
    ) -> dict:
        await self.stop()
        self.settings = _load_settings()
        self.spec = load_spec(self.settings.strategy_path(strategy))
        self.error = None

        date_start = _dt.date.fromisoformat(start) if start else _dt.date.today() - _dt.timedelta(days=120)
        date_end = _dt.date.fromisoformat(end) if end else _dt.date.today()

        if kind == "demo":
            from ..demo import DemoRunner

            runner = DemoRunner(self.settings, self.spec)
            coro = runner.run(date_start, date_end, pace_s=max(0.0, pace_s))
            self.session = runner
        elif kind in ("paper", "live"):
            if kind == "live":
                # Two independent gates: the UI confirmation, and the code-level
                # arm switch. The arm switch cannot be bypassed from the console.
                if not confirm_live:
                    raise ValueError("Live trading requires confirm_live=true")
                if not self.settings.live_armed:
                    raise ValueError(
                        self.settings.live_block_reason()
                        or "live trading is locked"
                    )
            from ..runtime import TradingRuntime

            self.settings.mode = kind
            self.settings.live_trading = kind == "live"
            runtime = TradingRuntime(self.settings, self.spec)
            coro = runtime.start()
            self.session = runtime
        else:
            raise ValueError(f"unknown session kind: {kind!r}")

        self.kind = kind
        self.started_at = _dt.datetime.now()
        self.task = asyncio.create_task(self._guard(coro))
        log.info("Web session started: kind=%s strategy=%s", kind, self.spec.version)
        return {"ok": True, "kind": kind, "strategy": self.spec.version}

    async def _guard(self, coro) -> None:
        try:
            await coro
            log.info("Web session finished normally")
        except asyncio.CancelledError:
            log.info("Web session cancelled")
            raise
        except Exception as exc:
            self.error = str(exc)
            log.error("Web session error: %s", exc)

    async def stop(self) -> dict:
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        if self.session is not None:
            try:
                if self.kind in ("paper", "live") and hasattr(self.session, "stop"):
                    await self.session.stop()
            except Exception as exc:  # pragma: no cover
                log.warning("session.stop failed: %s", exc)
            try:
                self.session.writer.stop()
            except Exception:
                pass
        self.session = None
        self.task = None
        self.kind = None
        return {"ok": True, "running": False}

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    # ================================================================ snapshot
    def snapshot(self) -> dict:
        s = self.session
        out: dict = {
            "session": {
                "running": self.running,
                "kind": self.kind,
                "strategy": getattr(self.spec, "version", None),
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "error": self.error,
            },
            "server_time": _dt.datetime.now().isoformat(timespec="seconds"),
        }
        if s is None:
            return out
        try:
            snap = s.book.snapshot()
            st = s.engine.state
            out.update({
                "mode": getattr(s.settings, "mode", None),
                "live": getattr(s.settings, "is_live", False),
                "live_armed": getattr(s.settings, "live_armed", False),
                "live_block_reason": getattr(s.settings, "live_block_reason", lambda: None)(),
                "kill_switch": s.kill_switch.is_active(),
                "kill_reason": s.kill_switch.reason,
                "side": snap.side.value if snap.side else None,
                "lots": snap.lots,
                "avg_price": round(snap.avg_price, 2),
                "mark_price": round(snap.mark_price, 2),
                "realized": round(snap.realized_money, 2),
                "unrealized": round(snap.unrealized_money, 2),
                "total": round(snap.total_money, 2),
                "stop": round(st.stop_level, 2) if st.stop_level else None,
                "cycle": st.cycle_id,
                "trading_day": str(st.trading_day) if st.trading_day else None,
                "last_day": st.is_last_trading_day,
                "trades_today": s.risk.trades_today,
                "daily_pnl": round(s.risk.daily_pnl, 2),
                "spot_ltp": s.store.ltp(getattr(s.spot, "token", "")),
                "futures_ltp": s.store.ltp(getattr(s.instrument, "token", "")),
                "spot_symbol": getattr(s.spot, "symbol", None),
                "futures_symbol": getattr(s.instrument, "symbol", None),
                "levels": self.levels(),
            })
        except Exception as exc:  # pragma: no cover
            out["error"] = str(exc)
        return out

    # ============================================================= kill switch
    def trip_kill(self, reason: str) -> None:
        from ..risk.kill_switch import KillSwitch

        KillSwitch(self.settings.kill_switch_file).trip(reason)

    def reset_kill(self) -> None:
        from ..risk.kill_switch import KillSwitch

        KillSwitch(self.settings.kill_switch_file).reset()

    # ============================================================== strategies
    def list_strategies(self) -> dict:
        s = _load_settings()
        d = Path(s.strategy_dir)
        versions = sorted(p.stem for p in d.glob("*.yaml"))
        return {"active": s.active_strategy, "versions": versions}

    def _strategy_path(self, name: str) -> Path:
        safe = Path(name).name
        return Path(self.settings.strategy_dir) / (safe if safe.endswith(".yaml") else f"{safe}.yaml")

    def read_strategy(self, name: str) -> str:
        p = self._strategy_path(name)
        if not p.exists():
            raise FileNotFoundError(f"strategy {name!r} not found")
        return p.read_text(encoding="utf-8")

    def save_strategy(self, name: str, text: str) -> dict:
        p = self._strategy_path(name)
        tmp = p.with_suffix(".yaml.tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            load_spec(tmp)  # validate
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            raise ValueError(f"invalid strategy YAML: {exc}")
        tmp.replace(p)
        return {"ok": True, "name": p.stem}

    def activate_strategy(self, name: str) -> None:
        self._strategy_path(name)  # raises if missing
        update_env({"ACTIVE_STRATEGY": name})

    # ================================================================== config
    def read_config(self) -> dict:
        env = read_env()
        cfg = {k: env.get(k, "") for k in sorted(SETTING_KEYS | READONLY_KEYS)}
        cfg["live_armed"] = self.settings.live_armed
        cfg["credentials"] = {
            "angel_api_key": "set" if env.get("ANGEL_API_KEY") else "missing",
            "angel_client_code": "set" if env.get("ANGEL_CLIENT_CODE") else "missing",
            "angel_pin": "set" if env.get("ANGEL_PIN") else "missing",
            "angel_totp_secret": "set" if env.get("ANGEL_TOTP_SECRET") else "missing",
        }
        return cfg

    def write_config(self, payload: dict) -> dict:
        updates = {k: str(v) for k, v in payload.items() if k in SETTING_KEYS}
        if updates:
            update_env(updates)
            self.settings = _load_settings()
        return {"ok": True, "updated": sorted(updates)}

    def write_credentials(self, payload: dict) -> dict:
        updates: dict[str, str] = {}
        for env_key in CRED_KEYS:
            value = payload.get(env_key) or payload.get(CRED_KEYS[env_key])
            if value:
                updates[env_key] = str(value).strip()
        if not updates:
            raise ValueError("no credential fields provided")
        update_env(updates)
        self.settings = _load_settings()
        return {"ok": True, "saved": sorted(updates)}

    # ==================================================================== data
    def _repo(self) -> Repository:
        s = _load_settings()
        return Repository(create_database(s.db_path))

    def trades(self, limit: int = 500) -> list[dict]:
        try:
            return [self._trade_dict(t) for t in self._repo().recent_trades(limit)]
        except Exception as exc:  # pragma: no cover
            log.warning("trades read failed: %s", exc)
            return []

    @staticmethod
    def _trade_dict(t) -> dict:
        return {
            "trade_id": t.trade_id,
            "cycle_id": t.cycle_id,
            "side": t.side,
            "lots": t.lots,
            "entry_time": str(t.entry_time) if t.entry_time else "",
            "entry_price": round(t.entry_price or 0, 2),
            "exit_time": str(t.exit_time) if t.exit_time else "",
            "exit_price": round(t.exit_price, 2) if t.exit_price is not None else None,
            "pnl_points": round(t.pnl_points or 0, 2),
            "pnl_money": round(t.pnl_money or 0, 2),
            "entry_reason": t.entry_reason,
            "exit_reason": t.exit_reason,
            "stop_level": round(t.stop_level, 2) if t.stop_level else None,
            "is_partial": t.is_partial,
            "is_reversal": t.is_reversal,
            "status": t.status,
        }

    def events(self, limit: int = 500) -> list[dict]:
        try:
            return [
                {
                    "ts": str(e.ts) if e.ts else "",
                    "event_type": e.event_type,
                    "detail": e.detail,
                    "strategy_version": e.strategy_version,
                }
                for e in self._repo().recent_events(limit)
            ]
        except Exception as exc:  # pragma: no cover
            log.warning("events read failed: %s", exc)
            return []

    def levels(self) -> dict | None:
        s = self.session
        if s is None:
            return None
        lv = getattr(s.engine, "memory", None)
        lv = lv.levels if lv else None
        if lv is None:
            return None
        return {
            "trading_date": str(lv.trading_date),
            "ref_close": round(lv.ref_close, 2),
            "prev_close": round(lv.prev_close, 2),
            "sma": round(lv.sma, 2) if lv.sma else None,
        }

    # =============================================================== backtest
    async def run_backtest(self, start, end, synthetic: bool = True, strategy: str | None = None) -> dict:
        from ..backtest.run import run_historical, run_synthetic

        s = _load_settings()
        spec = load_spec(s.strategy_path(strategy))
        sd = _dt.date.fromisoformat(start) if start else _dt.date.today() - _dt.timedelta(days=365)
        ed = _dt.date.fromisoformat(end) if end else _dt.date.today()
        fn = run_synthetic if synthetic else run_historical
        result = await asyncio.to_thread(fn, s, spec, sd, ed, None)
        return {
            "ok": True,
            "strategy": spec.version,
            "trades": len(result.trades),
            "net_pnl": round(sum(t.pnl_money for t in result.trades), 2),
            "report": result.meta.get("report_path", ""),
            "meta": json.loads(json.dumps(result.meta, default=str)),
        }

    def list_reports(self) -> list[dict]:
        s = _load_settings()
        d = Path(s.report_dir)
        if not d.exists():
            return []
        files = []
        for p in sorted(d.glob("*.xlsx"), key=lambda x: x.stat().st_mtime, reverse=True):
            files.append({"name": p.name, "size_kb": round(p.stat().st_size / 1024, 1)})
        return files

    def report_path(self, name: str) -> Path | None:
        s = _load_settings()
        d = Path(s.report_dir).resolve()
        p = (d / Path(name).name).resolve()
        if d not in p.parents or not p.exists():
            return None
        return p
