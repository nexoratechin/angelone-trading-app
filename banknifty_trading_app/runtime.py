"""Live / paper runtime.

Wires the whole pipeline together and owns the process lifecycle:

    feed (thread) -> bus -> this loop -> engine -> risk -> order manager -> broker
                                         \\-> ledger/book -> repo (batched)

Design notes
------------
* All blocking work (initial data, REST) happens at start-up or on a schedule,
  never in the tick handler.
* The engine is the single strategy brain; the position book and trade ledger
  track what actually filled.
* Everything is cancellable via the kill switch and Ctrl+C.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from pathlib import Path

from .angelone.auth import AngelAuth
from .angelone.instruments import Instrument, InstrumentMaster
from .angelone.rest import AngelREST
from .config import Settings
from .core.bus import EventBus
from .core.events import Topic
from .core.models import Fill, Tick, TradeIntent
from .database.repository import Repository
from .database.session import create_database
from .database.writer import PersistenceWriter
from .execution.order_manager import OrderManager
from .execution.reconciler import reconcile
from .execution.recovery import recover
from .execution.router import build_executor
from .logging import get_logger, get_trade_logger
from .market_data.daily_closes import compute_levels
from .market_data.feed import MarketDataFeed
from .market_data.staleness import StalenessMonitor
from .market_data.store import MarketDataStore
from .positions.book import PositionBook
from .positions.ledger import TradeLedger
from .risk.kill_switch import KillSwitch
from .risk.manager import RiskManager
from .rules import StrategySpec, load_spec
from .strategy.engine import StrategyEngine

log = get_logger("runtime")
tradelog = get_trade_logger()

_TZ = _dt.timezone(_dt.timedelta(hours=5, minutes=30))  # IST


def _time(value: str) -> _dt.time:
    return _dt.time.fromisoformat(value)


class TradingRuntime:
    def __init__(self, settings: Settings, spec: StrategySpec | None = None) -> None:
        self.settings = settings
        self.spec = spec or load_spec(settings.strategy_path())
        self.bus = EventBus()
        self.store = MarketDataStore()

        self.session_factory = create_database(settings.db_path)
        self.writer = PersistenceWriter(self.session_factory)
        self.repo = Repository(self.session_factory, self.writer)
        self.kill_switch = KillSwitch(settings.kill_switch_file)

        self.risk = RiskManager(settings, self.kill_switch)
        self.book = PositionBook(self.spec.instrument.lot_size)
        self.ledger = TradeLedger(self.spec.instrument.lot_size, on_trade_close=self._on_trade_close)
        self.engine = StrategyEngine(self.spec, on_event=self.repo.log_event)

        self.auth = AngelAuth(settings)
        self.rest: AngelREST | None = None
        self.master: InstrumentMaster | None = None
        self.instrument: Instrument | None = None
        self.spot: Instrument | None = None
        self.feed: MarketDataFeed | None = None
        self.order_manager: OrderManager | None = None
        self.staleness = StalenessMonitor(
            settings.stale_tick_timeout_s, on_stale=self._on_stale
        )

        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._position_pub_counter = 0
        self._dashboard = None

    # ================================================================== start
    async def start(self) -> None:
        self.settings.ensure_dirs()
        self.writer.start()
        log.info(
            "Starting runtime mode=%s live=%s strategy=%s lot_size=%d",
            self.settings.mode, self.settings.is_live, self.spec.version, self.spec.instrument.lot_size,
        )
        log.info("Credentials: %s", self.settings.redacted_credentials())

        if not self.settings.is_live:
            log.warning("PAPER mode: orders are simulated. Set MODE=live and LIVE_TRADING=true to trade live.")

        self.master = InstrumentMaster(self.settings.instrument_cache_path, self.settings.angel_instrument_master_url)
        self.rest = AngelREST(self.settings, self.auth)
        # forces login (also required for market data)
        self.auth.login()

        self.spot = self.master.resolve_spot(self.spec.instrument.spot_symbol, self.spec.instrument.spot_exchange)
        self.instrument = self.master.resolve_futures(
            self.spec.instrument.symbol, self.spec.instrument.exchange, self.spec.instrument.contract
        )
        log.info(
            "Trading %s token=%s expiry=%s | Spot %s token=%s",
            self.instrument.symbol, self.instrument.token, self.instrument.expiry,
            self.spot.symbol, self.spot.token,
        )

        # crash recovery + broker reconciliation before trading
        report = recover(self.repo)
        if report.open_trades:
            log.warning(
                "Recovered %d open lots from last run; reconciling with broker before trading",
                report.recovered_lots,
            )
            await self._reconcile()

        self._build_order_manager()
        await self._seed_levels()

        self.bus.bind_loop(asyncio.get_running_loop())
        self.feed = MarketDataFeed(
            self.settings,
            self.auth,
            self.bus,
            self.store,
            subscriptions=[
                (self.spot.exchange, self.spot.token),
                (self.instrument.exchange, self.instrument.token),
            ],
            token_symbols={
                self.spot.token: self.spot.symbol,
                self.instrument.token: self.instrument.symbol,
            },
        )
        self.feed.start()

        self._dashboard = self._start_dashboard()

        self._running = True
        self._tasks = [
            asyncio.create_task(self._tick_loop(), name="tick-loop"),
            asyncio.create_task(self._housekeeping_loop(), name="housekeeping"),
            asyncio.create_task(self._day_loop(), name="day-loop"),
        ]
        log.info("Runtime started. Press Ctrl+C to stop.")
        await asyncio.gather(*self._tasks)

    async def stop(self) -> None:
        self._running = False
        if self.feed:
            self.feed.stop()
        for task in self._tasks:
            task.cancel()
        self.writer.stop()
        self.auth.logout()
        log.info("Runtime stopped")

    # ================================================================== ticks
    async def _tick_loop(self) -> None:
        sub = self.bus.subscribe(Topic.TICK)
        while self._running:
            tick: Tick = await sub.get()
            try:
                await self._handle_tick(tick)
            except Exception as exc:  # never let one bad tick kill the loop
                log.exception("Tick handling error: %s", exc)

    async def _handle_tick(self, tick: Tick) -> None:
        s = self.settings
        now = tick.timestamp
        t = now.timetz().replace(tzinfo=None)
        if not (_time(s.market_open) <= t <= _time(s.market_close)):
            return

        self.staleness.mark(now)
        is_spot = self.spot is not None and tick.token == self.spot.token

        if not is_spot:
            # futures tick: mark to market
            self.book.mark(tick.ltp)
            self._position_pub_counter += 1
            if self._position_pub_counter % 50 == 0:
                self._publish_position()
            return

        spot = tick.ltp
        futures = self.store.ltp(self.instrument.token) if self.instrument else None
        if futures is None:
            return

        intents = self.engine.on_tick(spot, futures, now)
        self.risk.set_open_lots(self.engine.state.lots_open)
        for intent in intents:
            await self._process_intent(intent, now)

    async def _process_intent(self, intent: TradeIntent, now: _dt.datetime) -> None:
        ok, reason = self.risk.evaluate(intent, now)
        self.bus.publish(Topic.RISK_DECISION, {"intent": intent.intent_id, "allowed": ok, "reason": reason})
        if not ok:
            self.risk.mark_rejected(intent, reason)
            tradelog.info("REJECTED %s %s x%d (%s)", intent.kind.value, intent.side.value, intent.lots, reason)
            return
        assert self.order_manager is not None
        await self.order_manager.handle_intent(intent)

    # ================================================================ fills
    def _on_fill(self, fill: Fill) -> None:
        self.book.on_fill(fill)
        self.ledger.on_fill(fill)
        self.risk.on_fill(fill)
        self.repo.log_fill(fill)
        self.risk.set_open_lots(self.book.open_lots)
        self.risk.set_daily_pnl(self.book.snapshot().total_money)
        self.bus.publish(Topic.FILL, fill)
        self._publish_position()

    def _on_trade_close(self, trade) -> None:
        self.repo.log_trade(trade)

    # ================================================================ position
    def _publish_position(self) -> None:
        snap = self.book.snapshot()
        self.bus.publish(
            Topic.POSITION,
            {
                "side": snap.side.value if snap.side else None,
                "lots": snap.lots,
                "avg_price": snap.avg_price,
                "mark": snap.mark_price,
                "realized": snap.realized_money,
                "unrealized": snap.unrealized_money,
                "total": snap.total_money,
                "stop": self.engine.state.stop_level,
                "cycle": self.engine.state.cycle_id,
            },
        )

    # ================================================================ levels
    async def _seed_levels(self) -> None:
        closes = await asyncio.to_thread(self._fetch_daily_closes)
        if not closes:
            log.error("Could not seed daily closes; entries will be blocked until levels exist")
            return
        self.store.set_closes(self.spec.instrument.spot_symbol, closes)
        await self._start_day_for_today(closes)

    def _fetch_daily_closes(self, lookback_days: int = 90) -> list[float]:
        assert self.rest is not None and self.spot is not None
        today = _dt.date.today()
        start = today - _dt.timedelta(days=lookback_days)
        from_dt = _dt.datetime.combine(start, _dt.time(9, 15))
        to_dt = _dt.datetime.combine(today - _dt.timedelta(days=1), _dt.time(15, 30))
        rows = self.rest.candles(self.spot.exchange, self.spot.token, "ONE_DAY", from_dt, to_dt)
        closes: list[float] = []
        for row in rows:
            try:
                closes.append(float(row[4]))
            except (IndexError, TypeError, ValueError):
                continue
        return closes

    async def _start_day_for_today(self, closes: list[float]) -> None:
        today = _dt.date.today()
        try:
            levels = compute_levels(
                today,
                closes,
                reference_offset_days=self.spec.reference_offset_days,
                sma_period=self.spec.sma_period,
                require_sma=True,
            )
        except Exception as exc:
            log.error("Cannot compute levels for %s: %s", today, exc)
            return
        is_last = self._is_last_trading_day(today)
        if not self.engine.state.cycle_id:
            self.engine.start_new_cycle()
            self.ledger.set_cycle(self.engine.state.cycle_id)
        self.engine.start_day(levels, is_last_trading_day=is_last)
        tradelog.info(
            "LEVELS %s ref=%.2f prev=%.2f sma=%.2f last_day=%s",
            today, levels.ref_close, levels.prev_close, levels.sma or 0.0, is_last,
        )

    def _is_last_trading_day(self, day: _dt.date) -> bool:
        if self.instrument is None or self.instrument.expiry is None:
            return False
        expiry = self.instrument.expiry
        # last weekday on or before expiry
        candidate = expiry
        while candidate.weekday() >= 5:
            candidate -= _dt.timedelta(days=1)
        return day == candidate

    # ============================================================ housekeeping
    async def _housekeeping_loop(self) -> None:
        while self._running:
            await asyncio.sleep(2)
            try:
                self.staleness.check()
                self.risk.set_daily_pnl(self.book.snapshot().total_money)
                if self.kill_switch.is_active():
                    log.warning("Kill switch active: %s", self.kill_switch.reason)
            except Exception as exc:
                log.exception("Housekeeping error: %s", exc)

    async def _day_loop(self) -> None:
        """Refresh levels each morning; append today's close after the session."""
        last_day = _dt.date.today()
        while self._running:
            await asyncio.sleep(30)
            now = _dt.datetime.now(_TZ).replace(tzinfo=None)
            today = now.date()
            t = now.time()

            if today != last_day:
                last_day = today
                self.risk.reset_day()
                self.engine.state.cycle_closed = False
                closes = await asyncio.to_thread(self._fetch_daily_closes)
                if closes:
                    self.store.set_closes(self.spec.instrument.spot_symbol, closes)
                    await self._start_day_for_today(closes)

            # safety square-off for expiry if ticks stopped arriving
            if (
                self.engine.state.has_position
                and self.engine.state.is_last_trading_day
                and self.spec.expiry_squareoff_time
                and t >= _time(self.spec.expiry_squareoff_time)
            ):
                for intent in self.engine.force_square_off(now, "expiry_square_off_scheduled"):
                    await self._process_intent(intent, now)

    # ================================================================ helpers
    def _build_order_manager(self) -> None:
        assert self.instrument is not None
        executor = build_executor(self.settings, self.store, rest=self.rest, instrument=self.instrument)
        self.order_manager = OrderManager(
            self.settings,
            executor,
            self.instrument,
            self.spec.instrument.lot_size,
            self.spec.instrument.product_type,
            on_fill=self._on_fill,
            on_order=self.repo.log_order,
        )

    async def _reconcile(self) -> None:
        if self.rest is None or self.instrument is None:
            return
        rows = await asyncio.to_thread(self.rest.positions)
        snap = self.book.snapshot()
        reconcile(snap.side, snap.lots * self.spec.instrument.lot_size, rows, self.instrument.symbol)

    def _on_stale(self) -> None:
        log.error("Stale market data detected - new entries should be treated with caution")
        self.bus.publish(Topic.HEALTH, {"source": "staleness", "status": "stale"})

    def _start_dashboard(self):
        if not self.settings.dashboard_enabled:
            return None
        try:
            from .dashboard.app import start_dashboard

            return start_dashboard(self.settings, self.bus, self)
        except Exception as exc:  # dashboard is non-critical
            log.warning("Dashboard disabled: %s", exc)
            return None
