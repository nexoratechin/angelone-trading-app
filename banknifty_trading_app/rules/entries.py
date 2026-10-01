"""Entry trigger rules."""

from __future__ import annotations

from ..core.models import Side
from .base import EntryRule, RuleContext
from .registry import register_entry


@register_entry("reference_cross")
class ReferenceCrossEntry(EntryRule):
    """Cross of the reference Spot close.

    Signals are *not* produced here in isolation: the engine only acts on a
    signal when at least one :class:`Filter` also allows the side.

    Parameters
    ----------
    require_fresh_cross:
        When True, Spot must first be seen on the opposite side of the
        reference and then trade through it during the session. A gap that
        opens beyond the level does not auto-trigger.
    gap_open_counts:
        When True, an opening print already beyond the level is treated as an
        immediate valid cross (the engine arms the corresponding flag).
    """

    def __init__(self, require_fresh_cross: bool = True, gap_open_counts: bool = False) -> None:
        self.require_fresh_cross = require_fresh_cross
        self.gap_open_counts = gap_open_counts

    def observe(self, ctx: RuleContext) -> None:
        st = ctx.state
        if ctx.spot > ctx.ref_close:
            st.above_ref_seen = True
        elif ctx.spot < ctx.ref_close:
            st.below_ref_seen = True

    def signal(self, ctx: RuleContext) -> Side | None:
        st = ctx.state
        above = ctx.spot > ctx.ref_close
        below = ctx.spot < ctx.ref_close

        if self.require_fresh_cross:
            if above and st.below_ref_seen:
                return Side.BUY
            if below and st.above_ref_seen:
                return Side.SELL
            return None

        if above:
            return Side.BUY
        if below:
            return Side.SELL
        return None
