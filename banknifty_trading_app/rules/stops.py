"""Stop-loss rules (all levels expressed on Spot)."""

from __future__ import annotations

from ..core.models import Side
from .base import RuleContext, StopRule
from .registry import register_stop


@register_stop("daily_close")
class DailyCloseStop(StopRule):
    """Yesterday's Spot close is the support/resistance level.

    * On the entry day the initial stop is ``prev_close``.
    * From the next day, the stop is recomputed from the new ``prev_close`` and
      **ratcheted** so it can only ever tighten (never loosen against the
      position), per the agreed rule.
    """

    def __init__(self, ratchet: bool = True, protective_only: bool = True) -> None:
        self.ratchet = ratchet
        self.protective_only = protective_only

    def initial_stop(self, side: Side, ctx: RuleContext) -> float:
        """Yesterday's close as support/resistance.

        With ``protective_only`` (the default) the level is used only when it is
        actually protective of the entry - below a long, above a short. If the
        close is on the wrong side (e.g. a long entered beneath yesterday's
        close), we fall back to the entry price so the position is not stopped
        out instantly. Set ``protective_only: false`` for the literal reading.
        """
        entry = ctx.state.entry_spot or ctx.spot
        candidate = ctx.prev_close
        if not self.protective_only:
            return candidate
        if side is Side.BUY and candidate < entry:
            return candidate
        if side is Side.SELL and candidate > entry:
            return candidate
        return entry

    def daily_stop(self, side: Side, ctx: RuleContext, current_stop: float | None) -> float:
        candidate = ctx.prev_close
        if current_stop is None:
            return candidate
        if not self.ratchet:
            return candidate
        return self.tighten(side, current_stop, candidate)


@register_stop("fixed_percent")
class FixedPercentStop(StopRule):
    """Alternative stop: a fixed % away from the entry Spot.

    Provided as a worked example of swapping a rule purely from YAML.
    """

    def __init__(self, percent: float = 1.0) -> None:
        self.percent = float(percent)

    def initial_stop(self, side: Side, ctx: RuleContext) -> float:
        entry = ctx.state.entry_spot or ctx.spot
        move = entry * self.percent / 100.0
        return entry - move if side is Side.BUY else entry + move

    def daily_stop(self, side: Side, ctx: RuleContext, current_stop: float | None) -> float:
        # no daily re-anchoring for a fixed distance stop
        return current_stop if current_stop is not None else self.initial_stop(side, ctx)
