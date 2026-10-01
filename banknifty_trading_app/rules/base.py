"""Rule interfaces.

Every strategy rule is a small, independently replaceable object. The engine
knows only these interfaces, so adding/removing/changing a rule never touches
market data, risk, execution, persistence or reporting.

A rule instance is built from a plain ``dict`` (parsed from a strategy YAML
spec) via :meth:`ConfigurableRule.from_config`, which makes the YAML the single
place you edit to change strategy behaviour.
"""

from __future__ import annotations

import datetime as _dt
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, TypeVar

from ..core.models import Side
from ..core.state import StrategyState

T = TypeVar("T", bound="ConfigurableRule")


@dataclass(slots=True)
class RuleContext:
    """Immutable-ish snapshot handed to rules on each evaluation."""

    ts: _dt.datetime
    trading_day: _dt.date
    spot: float
    futures: float
    ref_close: float   # day-before-yesterday Spot close -> entry trigger
    prev_close: float  # yesterday Spot close -> support/resistance & stop
    sma: float | None
    state: StrategyState


class ConfigurableRule(ABC):
    """Base class giving every rule a registry name and dict-based construction."""

    rule_name: ClassVar[str] = ""

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None) -> "ConfigurableRule":
        cfg = dict(cfg or {})
        cfg.pop("type", None)
        cfg.pop("enabled_note", None)
        return cls(**cfg)  # type: ignore[arg-type]


class Filter(ConfigurableRule):
    """A gate that must pass before an entry on ``side`` is allowed."""

    @abstractmethod
    def allow(self, side: Side, ctx: RuleContext) -> bool:  # pragma: no cover
        raise NotImplementedError


class EntryRule(ConfigurableRule):
    """Decides whether the current tick is an entry trigger (and which side)."""

    def observe(self, ctx: RuleContext) -> None:
        """Update any internal cross/arming state from the tick. Optional."""

    @abstractmethod
    def signal(self, ctx: RuleContext) -> Side | None:  # pragma: no cover
        raise NotImplementedError


class StopRule(ConfigurableRule):
    """Computes and ratchets the protective stop (expressed on Spot)."""

    @abstractmethod
    def initial_stop(self, side: Side, ctx: RuleContext) -> float:  # pragma: no cover
        raise NotImplementedError

    @abstractmethod
    def daily_stop(
        self, side: Side, ctx: RuleContext, current_stop: float | None
    ) -> float:  # pragma: no cover
        raise NotImplementedError

    def breached(self, side: Side, spot: float, stop: float) -> bool:
        return spot <= stop if side is Side.BUY else spot >= stop

    def tighten(self, side: Side, a: float, b: float) -> float:
        """Return the tighter (never looser) of two stops."""
        return max(a, b) if side is Side.BUY else min(a, b)


class PartialRule(ConfigurableRule):
    """Decides when to book a partial profit on the running position."""

    lots_to_exit: int = 1
    move_stop_to_breakeven: bool = True

    @abstractmethod
    def should_trigger(self, ctx: RuleContext) -> bool:  # pragma: no cover
        raise NotImplementedError


class TrailingRule(ConfigurableRule):
    """Optional trailing logic applied to the carried position."""

    enabled: bool = False

    def update(self, ctx: RuleContext, current_stop: float | None) -> float | None:
        """Return a possibly-tightened stop, or the unchanged stop."""
        return current_stop

    def wants_exit(self, ctx: RuleContext) -> bool:
        """Set by trailing rules that flatten the carried lot (e.g. exit_remaining)."""
        return False


class ExpiryRule(ConfigurableRule):
    """Decides whether to flatten on the last trading day of the contract."""

    @abstractmethod
    def should_square_off(self, ctx: RuleContext, is_last_trading_day: bool) -> bool:  # pragma: no cover
        raise NotImplementedError
