"""Connectivity self-test for Angel One.

Runs the read-only half of the pipeline and reports PASS/FAIL per step:

  1. credentials present
  2. login (TOTP)
  3. resolve Bank Nifty Spot instrument
  4. resolve near-month BANKNIFTY futures instrument
  5. fetch Spot LTP
  6. fetch futures LTP
  7. fetch daily candles (needed for levels/SMA)
  8. open the WebSocket and confirm live ticks arrive

It NEVER places, modifies or cancels an order, and it works for both paper and
live modes. Exit code 0 = all steps passed.
"""

from __future__ import annotations

import datetime as _dt
import threading
import time

from .angelone.auth import AngelAuth
from .angelone.instruments import InstrumentMaster
from .angelone.rest import AngelREST
from .angelone.websocket import AngelWebSocket
from .config import Settings
from .rules import StrategySpec, load_spec


class _Steps:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.results.append((name, True, detail))
        print(f"[PASS] {name}" + (f" - {detail}" if detail else ""))

    def fail(self, name: str, detail: str = "") -> None:
        self.results.append((name, False, detail))
        print(f"[FAIL] {name}" + (f" - {detail}" if detail else ""))

    @property
    def ok_all(self) -> bool:
        return all(r[1] for r in self.results)


def run_selftest(settings: Settings, spec: StrategySpec | None = None, ws_seconds: float = 8.0) -> int:
    spec = spec or load_spec(settings.strategy_path())
    steps = _Steps()
    print(f"Angel One self-test | strategy={spec.version} mode={settings.mode}\n")

    creds = settings.redacted_credentials()
    if all(v == "set" for v in creds.values()):
        steps.ok("credentials present", ", ".join(f"{k}=set" for k in creds))
    else:
        steps.fail("credentials present", f"{creds}  (run: setup)")
        return 1

    auth = AngelAuth(settings)
    try:
        auth.login()
        steps.ok("login", f"client={settings.secret(settings.angel_client_code)}")
    except Exception as exc:
        steps.fail("login", str(exc))
        return 1

    rest = AngelREST(settings, auth)
    try:
        master = InstrumentMaster(settings.instrument_cache_path, settings.angel_instrument_master_url)
        master.load()
        steps.ok("instrument master", f"{settings.instrument_cache_path}")
    except Exception as exc:
        steps.fail("instrument master", str(exc))
        return 1

    spot = None
    try:
        spot = master.resolve_spot(spec.instrument.spot_symbol, spec.instrument.spot_exchange)
        steps.ok("resolve Spot", f"{spot.symbol} token={spot.token} exchange={spot.exchange}")
    except Exception as exc:
        steps.fail("resolve Spot", str(exc))

    fut = None
    try:
        fut = master.resolve_futures(spec.instrument.symbol, spec.instrument.exchange, spec.instrument.contract)
        steps.ok(
            "resolve futures",
            f"{fut.symbol} token={fut.token} expiry={fut.expiry} lot_size={fut.lot_size}",
        )
    except Exception as exc:
        steps.fail("resolve futures", str(exc))

    if spot is not None:
        try:
            ltp = rest.ltp(spot.exchange, spot.symbol, spot.token)
            steps.ok("Spot LTP", f"{ltp}") if ltp else steps.fail("Spot LTP", "no value")
        except Exception as exc:
            steps.fail("Spot LTP", str(exc))

    if fut is not None:
        try:
            ltp = rest.ltp(fut.exchange, fut.symbol, fut.token)
            steps.ok("Futures LTP", f"{ltp}") if ltp else steps.fail("Futures LTP", "no value")
        except Exception as exc:
            steps.fail("Futures LTP", str(exc))

    if spot is not None:
        try:
            today = _dt.date.today()
            rows = rest.candles(
                spot.exchange, spot.token, "ONE_DAY",
                _dt.datetime.combine(today - _dt.timedelta(days=40), _dt.time(9, 15)),
                _dt.datetime.combine(today - _dt.timedelta(days=1), _dt.time(15, 30)),
            )
            if rows:
                steps.ok("daily candles", f"{len(rows)} rows")
            else:
                steps.fail("daily candles", "0 rows (check history permissions/range)")
        except Exception as exc:
            steps.fail("daily candles", str(exc))

    # ---- WebSocket live tick test (read-only) -----------------------------
    if spot is not None and fut is not None:
        received: list[tuple[str, float]] = []
        done = threading.Event()

        def _on_tick(token: str, ltp: float, ts) -> None:  # noqa: ANN001
            received.append((token, ltp))
            if len(received) >= 2:
                done.set()

        ws = AngelWebSocket(settings, auth, _on_tick)
        ws.subscribe([
            {"exchangeType": 1 if spot.exchange.upper() == "NSE" else 2, "tokens": [spot.token]},
            {"exchangeType": 2, "tokens": [fut.token]},
        ])
        ws.start()
        print(f"  ... listening for WebSocket ticks for {ws_seconds:.0f}s")
        got = done.wait(timeout=ws_seconds)
        ws.stop()
        if got or received:
            ex = ", ".join(f"{t}={v}" for t, v in received[:4])
            steps.ok("websocket ticks", f"{len(received)} tick(s): {ex}")
        else:
            steps.fail("websocket ticks", "no ticks received (market may be closed, or auth/frame issue)")

    print("\n" + ("ALL CHECKS PASSED" if steps.ok_all else "SOME CHECKS FAILED"))
    if not steps.ok_all:
        print("Note: 'websocket ticks' can legitimately fail when the market is closed.")
    return 0 if steps.ok_all else 1
