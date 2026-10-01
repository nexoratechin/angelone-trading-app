"""Logging setup: console + rotating file logs, plus a dedicated trade log.

The trade log is a separate file so executions and strategy events are easy to
audit without noise from transport/debug logging.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_TRADE_LOGGER_NAME = "banknifty.trade"
_configured = False


def setup_logging(log_dir: str | Path, level: int = logging.INFO) -> None:
    global _configured
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_dir = Path(log_dir)

    root = logging.getLogger()
    if not _configured:
        root.setLevel(level)
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

        app_file = logging.handlers.RotatingFileHandler(
            log_dir / "app.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8"
        )
        app_file.setFormatter(fmt)
        root.addHandler(app_file)

        # trade/audit logger - independent file, always INFO+
        trade = logging.getLogger(_TRADE_LOGGER_NAME)
        trade.setLevel(logging.INFO)
        trade.propagate = False
        trade_file = logging.handlers.RotatingFileHandler(
            log_dir / "trades.log", maxBytes=10_000_000, backupCount=10, encoding="utf-8"
        )
        trade_file.setFormatter(fmt)
        trade.addHandler(trade_file)

        _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"banknifty.{name}" if not name.startswith("banknifty") else name)


def get_trade_logger() -> logging.Logger:
    return logging.getLogger(_TRADE_LOGGER_NAME)
