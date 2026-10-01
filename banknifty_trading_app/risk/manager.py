"""Risk manager: the single gate every order passes through.

Order of evaluation:
  1. kill switch        - stop everything new
  2. duplicate guard    - never send the same intent twice
  3. configured gates   - lot size, trade count, daily loss, trading hours
Exits are always allowed so risk limits can never trap an open position.
"""

from __future__ import annotations

import datetime as _dt

from ..config import Settings
from ..core.models import Fill, IntentKind, IntentStatus, TradeIntent
from ..logging import get_logger
from ..rules.risk_gates import DuplicateGuard, RiskGate, RiskSnapshot
from .kill_switch import KillSwitch
from .limits import build_gates

log = get_logger("risk.manager")


class RiskManager:
    def __init__(
        self,
        settings: Settings,
        kill_switch: KillSwitch,
        gates: list[RiskGate] | None = None,
    ) -> None:
        self.settings = settings
        self.kill_switch = kill_switch
        self.gates = gates if gates is not None else build_gates(settings)
        self.duplicates = DuplicateGuard()

        self.daily_pnl = 0.0
        self.trades_today = 0
        self.open_lots = 0
        self._loss_breached = False

    # ------------------------------------------------------------- counters
    def set_daily_pnl(self, pnl: float) -> None:
        self.daily_pnl = float(pnl)
        self._maybe_trip_daily_loss()

    def on_fill(self, fill: Fill) -> None:
        if fill.reason in ("entry_signal", "reversal"):
            self.trades_today += 1

    def set_open_lots(self, lots: int) -> None:
        self.open_lots = int(lots)

    def reset_day(self) -> None:
        self.daily_pnl = 0.0
        self.trades_today = 0
        self._loss_breached = False

    def _maybe_trip_daily_loss(self) -> None:
        if self.settings.max_daily_loss is None or self._loss_breached:
            return
        if self.daily_pnl <= -abs(self.settings.max_daily_loss):
            self._loss_breached = True
            self.kill_switch.trip(f"max_daily_loss reached: {self.daily_pnl:.2f}")

    # ------------------------------------------------------------- evaluate
    def evaluate(self, intent: TradeIntent, now: _dt.datetime) -> tuple[bool, str]:
        # exits are always permitted
        if intent.kind in (IntentKind.EXIT, IntentKind.PARTIAL_EXIT):
            return True, "exit_allowed"

        if self.kill_switch.is_active():
            return False, f"kill_switch_active({self.kill_switch.reason})"

        if self.duplicates.is_duplicate(intent):
            return False, "duplicate_intent"

        snap = RiskSnapshot(
            daily_pnl=self.daily_pnl,
            trades_today=self.trades_today,
            open_lots=self.open_lots,
        )
        for gate in self.gates:
            ok, reason = gate.check(intent, snap, now)
            if not ok:
                return False, reason
        return True, "ok"

    def mark_rejected(self, intent: TradeIntent, reason: str) -> None:
        intent.status = IntentStatus.REJECTED
        log.warning("Intent rejected: %s kind=%s side=%s reason=%s", intent.intent_id, intent.kind, intent.side, reason)
