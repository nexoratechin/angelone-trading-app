"""Angel One SmartAPI constants.

These string values mirror the official SmartAPI contract. They are collected
here so no layer uses magic strings. Verify against the official docs when the
broker changes its API.
"""

from __future__ import annotations

# Order varieties / types / products (REST)
VARIETY_NORMAL = "NORMAL"

TRANSACTION_BUY = "BUY"
TRANSACTION_SELL = "SELL"

ORDER_TYPE_MARKET = "MARKET"
ORDER_TYPE_LIMIT = "LIMIT"

PRODUCT_NRML = "NRML"       # carry-forward (required by this strategy)
PRODUCT_MIS = "MIS"         # intraday only - NOT compatible with carry
PRODUCT_CNC = "CNC"

DURATION_DAY = "DAY"

# Exchange segments (REST)
EXCHANGE_NSE = "NSE"
EXCHANGE_NFO = "NFO"
EXCHANGE_BSE = "BSE"
EXCHANGE_BFO = "BFO"
EXCHANGE_MCX = "MCX"

# WebSocket exchangeType codes (matches SmartWebSocketV2 constants)
#   1 NSE_CM (cash + indices), 2 NSE_FO, 3 BSE_CM, 4 BSE_FO, 5 MCX_FO,
#   7 NCX_FO, 13 CDE_FO
WS_EXCHANGE_TYPE = {
    "NSE": 1,       # NSE cash / index
    "NFO": 2,       # NSE futures & options
    "BSE": 3,
    "BFO": 4,
    "MCX": 5,
    "NCDEX": 7,
    "CDE": 13,
}

# WebSocket subscription modes
WS_MODE_LTP = 1
WS_MODE_QUOTE = 2
WS_MODE_SNAPQUOTE = 3

# REST order-status strings -> our normalised OrderStatus
ORDER_STATUS_MAP = {
    "complete": "COMPLETE",
    "completed": "COMPLETE",
    "rejected": "REJECTED",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
    "open": "OPEN",
    "pending": "PENDING",
    "trigger pending": "OPEN",
    "validation pending": "PENDING",
    "put order req received": "PENDING",
    "modified": "OPEN",
    "aborted": "CANCELLED",
}
