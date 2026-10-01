"""Strategy specification: a versioned, human-editable description of rules.

A spec is loaded from ``strategy_versions/<version>.yaml``. Everything the
engine needs comes from here, so switching strategies is a config change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import (  # noqa: F401  (imports trigger rule registration)
    entries,
    expiry,
    filters,
    stops,
    targets,
    trailing,
)
from .base import EntryRule, ExpiryRule, Filter, PartialRule, StopRule, TrailingRule
from .registry import (
    ENTRIES,
    EXPIRIES,
    FILTERS,
    PARTIALS,
    STOPS,
    TRAILINGS,
    build,
)


@dataclass
class InstrumentSpec:
    symbol: str = "BANKNIFTY"
    exchange: str = "NFO"
    spot_symbol: str = "NIFTY BANK"
    spot_exchange: str = "NSE"
    lots: int = 2
    lot_size: int = 30
    product_type: str = "NRML"
    contract: str = "near_month"  # near_month | next_month


@dataclass
class StrategySpec:
    version: str
    description: str
    instrument: InstrumentSpec
    reference_offset_days: int
    sma_period: int
    require_fresh_cross: bool
    gap_open_counts: bool
    one_trade_scope: str
    filters: list[Filter]
    entry: EntryRule
    stop: StopRule
    partial: PartialRule
    trailing: TrailingRule | None
    expiry: ExpiryRule
    reversal_enabled: bool
    reversal_lots: int
    reversal_stop_mode: str
    reversal_buffer_points: float
    reversal_cooldown_s: float
    max_reversals_per_day: int | None
    expiry_squareoff_time: str | None
    source_path: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _instrument(cfg: dict) -> InstrumentSpec:
    cfg = cfg or {}
    return InstrumentSpec(
        symbol=cfg.get("symbol", "BANKNIFTY"),
        exchange=cfg.get("exchange", "NFO"),
        spot_symbol=cfg.get("spot_symbol", "NIFTY BANK"),
        spot_exchange=cfg.get("spot_exchange", "NSE"),
        lots=int(cfg.get("lots", 2)),
        lot_size=int(cfg.get("lot_size", 30)),
        product_type=cfg.get("product_type", "NRML"),
        contract=cfg.get("contract", "near_month"),
    )


def _sma_period(filter_cfgs: list[dict], default: int = 20) -> int:
    for cfg in filter_cfgs:
        if cfg.get("type") == "sma":
            return int(cfg.get("period", default))
    return default


def load_spec(path: str | Path) -> StrategySpec:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"strategy spec not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    strategy_cfg = raw.get("strategy", {}) or {}
    filter_cfgs = list(raw.get("filters", []) or [])
    reversal_cfg = raw.get("reversal", {}) or {}
    expiry_cfg = dict(raw.get("expiry", {}) or {})

    squareoff_time = expiry_cfg.pop("squareoff_time", strategy_cfg.get("expiry_squareoff_time"))

    spec = StrategySpec(
        version=str(raw.get("version", path.stem)),
        description=str(raw.get("description", "")),
        instrument=_instrument(raw.get("instrument", {})),
        reference_offset_days=int(strategy_cfg.get("reference_offset_days", 2)),
        sma_period=_sma_period(filter_cfgs),
        require_fresh_cross=bool(strategy_cfg.get("require_fresh_cross", True)),
        gap_open_counts=bool(strategy_cfg.get("gap_open_counts", False)),
        one_trade_scope=str(strategy_cfg.get("one_trade_scope", "cycle")),
        filters=[build(FILTERS, cfg) for cfg in filter_cfgs],
        entry=build(ENTRIES, raw.get("entry")),
        stop=build(STOPS, raw.get("stops") or raw.get("stop")),
        partial=build(PARTIALS, raw.get("partial")),
        trailing=build(TRAILINGS, raw.get("trailing"), default_none=True),
        expiry=build(EXPIRIES, expiry_cfg),
        reversal_enabled=bool(reversal_cfg.get("enabled", True)),
        reversal_lots=int(reversal_cfg.get("lots", 2)),
        reversal_stop_mode=str(reversal_cfg.get("stop_mode", "same_rules")),
        reversal_buffer_points=float(reversal_cfg.get("buffer_points", 0.0)),
        reversal_cooldown_s=float(reversal_cfg.get("cooldown_s", 60.0)),
        max_reversals_per_day=(
            int(reversal_cfg["max_reversals_per_day"])
            if reversal_cfg.get("max_reversals_per_day") not in (None, "", "null")
            else None
        ),
        expiry_squareoff_time=squareoff_time,
        source_path=str(path),
        raw=raw,
    )
    return spec
