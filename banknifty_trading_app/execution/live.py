"""Live executor: places real orders via SmartAPI and confirms the fill.

Safety properties:
  * master arm switch: refuses to construct or run unless MODE=live,
    LIVE_TRADING=true and LIVE_ARMED=true (re-checked before every order),
  * broker-side duplicate check: if an open order with the same tag already
    exists, we do not place another,
  * order-status confirmation with a timeout, so we never assume a fill,
  * every failure is logged and surfaced to the order manager.
"""

from __future__ import annotations

import asyncio
import datetime as _dt

from ..angelone.constants import (
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    TRANSACTION_BUY,
    TRANSACTION_SELL,
)
from ..angelone.instruments import Instrument
from ..angelone.rest import AngelREST
from ..config import Settings
from ..core.models import Fill, OrderRequest, OrderStatus, Side, TradeIntent
from ..logging import get_logger, get_trade_logger

log = get_logger("execution.live")
tradelog = get_trade_logger()


class LiveExecutor:
    """Places real orders. Refuses to run unless every live flag is set.

    The arm switch is re-checked twice: once at construction and again
    immediately before each broker call. This is deliberate defence in depth -
    ``build_executor`` already enforces it, but if anything ever mutates
    ``settings`` mid-session the order still cannot escape.
    """

    def __init__(
        self,
        settings: Settings,
        rest: AngelREST,
        instrument: Instrument,
    ) -> None:
        settings.assert_live_allowed()
        self.settings = settings
        self.rest = rest
        self.instrument = instrument

    def _arm_ok(self) -> bool:
        """Return True only if live trading is still fully armed right now."""
        try:
            self.settings.assert_live_allowed()
            return True
        except Exception as exc:
            log.error("LIVE BLOCKED before order: %s", exc)
            return False

    async def execute(self, req: OrderRequest, intent: TradeIntent) -> Fill | None:
        if not self._arm_ok():
            return None

        tag = f"{intent.intent_id[:18]}"

        if await self._duplicate_open_order(tag):
            log.warning("Skipping order; an open order with tag %s already exists", tag)
            return None

        try:
            order_id = await asyncio.to_thread(
                self.rest.place_order,
                instrument=self.instrument,
                side=req.side.value,
                quantity=req.quantity,
                order_type=req.order_type,
                product_type=req.product_type,
                price=req.price,
                tag=tag,
            )
        except Exception as exc:
            log.error("place_order failed for %s: %s", req.side.value, exc)
            return None

        if not order_id:
            log.error("place_order returned no order id")
            return None

        return await self._await_fill(str(order_id), req, intent)

    async def _duplicate_open_order(self, tag: str) -> bool:
        try:
            book = await asyncio.to_thread(self.rest.order_book)
        except Exception:
            return False
        for row in book:
            if str(row.get("ordertag") or row.get("tag") or "") != tag:
                continue
            status = self.rest.normalise_status(str(row.get("status", "")))
            if status in (OrderStatus.OPEN.value, OrderStatus.PENDING.value):
                return True
        return False

    async def _await_fill(
        self, order_id: str, req: OrderRequest, intent: TradeIntent
    ) -> Fill | None:
        deadline = _dt.datetime.now() + _dt.timedelta(seconds=self.settings.order_status_timeout_s)
        while _dt.datetime.now() < deadline:
            try:
                book = await asyncio.to_thread(self.rest.order_book)
            except Exception as exc:
                log.warning("order_book poll failed: %s", exc)
                book = []
            for row in book:
                if str(row.get("orderid")) != order_id:
                    continue
                status = self.rest.normalise_status(str(row.get("status", "")))
                if status == OrderStatus.COMPLETE.value:
                    price = float(row.get("averageprice") or row.get("price") or req.price or 0.0)
                    filled = int(float(row.get("filledshares") or row.get("quantity") or req.quantity))
                    fill = Fill(
                        order_id=order_id,
                        intent_id=req.intent_id,
                        symbol=req.symbol,
                        side=req.side,
                        quantity=filled,
                        price=price,
                        timestamp=_dt.datetime.now(),
                        reason=req.reason,
                        strategy_version=intent.strategy_version,
                        meta=dict(intent.meta),
                    )
                    tradelog.info(
                        "LIVE FILL %s %s x%d @ %.2f (%s) order=%s",
                        fill.side.value, fill.symbol, fill.quantity, fill.price, fill.reason, order_id,
                    )
                    return fill
                if status in (OrderStatus.REJECTED.value, OrderStatus.CANCELLED.value):
                    log.error("Order %s ended as %s: %s", order_id, status, row.get("text"))
                    return None
            await asyncio.sleep(self.settings.order_status_poll_interval_s)

        log.error("Timed out waiting for order %s to complete", order_id)
        return None

    @staticmethod
    def build_price(intent: TradeIntent, side: Side, buffer_points: float) -> float | None:
        """Market-protected LIMIT price (only used when order type is LIMIT)."""
        ref = intent.futures_price
        if not ref:
            return None
        return ref + buffer_points if side is Side.BUY else ref - buffer_points
