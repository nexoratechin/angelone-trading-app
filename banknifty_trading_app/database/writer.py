"""Batched, threaded persistence writer (sqlite3).

Producers call :meth:`submit` (a non-blocking queue put); a single background
thread coalesces rows and writes them with ``executemany``. Disk I/O never
touches the tick-to-order path.
"""

from __future__ import annotations

import json
import queue
import threading
from datetime import datetime
from typing import Any

from ..logging import get_logger
from .session import Database

log = get_logger("database.writer")

_TERMINAL = object()


def _serialise(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    return value


class PersistenceWriter:
    def __init__(
        self,
        database: Database,
        flush_interval_s: float = 0.5,
        max_batch: int = 500,
    ) -> None:
        self.database = database
        self.flush_interval_s = flush_interval_s
        self.max_batch = max_batch
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.database.init_schema()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="db-writer", daemon=True)
        self._thread.start()

    def stop(self, drain: bool = True) -> None:
        self._stop.set()
        if drain:
            self._q.put(_TERMINAL)
        if self._thread:
            self._thread.join(timeout=10)

    def submit(self, table: str, payload: dict[str, Any]) -> None:
        self._q.put((table, payload))

    # ------------------------------------------------------------------ thread
    def _run(self) -> None:
        while not self._stop.is_set():
            batch: list[tuple[str, dict]] = []
            try:
                item = self._q.get(timeout=self.flush_interval_s)
            except queue.Empty:
                continue
            if item is _TERMINAL:
                break
            batch.append(item)
            while len(batch) < self.max_batch:
                try:
                    item = self._q.get_nowait()
                except queue.Empty:
                    break
                if item is _TERMINAL:
                    self._flush(batch)
                    self._drain_rest()
                    return
                batch.append(item)
            self._flush(batch)
        self._drain_rest()

    def _drain_rest(self) -> None:
        leftover: list[tuple[str, dict]] = []
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                break
            if item is not _TERMINAL:
                leftover.append(item)
        if leftover:
            self._flush(leftover)

    def _flush(self, batch: list[tuple[str, dict]]) -> None:
        if not batch:
            return
        grouped: dict[str, list[dict]] = {}
        for table, payload in batch:
            grouped.setdefault(table, []).append(payload)
        try:
            conn = self.database.connect()
            try:
                for table, rows in grouped.items():
                    columns = list(rows[0].keys())
                    placeholders = ", ".join("?" for _ in columns)
                    collist = ", ".join(columns)
                    sql = f"INSERT INTO {table} ({collist}) VALUES ({placeholders})"
                    values = [tuple(_serialise(row.get(c)) for c in columns) for row in rows]
                    conn.executemany(sql, values)
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # pragma: no cover - durability fault
            log.error("Batch DB write failed (%d rows): %s", len(batch), exc)


# -- payload builders ------------------------------------------------------
def event_payload(record) -> dict:
    return {
        "ts": record.timestamp,
        "event_type": record.event_type,
        "detail": record.detail,
        "strategy_version": record.strategy_version,
        "payload_json": json.dumps(record.payload, default=str),
    }


def order_payload(order_request, status: str = "SUBMITTED") -> dict:
    return {
        "order_id": order_request.order_id,
        "intent_id": order_request.intent_id,
        "ts": datetime.now(),
        "symbol": order_request.symbol,
        "side": order_request.side.value,
        "quantity": order_request.quantity,
        "price": order_request.price,
        "status": status,
        "reason": order_request.reason,
        "strategy_version": "",
    }


def fill_payload(fill) -> dict:
    return {
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "intent_id": fill.intent_id,
        "ts": fill.timestamp,
        "symbol": fill.symbol,
        "side": fill.side.value,
        "quantity": fill.quantity,
        "price": fill.price,
        "reason": fill.reason,
        "strategy_version": fill.strategy_version,
    }


def trade_payload(trade) -> dict:
    return {
        "trade_id": trade.trade_id,
        "cycle_id": trade.cycle_id,
        "side": trade.side.value if hasattr(trade.side, "value") else str(trade.side),
        "lots": trade.lots,
        "lot_size": trade.lot_size,
        "entry_time": trade.entry_time,
        "entry_price": trade.entry_price,
        "exit_time": trade.exit_time,
        "exit_price": trade.exit_price,
        "pnl_points": trade.pnl_points,
        "pnl_money": trade.pnl_money,
        "entry_reason": trade.entry_reason,
        "exit_reason": trade.exit_reason,
        "stop_level": trade.stop_level,
        "strategy_version": trade.strategy_version,
        "is_partial": trade.is_partial,
        "is_reversal": trade.is_reversal,
        "status": trade.status,
    }
