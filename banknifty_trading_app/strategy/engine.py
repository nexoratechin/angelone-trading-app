"""The strategy state machine.

This single engine is driven by *both* the live feed and the bar-replay
backtester, which is what guarantees backtest/live parity. It performs no I/O:
it consumes Spot/Futures prices and returns :class:`TradeIntent` objects for the
risk and execution layers to act on.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable

from ..core.models import (
    DayLevels,
    IntentKind,
    Side,
    StrategyEventRecord,
    TradeIntent,
    new_id,
)
from ..core.state import EngineMemory, StrategyState
from ..logging import get_logger
from ..rules import RuleContext, StrategySpec

log = get_logger("strategy.engine")

EventHandler = Callable[[StrategyEventRecord], None]


class StrategyEngine:
    def __init__(self, spec: StrategySpec, on_event: EventHandler | None = None) -> None:
        self.spec = spec
        self.state = StrategyState()
        self.memory = EngineMemory()
        self._on_event = on_event
        self._cycle_seq = 0
        self._reversals_today = 0

    # ------------------------------------------------------------------ setup
    def start_new_cycle(self) -> None:
        self._cycle_seq += 1
        self.state.start_new_cycle(f"cyc_{self.state.trading_day or ''}_{self._cycle_seq}")

    def start_day(
        self,
        levels: DayLevels,
        *,
        first_spot: float | None = None,
        first_futures: float | None = None,
        is_last_trading_day: bool = False,
        ts: _dt.datetime | None = None,
    ) -> None:
        ts = ts or _dt.datetime.combine(levels.trading_date, _dt.time(9, 15))
        self.memory.levels = levels
        self.state.trading_day = levels.trading_date
        self.state.is_last_trading_day = is_last_trading_day
        self.state.reset_day_crosses()
        self._reversals_today = 0
        if self.spec.one_trade_scope == "day":
            self.state.initial_trade_done = False

        if first_spot is not None:
            self.memory.last_spot = first_spot
            self.memory.last_futures = first_futures if first_futures is not None else first_spot
            self._arm_open(levels.ref_close, first_spot)

        ctx = self._ctx(first_spot or self.memory.last_spot, first_futures or self.memory.last_futures, ts)
        if self.state.has_position and self.state.stop_level is not None:
            candidate = self.spec.stop.daily_stop(self.state.side, ctx, self.state.stop_level)  # type: ignore[arg-type]
            self.state.stop_level = self._tighten(self.state.side, self.state.stop_level, candidate)  # type: ignore[arg-type]

        self._event(
            "day_start",
            f"{levels.trading_date} ref={levels.ref_close:.2f} prev={levels.prev_close:.2f} "
            f"sma={levels.sma if levels.sma is None else round(levels.sma, 2)} "
            f"last_day={is_last_trading_day}",
            ts,
        )

    def _arm_open(self, ref_close: float, first_spot: float) -> None:
        st = self.state
        gap_counts = getattr(self.spec.entry, "gap_open_counts", False)
        if gap_counts:
            # an opening print beyond the level is treated as a valid cross
            st.below_ref_seen = first_spot > ref_close
            st.above_ref_seen = first_spot < ref_close
        else:
            st.below_ref_seen = first_spot < ref_close
            st.above_ref_seen = first_spot > ref_close

    # ------------------------------------------------------------------ ticks
    def on_tick(
        self, spot: float, futures: float, ts: _dt.datetime
    ) -> list[TradeIntent]:
        intents: list[TradeIntent] = []
        st = self.state
        self.memory.last_spot = spot
        self.memory.last_futures = futures
        self.memory.last_tick_time = ts
        if self.memory.levels is None:
            return intents

        ctx = self._ctx(spot, futures, ts)

        if st.has_position:
            st.peak_spot = max(st.peak_spot, spot)
            st.trough_spot = min(st.trough_spot, spot)

        # 1) expiry square-off has absolute priority
        if st.has_position and self._expiry_due(ts):
            intents.extend(self._square_off(reason="expiry_square_off", ctx=ctx, close_cycle=True))
            return intents

        # 2) trailing rule (may also request a full exit)
        tr = self.spec.trailing
        if st.has_position and tr is not None and tr.enabled:
            if tr.wants_exit(ctx):
                intents.extend(self._square_off(reason="trailing_exit", ctx=ctx))
                return intents
            candidate = tr.update(ctx, st.stop_level)
            if candidate is not None:
                st.stop_level = self._tighten(st.side, st.stop_level, candidate)  # type: ignore[arg-type]

        # 3) stop-loss -> exit + reversal
        if (
            st.has_position
            and st.stop_level is not None
            and self._stop_check_allowed(ts)
            and self.spec.stop.breached(st.side, spot, st.stop_level)  # type: ignore[arg-type]
        ):
            intents.extend(self._stop_out(ctx))
            return intents

        # 4) partial profit
        if st.has_position and self.spec.partial.should_trigger(ctx):
            intents.extend(self._partial(ctx))

        # 5) fresh entry (only when flat)
        if not st.has_position and self._entry_allowed():
            self.spec.entry.observe(ctx)
            side = self.spec.entry.signal(ctx)
            if side is not None and self._filters_allow(side, ctx):
                intents.extend(self._enter(side, self.spec.instrument.lots, "entry_signal", ctx))

        return intents

    def force_square_off(self, ts: _dt.datetime, reason: str = "manual_square_off") -> list[TradeIntent]:
        if not self.state.has_position:
            return []
        ctx = self._ctx(self.memory.last_spot, self.memory.last_futures, ts)
        return self._square_off(reason=reason, ctx=ctx, close_cycle=True)

    # -------------------------------------------------------------- decision
    def _entry_allowed(self) -> bool:
        st = self.state
        if st.cycle_closed:
            return False
        return not st.initial_trade_done

    def _filters_allow(self, side: Side, ctx: RuleContext) -> bool:
        for flt in self.spec.filters:
            if not flt.allow(side, ctx):
                return False
        return True

    def _stop_check_allowed(self, ts: _dt.datetime) -> bool:
        st = self.state
        if st.entry_time is None:
            return True
        if st.entry_reason != "reversal":
            return True
        return (ts - st.entry_time).total_seconds() >= self.spec.reversal_cooldown_s

    def _expiry_due(self, ts: _dt.datetime) -> bool:
        st = self.state
        if not st.is_last_trading_day or self.spec.expiry_squareoff_time is None:
            return False
        cutoff = _dt.time.fromisoformat(self.spec.expiry_squareoff_time)
        return ts.timetz().replace(tzinfo=None) >= cutoff

    # --------------------------------------------------------------- actions
    def _enter(self, side: Side, lots: int, reason: str, ctx: RuleContext) -> list[TradeIntent]:
        st = self.state
        # set the entry context first so stop rules can see the entry price
        st.side = side
        st.lots_open = lots
        st.entry_spot = ctx.spot
        st.entry_futures = ctx.futures
        st.entry_time = ctx.ts
        st.entry_reason = reason
        st.partial_taken = False
        st.peak_spot = ctx.spot
        st.trough_spot = ctx.spot
        st.initial_trade_done = True

        stop = self.spec.stop.initial_stop(side, ctx)
        if reason == "reversal":
            if self.spec.reversal_stop_mode == "next_day":
                stop = None  # arms from the next day's previous close
            elif self.spec.reversal_buffer_points:
                buf = self.spec.reversal_buffer_points
                stop = stop - buf if side is Side.BUY else stop + buf
        st.stop_level = stop
        st.initial_stop = stop

        self._event(
            "enter",
            f"{side.value} {lots} lot(s) reason={reason} spot={ctx.spot:.2f} "
            f"stop={stop if stop is None else round(stop, 2)}",
            ctx.ts,
            {"side": side.value, "lots": lots, "stop": stop},
        )
        return [
            TradeIntent(
                kind=IntentKind.ENTER,
                side=side,
                lots=lots,
                reason=reason,
                spot_price=ctx.spot,
                futures_price=ctx.futures,
                timestamp=ctx.ts,
                strategy_version=self.spec.version,
                meta={"stop": stop, "entry_spot": ctx.spot, "is_reversal": reason == "reversal"},
            )
        ]

    def _partial(self, ctx: RuleContext) -> list[TradeIntent]:
        st = self.state
        lots = self.spec.partial.lots_to_exit
        st.lots_open -= lots
        st.partial_taken = True
        if self.spec.partial.move_stop_to_breakeven:
            st.stop_level = self._tighten(st.side, st.stop_level, st.entry_spot)  # type: ignore[arg-type]
        self._event(
            "partial_exit",
            f"{st.side.value} exit {lots} lot(s) at spot={ctx.spot:.2f} "
            f"remaining={st.lots_open} stop={st.stop_level}",
            ctx.ts,
            {"lots": lots, "remaining": st.lots_open, "stop": st.stop_level},
        )
        return [
            TradeIntent(
                kind=IntentKind.PARTIAL_EXIT,
                side=st.side,  # type: ignore[arg-type]
                lots=lots,
                reason="partial_target",
                spot_price=ctx.spot,
                futures_price=ctx.futures,
                timestamp=ctx.ts,
                strategy_version=self.spec.version,
                meta={"stop": st.stop_level, "remaining": st.lots_open},
            )
        ]

    def _stop_out(self, ctx: RuleContext) -> list[TradeIntent]:
        st = self.state
        closed_side = st.side
        closed_lots = st.lots_open
        stop = st.stop_level
        self._event(
            "stop_loss",
            f"{closed_side.value} stop hit at spot={ctx.spot:.2f} stop={stop}",
            ctx.ts,
            {"side": closed_side.value, "lots": closed_lots, "stop": stop},
        )
        intents = [
            TradeIntent(
                kind=IntentKind.EXIT,
                side=closed_side,  # type: ignore[arg-type]
                lots=closed_lots,
                reason="stop_loss",
                spot_price=ctx.spot,
                futures_price=ctx.futures,
                timestamp=ctx.ts,
                strategy_version=self.spec.version,
                meta={"stop": stop},
            )
        ]
        st.reset_position()
        st.initial_trade_done = True

        if self.spec.reversal_enabled and self._reversal_allowed():
            self._reversals_today += 1
            intents.extend(
                self._enter(closed_side.opposite, self.spec.reversal_lots, "reversal", ctx)
            )
        return intents

    def _reversal_allowed(self) -> bool:
        cap = self.spec.max_reversals_per_day
        return cap is None or self._reversals_today < cap

    def _square_off(
        self, *, reason: str, ctx: RuleContext, close_cycle: bool = False
    ) -> list[TradeIntent]:
        st = self.state
        side = st.side
        lots = st.lots_open
        self._event(
            reason,
            f"flatten {side.value} {lots} lot(s) at spot={ctx.spot:.2f}",
            ctx.ts,
            {"side": side.value, "lots": lots},
        )
        intents = [
            TradeIntent(
                kind=IntentKind.EXIT,
                side=side,  # type: ignore[arg-type]
                lots=lots,
                reason=reason,
                spot_price=ctx.spot,
                futures_price=ctx.futures,
                timestamp=ctx.ts,
                strategy_version=self.spec.version,
                meta={"stop": st.stop_level},
            )
        ]
        st.reset_position()
        st.initial_trade_done = True
        if close_cycle:
            st.cycle_closed = True
        return intents

    # -------------------------------------------------------------- helpers
    def _ctx(self, spot: float, futures: float, ts: _dt.datetime) -> RuleContext:
        levels = self.memory.levels
        return RuleContext(
            ts=ts,
            trading_day=ts.date(),
            spot=spot,
            futures=futures,
            ref_close=levels.ref_close if levels else 0.0,
            prev_close=levels.prev_close if levels else 0.0,
            sma=levels.sma if levels else None,
            state=self.state,
        )

    @staticmethod
    def _tighten(side: Side | None, current: float | None, candidate: float | None) -> float | None:
        if candidate is None:
            return current
        if current is None:
            return candidate
        if side is None:
            return candidate
        return max(current, candidate) if side is Side.BUY else min(current, candidate)

    def _event(
        self,
        event_type: str,
        detail: str,
        ts: _dt.datetime,
        payload: dict | None = None,
    ) -> None:
        log.info("[%s] %s", event_type, detail)
        if self._on_event is not None:
            self._on_event(
                StrategyEventRecord(
                    event_type=event_type,
                    detail=detail,
                    timestamp=ts,
                    strategy_version=self.spec.version,
                    payload=payload or {},
                )
            )
