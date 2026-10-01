"""Partial-profit (target) rules."""

from __future__ import annotations

from ..core.models import Side
from .base import PartialRule, RuleContext
from .registry import register_partial


@register_partial("percent_move")
class PercentPartialTarget(PartialRule):
    """Book ``lots_to_exit`` when Spot moves ``percent`` favourably from entry.

    Percent is measured on **Spot from the entry Spot**, matching the agreed
    rule. After a trigger the remaining lot's stop is moved to breakeven
    (ratcheted, so it can never loosen an already-better stop).
    """

    def __init__(
        self,
        percent: float = 1.7,
        lots_to_exit: int = 1,
        move_stop_to_breakeven: bool = True,
    ) -> None:
        self.percent = float(percent)
        self.lots_to_exit = int(lots_to_exit)
        self.move_stop_to_breakeven = move_stop_to_breakeven

    def _target(self, side: Side, entry: float) -> float:
        move = entry * self.percent / 100.0
        return entry + move if side is Side.BUY else entry - move

    def should_trigger(self, ctx: RuleContext) -> bool:
        st = ctx.state
        if st.partial_taken or st.side is None or st.entry_spot <= 0:
            return False
        if st.lots_open <= self.lots_to_exit:
            return False
        target = self._target(st.side, st.entry_spot)
        return ctx.spot >= target if st.side is Side.BUY else ctx.spot <= target
