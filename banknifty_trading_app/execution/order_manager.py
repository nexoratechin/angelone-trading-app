"""Order manager: turns approved intents into broker orders.

Responsibilities:
  * translate a :class:`TradeIntent` into an :class:`OrderRequest`,
  * decide the transaction side (entries use the intent side; exits close it),
  * hand off to the active executor (paper or live),
  * emit the resulting fill.

Duplicate protection lives in the risk layer (intent ids) and in the live
executor (broker-side tag check), so this class stays simple.
"""

from __future__ import annotations

from collections.abc import Callable

from ..angelone.instruments import Instrument
from ..config import Settings
from ..core.models import Fill, IntentKind, OrderRequest, Side, TradeIntent
from ..logging import get_logger

log = get_logger("execution.order_manager")


class OrderManager:
    def __init__(
        self,
        settings: Settings,
        executor,
        instrument: Instrument,
        lot_size: int,
        product_type: str,
        on_fill: Callable[[Fill], None] | None = None,
        on_order: Callable[..., None] | None = None,
    ) -> None:
        self.settings = settings
        self.executor = executor
        self.instrument = instrument
        self.lot_size = lot_size
        self.product_type = product_type
        self.on_fill = on_fill or (lambda _f: None)
        self.on_order = on_order or (lambda _req, _status: None)

    def _build_request(self, intent: TradeIntent) -> OrderRequest:
        closing = intent.kind in (IntentKind.EXIT, IntentKind.PARTIAL_EXIT)
        side = intent.side.opposite if closing else intent.side
        price: float | None = None
        if self.settings.order_type == "LIMIT":
            ref = intent.futures_price or 0.0
            buf = self.settings.market_protect_buffer_points
            price = ref + buf if side is Side.BUY else ref - buf
        return OrderRequest(
            symbol=self.instrument.symbol,
            token=self.instrument.token,
            side=side,
            quantity=intent.lots * self.lot_size,
            order_type=self.settings.order_type,
            product_type=self.product_type,
            price=price,
            intent_id=intent.intent_id,
            reason=intent.reason,
        )

    async def handle_intent(self, intent: TradeIntent) -> Fill | None:
        req = self._build_request(intent)
        fill = await self.executor.execute(req, intent)
        self.on_order(req, "COMPLETE" if fill is not None else "REJECTED")
        if fill is not None:
            self.on_fill(fill)
        else:
            log.error(
                "No fill produced for intent %s kind=%s side=%s reason=%s",
                intent.intent_id, intent.kind.value, intent.side.value, intent.reason,
            )
        return fill
