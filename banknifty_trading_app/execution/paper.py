"""Paper (dry-run) executor: fills at the latest tick plus configurable slippage.

This is the default mode. It exercises the *entire* live path - strategy, risk,
order manager - and only swaps the final broker call.
"""

from __future__ import annotations

import datetime as _dt

from ..config import Settings
from ..core.models import Fill, OrderRequest, Side, TradeIntent
from ..logging import get_logger, get_trade_logger
from ..market_data.store import MarketDataStore

log = get_logger("execution.paper")
tradelog = get_trade_logger()


class PaperExecutor:
    def __init__(self, settings: Settings, store: MarketDataStore) -> None:
        self.settings = settings
        self.store = store

    async def execute(self, req: OrderRequest, intent: TradeIntent) -> Fill | None:
        ref = self.store.ltp(req.token)
        if ref is None:
            ref = req.price if req.price is not None else intent.futures_price
        slip = self.settings.paper_slippage_points
        price = ref + slip if req.side is Side.BUY else ref - slip
        price = round(price / self.settings.tick_size) * self.settings.tick_size

        fill = Fill(
            order_id=f"paper_{req.order_id}",
            intent_id=req.intent_id,
            symbol=req.symbol,
            side=req.side,
            quantity=req.quantity,
            price=price,
            timestamp=_dt.datetime.now(),
            reason=req.reason,
            strategy_version=intent.strategy_version,
            meta=dict(intent.meta),
        )
        tradelog.info(
            "PAPER FILL %s %s x%d @ %.2f (%s)",
            fill.side.value,
            fill.symbol,
            fill.quantity,
            fill.price,
            fill.reason,
        )
        return fill
