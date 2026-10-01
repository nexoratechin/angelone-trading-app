"""Thin, defensive wrapper over the official SmartAPI REST client.

Method names and signatures below are verified against the official
``smartapi-python`` source (``SmartApi/smartConnect.py``):

    position()            orderBook()          ltpData(exchange, symbol, token)
    placeOrder(dict)      placeOrderFullResponse(dict)   getCandleData(dict)
    cancelOrder(order_id, variety)             terminateSession(clientCode)

We never invent endpoints; if a method is missing (SDK version drift) we raise
a clear error.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ..config import Settings
from ..logging import get_logger
from .auth import AngelAuth, AngelSession
from .constants import (
    DURATION_DAY,
    ORDER_STATUS_MAP,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    PRODUCT_NRML,
    TRANSACTION_BUY,
    TRANSACTION_SELL,
    VARIETY_NORMAL,
)
from .instruments import Instrument

log = get_logger("angelone.rest")


class AngelREST:
    def __init__(self, settings: Settings, auth: AngelAuth) -> None:
        self.settings = settings
        self.auth = auth

    @property
    def client(self):
        self.auth.ensure_logged_in()
        return self.auth.client

    @property
    def session(self) -> AngelSession:
        return self.auth.ensure_logged_in()

    # ------------------------------------------------------------- account
    def positions(self) -> list[dict]:
        resp = self.client.position()
        if not resp or not resp.get("status"):
            log.warning("position() unsuccessful: %s", resp)
            return []
        return resp.get("data") or []

    def order_book(self) -> list[dict]:
        resp = self.client.orderBook()
        if not resp or not resp.get("status"):
            log.warning("orderBook() unsuccessful: %s", resp)
            return []
        return resp.get("data") or []

    def profile(self) -> dict:
        return self.client.getProfile(self.session.refresh_token)

    # ------------------------------------------------------------------ ltp
    def ltp(self, exchange: str, symbol: str, token: str) -> float | None:
        resp = self.client.ltpData(exchange, symbol, token)
        if not resp or not resp.get("status"):
            return None
        return float(resp["data"]["ltp"])

    # ------------------------------------------------------------ historical
    def candles(
        self,
        exchange: str,
        token: str,
        interval: str,
        from_dt: _dt.datetime,
        to_dt: _dt.datetime,
    ) -> list[list[Any]]:
        params = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": interval,
            "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
        }
        resp = self.client.getCandleData(params)
        if not resp or not resp.get("status"):
            log.warning("getCandleData unsuccessful: %s", str(resp)[:200])
            return []
        return resp.get("data") or []

    # ---------------------------------------------------------------- orders
    def place_order(
        self,
        *,
        instrument: Instrument,
        side: str,
        quantity: int,
        order_type: str,
        product_type: str,
        price: float | None = None,
        tag: str = "",
    ) -> str:
        """Place an order; returns the broker order id (never returns None)."""
        params = {
            "variety": VARIETY_NORMAL,
            "tradingsymbol": instrument.symbol,
            "symboltoken": instrument.token,
            "transactiontype": TRANSACTION_BUY if side.upper() == "BUY" else TRANSACTION_SELL,
            "exchange": instrument.exchange,
            "ordertype": ORDER_TYPE_MARKET if order_type.upper() == "MARKET" else ORDER_TYPE_LIMIT,
            "producttype": product_type or PRODUCT_NRML,
            "duration": DURATION_DAY,
            "price": str(round(price, 2)) if price is not None else "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(int(quantity)),
        }
        log.info("Placing order %s", {k: v for k, v in params.items()})

        # Prefer the full-response variant (returns {"data": {"orderid": ...}}).
        if hasattr(self.client, "placeOrderFullResponse"):
            resp = self.client.placeOrderFullResponse(params)
            if resp and resp.get("status"):
                order_id = str((resp.get("data") or {}).get("orderid") or "")
                if order_id:
                    return order_id
                raise RuntimeError(f"placeOrder returned no order id: {resp}")
            raise RuntimeError(f"Order rejected: {(resp or {}).get('message') or resp}")

        # Fallback: plain placeOrder returns the order id string (or None).
        order_id = self.client.placeOrder(params)
        if not order_id:
            raise RuntimeError("placeOrder returned no order id")
        return str(order_id)

    def cancel_order(self, order_id: str, variety: str = VARIETY_NORMAL) -> dict:
        return self.client.cancelOrder(order_id, variety)

    # --------------------------------------------------------------- helpers
    @staticmethod
    def normalise_status(raw: str) -> str:
        return ORDER_STATUS_MAP.get((raw or "").strip().lower(), "UNKNOWN")
