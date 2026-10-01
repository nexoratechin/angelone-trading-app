"""Trailing-stop rules.

The original notes mention a "2% Bank Nifty move" rule without defining its
exact calculation. Rather than invent it, :class:`PercentMovePlaceholder`
exposes the unknowns as configuration and does nothing until you choose a mode.
"""

from __future__ import annotations

from ..core.models import Side
from .base import RuleContext, TrailingRule
from .registry import register_trailing


@register_trailing("none")
class NoTrailing(TrailingRule):
    enabled = False

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled


@register_trailing("percent_trail")
class PercentTrailing(TrailingRule):
    """Trail the stop ``percent`` behind the best favourable Spot extreme."""

    enabled = True

    def __init__(self, percent: float = 1.0, activate_after_percent: float = 0.0) -> None:
        self.percent = float(percent)
        self.activate_after_percent = float(activate_after_percent)

    def update(self, ctx: RuleContext, current_stop: float | None) -> float | None:
        st = ctx.state
        if not st.has_position or st.entry_spot <= 0:
            return current_stop
        move = st.entry_spot * self.activate_after_percent / 100.0
        reached = (
            ctx.spot >= st.entry_spot + move
            if st.side is Side.BUY
            else ctx.spot <= st.entry_spot - move
        )
        if not reached:
            return current_stop
        if st.side is Side.BUY:
            candidate = st.peak_spot * (1 - self.percent / 100.0)
        else:
            candidate = st.trough_spot * (1 + self.percent / 100.0)
        if current_stop is None:
            return candidate
        return max(current_stop, candidate) if st.side is Side.BUY else min(current_stop, candidate)


@register_trailing("move_stop_to_entry")
class MoveStopToEntryTrailing(TrailingRule):
    """Once ``percent`` of favourable move is seen, lock the stop at breakeven."""

    enabled = True

    def __init__(self, percent: float = 2.0) -> None:
        self.percent = float(percent)

    def update(self, ctx: RuleContext, current_stop: float | None) -> float | None:
        st = ctx.state
        if not st.has_position or st.entry_spot <= 0:
            return current_stop
        move = st.entry_spot * self.percent / 100.0
        reached = (
            ctx.spot >= st.entry_spot + move
            if st.side is Side.BUY
            else ctx.spot <= st.entry_spot - move
        )
        if not reached:
            return current_stop
        if current_stop is None:
            return st.entry_spot
        return max(current_stop, st.entry_spot) if st.side is Side.BUY else min(current_stop, st.entry_spot)


@register_trailing("percent_move_placeholder")
class PercentMovePlaceholder(TrailingRule):
    """The undefined "2% move" rule, made explicit and configurable.

    mode:
      * ``disabled``            - do nothing (default)
      * ``move_stop_to_entry``  - at +percent, move stop to breakeven
      * ``trail_percent``       - at +percent, trail by ``trail_percent``
      * ``exit_remaining``      - at +percent, request exit of the carried lot
      * ``custom``              - refuses to load until you implement it here
    """

    enabled = True

    def __init__(
        self,
        percent: float = 2.0,
        mode: str = "disabled",
        trail_percent: float | None = None,
    ) -> None:
        self.percent = float(percent)
        self.mode = mode
        self.trail_percent = float(trail_percent) if trail_percent is not None else self.percent
        self._exit_requested = False
        if mode == "custom":
            raise NotImplementedError(
                "trailing mode 'custom' has no implementation yet - define the rule in "
                "rules/trailing.py:PercentMovePlaceholder before selecting it."
            )
        if mode not in {"disabled", "move_stop_to_entry", "trail_percent", "exit_remaining"}:
            raise ValueError(f"unknown percent_move_placeholder mode: {mode!r}")

    def _reached(self, ctx: RuleContext) -> bool:
        st = ctx.state
        if not st.has_position or st.entry_spot <= 0:
            return False
        move = st.entry_spot * self.percent / 100.0
        return (
            ctx.spot >= st.entry_spot + move
            if st.side is Side.BUY
            else ctx.spot <= st.entry_spot - move
        )

    def wants_exit(self, ctx: RuleContext) -> bool:
        if self.mode != "exit_remaining":
            return False
        if self._exit_requested:
            return False
        if self._reached(ctx):
            self._exit_requested = True
            return True
        return False

    def update(self, ctx: RuleContext, current_stop: float | None) -> float | None:
        if self.mode == "disabled" or self.mode == "exit_remaining":
            return current_stop
        if not self._reached(ctx):
            return current_stop
        st = ctx.state
        if self.mode == "move_stop_to_entry":
            candidate = st.entry_spot
        else:  # trail_percent
            candidate = (
                st.peak_spot * (1 - self.trail_percent / 100.0)
                if st.side is Side.BUY
                else st.trough_spot * (1 + self.trail_percent / 100.0)
            )
        if current_stop is None:
            return candidate
        return max(current_stop, candidate) if st.side is Side.BUY else min(current_stop, candidate)
