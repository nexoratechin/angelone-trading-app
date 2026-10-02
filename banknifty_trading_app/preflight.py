"""Go-live preflight checklist.

Answers the only question that matters before real money is at risk:
**"what do I need on my side?"**

Every check here is strictly read-only. This module never places, modifies or
cancels an order, and it never enables live trading. Run it as often as you
like:

    python -m banknifty_trading_app.main preflight              # offline checks
    python -m banknifty_trading_app.main preflight --online     # + broker login

Checks are graded:

    PASS  verified now
    WARN  not fatal, but fix it before you arm live trading
    FAIL  live trading must not be armed while this is failing
    INFO  something only a human / the broker can confirm
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from .config import Settings
from .logging import get_logger
from .rules import StrategySpec

log = get_logger("preflight")

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"


@dataclass
class Report:
    rows: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, level: str, section: str, message: str) -> None:
        self.rows.append((level, section, message))

    @property
    def failures(self) -> list[str]:
        return [m for lvl, _, m in self.rows if lvl == FAIL]

    @property
    def warnings(self) -> list[str]:
        return [m for lvl, _, m in self.rows if lvl == WARN]

    def render(self) -> None:
        section = None
        for level, sect, message in self.rows:
            if sect != section:
                print(f"\n[{sect}]")
                section = sect
            print(f"  {level:<4} {message}")


def _time(value: str) -> _dt.time:
    return _dt.time.fromisoformat(value)


# ==================================================================== sections
def _check_interlock(settings: Settings, r: Report) -> None:
    s = "1. Live-trading interlock"
    for name, value, ok in (
        ("MODE", settings.mode, settings.mode == "live"),
        ("LIVE_TRADING", settings.live_trading, settings.live_trading),
        ("LIVE_ARMED", settings.live_armed, settings.live_armed),
    ):
        level = PASS if ok else INFO
        r.add(level, s, f"{name}={value}")

    reason = settings.live_block_reason()
    if reason:
        r.add(
            PASS,
            s,
            "Live trading is locked - no real orders can be placed. Good while paper trading.",
        )
    else:
        r.add(
            WARN,
            s,
            "LIVE IS ARMED. Real orders will be placed. Run 'disarm-live' to lock it.",
        )


def _check_credentials(settings: Settings, r: Report) -> None:
    s = "2. Angel One account"
    creds = settings.redacted_credentials()
    missing = [k for k, v in creds.items() if v != "set"]
    if missing:
        r.add(FAIL, s, f"credentials missing: {', '.join(missing)}  (run: setup)")
    else:
        r.add(PASS, s, "API key, client code, PIN and TOTP secret are all present")

    r.add(INFO, s, "SmartAPI app must be ACTIVE in the Angel One portal (not deleted/expired)")
    r.add(INFO, s, "Your static IP must be whitelisted if the API key is IP-locked")
    r.add(INFO, s, "TOTP secret must be the base32 seed, not a rotating 6-digit code")
    r.add(INFO, s, "Contract notes / MIS enabled in the account if you use intraday product")


def _check_risk(settings: Settings, spec: StrategySpec, r: Report) -> None:
    s = "3. Risk limits"
    lots = spec.instrument.lots
    if settings.max_lots < lots:
        r.add(
            FAIL,
            s,
            f"MAX_LOTS={settings.max_lots} < strategy lots={lots}: every order would be rejected",
        )
    else:
        r.add(PASS, s, f"MAX_LOTS={settings.max_lots} allows the strategy's {lots} lots")

    if settings.max_daily_loss is None:
        r.add(WARN, s, "MAX_DAILY_LOSS is not set: no automatic stop for a losing day")
    else:
        r.add(PASS, s, f"MAX_DAILY_LOSS={settings.max_daily_loss}")

    if settings.max_trades_per_day is None:
        r.add(WARN, s, "MAX_TRADES_PER_DAY is not set: no cap on how many times it may re-enter")
    else:
        r.add(PASS, s, f"MAX_TRADES_PER_DAY={settings.max_trades_per_day}")

    from .risk.kill_switch import KillSwitch

    ks = KillSwitch(settings.kill_switch_file)
    if ks.is_active():
        r.add(WARN, s, f"kill switch is TRIPPED ({ks.reason}) - trading is blocked until reset")
    else:
        r.add(PASS, s, "kill switch is not tripped")

    r.add(INFO, s, "Verify the kill switch works: run 'kill test', confirm trading halts, 'unkill'")


def _check_instrument(spec: StrategySpec, r: Report) -> None:
    s = "4. Instrument & contract"
    i = spec.instrument
    r.add(PASS, s, f"trading {i.symbol} on {i.exchange}, signals from {i.spot_symbol} ({i.spot_exchange})")
    r.add(PASS, s, f"{i.lots} lots x lot_size {i.lot_size} = {i.lots * i.lot_size} qty per entry")
    r.add(
        WARN,
        s,
        f"lot_size={i.lot_size} is set in YAML - CONFIRM the current NSE BANKNIFTY lot size. "
        "A wrong lot size silently sends the wrong quantity.",
    )
    if spec.reversal_enabled:
        r.add(
            PASS,
            s,
            f"reversal on: {spec.reversal_lots} lots, cap {spec.max_reversals_per_day or 'none'}/day, "
            f"cooldown {spec.reversal_cooldown_s}s",
        )
    else:
        r.add(PASS, s, "reversal disabled")


def _check_session(settings: Settings, spec: StrategySpec, r: Report) -> None:
    s = "5. Session window (IST)"
    open_t, close_t = _time(settings.market_open), _time(settings.market_close)
    if open_t < _time("09:15"):
        r.add(WARN, s, f"MARKET_OPEN={settings.market_open} is before the 09:15 pre-open")
    elif open_t > _time("09:30"):
        r.add(INFO, s, f"MARKET_OPEN={settings.market_open} skips the first minutes")
    else:
        r.add(PASS, s, f"MARKET_OPEN={settings.market_open}")

    r.add(PASS, s, f"MARKET_CLOSE={settings.market_close}")

    squareoff = spec.expiry_squareoff_time or settings.expiry_squareoff_time
    if squareoff and _time(squareoff) < close_t:
        r.add(PASS, s, f"expiry square-off at {squareoff}, before the {settings.market_close} close")
    elif squareoff:
        r.add(WARN, s, f"expiry square-off {squareoff} is at/after the {settings.market_close} close")
    else:
        r.add(WARN, s, "no expiry square-off time configured: an expiry-day position may carry over")

    if settings.product_type == "NRML":
        r.add(INFO, s, "PRODUCT_TYPE=NRML carries overnight - you need overnight margin")
    else:
        r.add(INFO, s, f"PRODUCT_TYPE={settings.product_type} is intraday; positions auto-square at 15:15")


def _check_execution(settings: Settings, spec: StrategySpec, r: Report) -> None:
    s = "6. Execution model"
    if settings.order_type == "MARKET":
        r.add(PASS, s, "ORDER_TYPE=MARKET: fills at best available price, no price control")
    else:
        r.add(
            WARN,
            s,
            f"ORDER_TYPE=LIMIT at the last tick: a fast-moving market may not fill",
        )
    r.add(
        PASS,
        s,
        f"paper fills simulate {settings.paper_slippage_points} pt slippage; "
        "live fills are whatever the exchange gives you - usually worse",
    )
    r.add(PASS, s, f"order status timeout {settings.order_status_timeout_s}s (no blind fill assumption)")
    if spec.trailing is not None:
        r.add(
            WARN,
            s,
            "the '2% move' trailing rule is an undecided placeholder - decide its formula "
            "in the strategy YAML before trading it live",
        )
    else:
        r.add(PASS, s, "no trailing rule configured")


def _check_operations(r: Report) -> None:
    s = "7. Operations (only you can confirm)"
    for item in (
        "Machine stays ON and connected for the whole 09:15-15:30 window",
        "No OS sleep / hibernate / screen-lock that pauses the process",
        "Reliable internet; the WebSocket auto-reconnects but gaps lose ticks",
        "Angel One must allow unattended API order placement for your account",
        "Broker-side RMS / risk limits are not tighter than this app's limits",
        "You know how to flatten the position manually from the broker terminal",
        "You have watched paper results for several sessions and trust the P&L",
        "Account funds cover margin for the worst case, including reversal chains",
    ):
        r.add(INFO, s, item)


# ===================================================================== online
def _check_online(settings: Settings, spec: StrategySpec, r: Report) -> None:
    s = "8. Broker session (--online)"
    from .angelone.auth import AngelAuth
    from .angelone.instruments import InstrumentMaster
    from .angelone.rest import AngelREST

    auth = AngelAuth(settings)
    try:
        auth.login()
        r.add(PASS, s, "login succeeded (API key + PIN + TOTP accepted)")
    except Exception as exc:
        r.add(FAIL, s, f"login failed: {exc}")
        return

    rest = AngelREST(settings, auth)
    try:
        master = InstrumentMaster(settings.instrument_cache_path, settings.angel_instrument_master_url)
        master.load()
    except Exception as exc:
        r.add(FAIL, s, f"instrument master unavailable: {exc}")
        return

    try:
        fut = master.resolve_futures(
            spec.instrument.symbol, spec.instrument.exchange, spec.instrument.contract
        )
        r.add(PASS, s, f"futures resolved: {fut.symbol} token={fut.token} expiry={fut.expiry}")
        if fut.lot_size and fut.lot_size != spec.instrument.lot_size:
            r.add(
                FAIL,
                s,
                f"LOT SIZE MISMATCH: broker says {fut.lot_size}, strategy YAML says "
                f"{spec.instrument.lot_size}. Fix instrument.lot_size before going live.",
            )
        else:
            r.add(PASS, s, f"lot size agrees with broker ({fut.lot_size})")
    except Exception as exc:
        r.add(FAIL, s, f"futures instrument not found: {exc}")

    try:
        spot = master.resolve_spot(spec.instrument.spot_symbol, spec.instrument.spot_exchange)
        r.add(PASS, s, f"spot resolved: {spot.symbol} token={spot.token}")
    except Exception as exc:
        r.add(FAIL, s, f"spot instrument not found: {exc}")

    try:
        rows = rest.order_book()
        r.add(PASS, s, f"order book reachable ({len(rows)} orders today)")
    except Exception as exc:
        r.add(WARN, s, f"order book unavailable: {exc}")

    try:
        positions = rest.positions()
        open_rows = [p for p in positions if float(p.get("netqty") or 0) != 0]
        if open_rows:
            r.add(
                WARN,
                s,
                f"you already hold {len(open_rows)} open position(s) - flatten before arming live, "
                "otherwise startup reconciliation will fight them",
            )
        else:
            r.add(PASS, s, "no open positions at the broker")
    except Exception as exc:
        r.add(WARN, s, f"positions unavailable: {exc}")

    try:
        data = (rest.profile().get("data") or {}) if isinstance(rest.profile(), dict) else {}
        cash = data.get("availablecash") or data.get("availablemargin")
        if cash is not None:
            r.add(PASS, s, f"available cash reported by broker: {cash}")
        else:
            r.add(INFO, s, "broker did not report available cash - check margin in the terminal")
    except Exception as exc:
        r.add(INFO, s, f"could not read profile/funds: {exc}")

    r.add(
        INFO,
        s,
        "WebSocket tick test: run  python -m banknifty_trading_app.main check",
    )


# ====================================================================== entry
def run_preflight(settings: Settings, spec: StrategySpec | None = None, online: bool = False) -> int:
    from .rules import load_spec

    spec = spec or load_spec(settings.strategy_path())
    r = Report()

    print("=" * 72)
    print("GO-LIVE PREFLIGHT  -  read-only, places no orders")
    print(f"strategy={spec.version}  mode={settings.mode}  armed={settings.live_armed}")
    print("=" * 72)

    _check_interlock(settings, r)
    _check_credentials(settings, r)
    _check_risk(settings, spec, r)
    _check_instrument(spec, r)
    _check_session(settings, spec, r)
    _check_execution(settings, spec, r)
    _check_operations(r)
    if online:
        _check_online(settings, spec, r)
    else:
        r.add(INFO, "8. Broker session", "skipped - add --online to test login and instruments")

    r.render()

    print("\n" + "=" * 72)
    if r.failures:
        print(f"VERDICT: NOT READY - {len(r.failures)} blocking issue(s):")
        for m in r.failures:
            print(f"  - {m}")
        print("\nFix these before running 'arm-live'.")
    else:
        print("VERDICT: no blocking issues found.")
        if settings.is_live:
            print("Live trading is ARMED - be very careful.")
        else:
            print("Live trading is still LOCKED (safe).")
        if not online:
            print("Next: python -m banknifty_trading_app.main preflight --online")
        else:
            print("Next: paper trade first, then 'arm-live' when you are satisfied.")
    print("=" * 72)

    if r.failures:
        return 1
    return 0