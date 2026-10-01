"""Backtest cost model.

All rates are deliberately configurable: Indian F&O charges change and vary by
broker, so treat these as sensible defaults to be tuned, not gospel.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import Side


@dataclass
class CostModel:
    slippage_points: float = 0.5
    brokerage_per_order: float = 20.0
    stt_pct: float = 0.0125        # securities transaction tax (sell side)
    exchange_txn_pct: float = 0.0019
    sebi_pct: float = 0.0001
    stamp_pct: float = 0.002       # stamp duty (buy side)
    gst_pct: float = 18.0          # on brokerage + exchange + sebi

    def fill_price(self, price: float, side: Side) -> float:
        """Apply adverse slippage to a decision price."""
        return price + self.slippage_points if side is Side.BUY else price - self.slippage_points

    def charges(self, price: float, quantity: int, side: Side) -> float:
        turnover = abs(price) * quantity
        brokerage = self.brokerage_per_order
        exchange = turnover * self.exchange_txn_pct / 100.0
        sebi = turnover * self.sebi_pct / 100.0
        gst = (brokerage + exchange + sebi) * self.gst_pct / 100.0
        stt = turnover * self.stt_pct / 100.0 if side is Side.SELL else 0.0
        stamp = turnover * self.stamp_pct / 100.0 if side is Side.BUY else 0.0
        return brokerage + exchange + sebi + gst + stt + stamp
