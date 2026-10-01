"""Crash recovery.

After a restart we need to know whether the process died holding a position.
We read still-open legs from the database and expose them so the runner can
either adopt them (rebuild strategy state) or flatten them safely. We never
silently forget an open position.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import Side, Trade
from ..database.models import TradeRow
from ..logging import get_logger
from ..positions.ledger import TradeLedger

log = get_logger("execution.recovery")


@dataclass
class RecoveryReport:
    open_trades: list[Trade]
    recovered_lots: int
    recovered_side: Side | None
    cycle_id: str = ""


def rows_to_trades(rows: list[TradeRow]) -> list[Trade]:
    trades: list[Trade] = []
    for row in rows:
        trades.append(
            Trade(
                trade_id=row.trade_id,
                cycle_id=row.cycle_id,
                side=Side(row.side),
                lots=row.lots,
                lot_size=row.lot_size,
                entry_time=row.entry_time,
                entry_price=row.entry_price,
                entry_reason=row.entry_reason,
                stop_level=row.stop_level,
                strategy_version=row.strategy_version,
                is_reversal=row.is_reversal,
                status="OPEN",
            )
        )
    return trades


def recover(repo) -> RecoveryReport:
    rows = repo.open_trades()
    trades = rows_to_trades(rows)
    lots = sum(t.lots for t in trades)
    sides = {t.side for t in trades}
    side = sides.pop() if len(sides) == 1 else None
    cycle_id = trades[0].cycle_id if trades else ""

    if trades:
        log.warning(
            "Recovered %d open lots (%s) from DB - reconcile with broker before trading",
            lots,
            side.value if side else "mixed",
        )
    else:
        log.info("No open positions recovered from DB")
    return RecoveryReport(trades, lots, side, cycle_id)
