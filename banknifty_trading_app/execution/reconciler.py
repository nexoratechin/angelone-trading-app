"""Position/order reconciliation against the broker.

Runs at start-up (and on demand). If the local view disagrees with the broker,
we log loudly and can optionally adopt the broker's view - never trade blind.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import Side
from ..logging import get_logger

log = get_logger("execution.reconciler")


@dataclass
class ReconcileResult:
    matched: bool
    local_side: Side | None
    local_lots: int
    broker_side: Side | None
    broker_lots: int
    message: str = ""


def net_position_from_broker(rows: list[dict], symbol: str) -> tuple[Side | None, int]:
    net = 0
    for row in rows:
        if str(row.get("tradingsymbol", "")).upper() != symbol.upper():
            continue
        netbuy = int(float(row.get("netbuyqty") or row.get("buyqty") or 0))
        netsell = int(float(row.get("netsellqty") or row.get("sellqty") or 0))
        net += netbuy - netsell
    if net > 0:
        return Side.BUY, net
    if net < 0:
        return Side.SELL, abs(net)
    return None, 0


def reconcile(
    local_side: Side | None,
    local_units: int,
    broker_rows: list[dict],
    symbol: str,
) -> ReconcileResult:
    broker_side, broker_lots_units = net_position_from_broker(broker_rows, symbol)
    matched = (local_side == broker_side) and (local_units == broker_lots_units)
    msg = (
        "reconciled OK"
        if matched
        else (
            f"MISMATCH local={local_side}:{local_units} "
            f"broker={broker_side}:{broker_lots_units}"
        )
    )
    if matched:
        log.info("Position %s", msg)
    else:
        log.error("Position %s", msg)
    return ReconcileResult(matched, local_side, local_units, broker_side, broker_lots_units, msg)
