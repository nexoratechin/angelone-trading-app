"""Topic names for the internal event bus."""

from __future__ import annotations


class Topic:
    TICK = "tick"
    TRADE_INTENT = "trade_intent"          # strategy -> risk
    RISK_DECISION = "risk_decision"        # risk -> execution / audit
    ORDER_REQUEST = "order_request"        # risk -> execution
    ORDER_UPDATE = "order_update"          # execution -> observers
    FILL = "fill"                          # execution -> positions/strategy
    STRATEGY_EVENT = "strategy_event"      # strategy -> persistence/logging
    POSITION = "position"                  # positions -> dashboard
    HEALTH = "health"                      # feed/risk health signals


# Topics whose queues are allowed to drop the oldest event under back-pressure
# (only safe for *state*, never for orders/fills).
DROPPABLE_TOPICS = {Topic.TICK}
