"""P&L helpers (points vs currency)."""

from __future__ import annotations


def points_to_money(points: float, lots: float, lot_size: int) -> float:
    return points * lots * lot_size


def money_to_points(money: float, lots: float, lot_size: int) -> float:
    denom = lots * lot_size
    return money / denom if denom else 0.0
