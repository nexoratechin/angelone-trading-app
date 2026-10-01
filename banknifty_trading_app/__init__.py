"""Bank Nifty futures automated trading application (Angel One SmartAPI).

Architecture (event-driven, one-way data flow):

    MARKET DATA -> STRATEGY / RULE ENGINE -> SIGNAL -> RISK MANAGER
                -> ORDER MANAGER -> ANGEL ONE

The strategy is described by versioned YAML specs in ``strategy_versions/``
and executed by a single engine shared by live trading and backtesting, so the
two can never drift apart.
"""

__version__ = "0.1.0"
