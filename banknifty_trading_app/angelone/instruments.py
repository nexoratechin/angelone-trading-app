"""Instrument (scrip) master handling.

We never hard-code symbol tokens. The Angel One instrument master is downloaded
once and cached to disk, then used to resolve:

  * the Bank Nifty **Spot index** token (``NIFTY BANK``), and
  * the **near-month / next-month BANKNIFTY futures** contract.

The master file is large; the cache means we only pay for it once per day.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from dataclasses import dataclass
from pathlib import Path

from ..logging import get_logger

log = get_logger("angelone.instruments")

_EXPIRY_FORMATS = ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d", "%d%b%y", "%d-%m-%Y")


@dataclass(frozen=True)
class Instrument:
    token: str
    symbol: str          # tradingsymbol
    name: str
    exchange: str        # exch_seg
    instrument_type: str
    lot_size: int
    tick_size: float
    expiry: _dt.date | None = None


def _parse_expiry(value: str | None) -> _dt.date | None:
    if not value:
        return None
    value = str(value).strip()
    for fmt in _EXPIRY_FORMATS:
        try:
            return _dt.datetime.strptime(value.upper(), fmt).date()
        except ValueError:
            continue
    log.warning("Unparseable expiry %r in instrument master", value)
    return None


class InstrumentMaster:
    def __init__(self, cache_path: str | Path, url: str, max_age_hours: int = 12) -> None:
        self.cache_path = Path(cache_path)
        self.url = url
        self.max_age_hours = max_age_hours
        self._records: list[dict] | None = None

    # ------------------------------------------------------------- loading
    def _cache_fresh(self) -> bool:
        if not self.cache_path.exists():
            return False
        age_s = time.time() - self.cache_path.stat().st_mtime
        return age_s < self.max_age_hours * 3600

    def load(self, force: bool = False) -> list[dict]:
        if self._records is not None and not force:
            return self._records
        if not force and self._cache_fresh():
            try:
                self._records = json.loads(self.cache_path.read_text(encoding="utf-8"))
                log.info("Loaded %d instruments from cache", len(self._records))
                return self._records
            except Exception as exc:  # pragma: no cover - corrupt cache
                log.warning("Instrument cache unreadable (%s); re-downloading", exc)

        import requests  # local import keeps import-time light

        log.info("Downloading instrument master from %s", self.url)
        resp = requests.get(self.url, timeout=60)
        resp.raise_for_status()
        records = resp.json()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(records), encoding="utf-8")
        self._records = records
        log.info("Cached %d instruments to %s", len(records), self.cache_path)
        return records

    # ------------------------------------------------------------ resolving
    def resolve_spot(self, symbol: str = "NIFTY BANK", exchange: str = "NSE") -> Instrument:
        records = self.load()
        wanted = symbol.replace(" ", "").upper()
        candidates: list[dict] = []
        for rec in records:
            name = str(rec.get("name", "")).replace(" ", "").upper()
            sym = str(rec.get("symbol", "")).replace(" ", "").upper()
            itype = str(rec.get("instrumenttype", "")).upper()
            if name == wanted or sym == wanted:
                if "FUT" in itype or "OPT" in itype:
                    continue
                candidates.append(rec)
        # prefer an index record on the requested exchange
        for rec in candidates:
            if str(rec.get("exch_seg", "")).upper() == exchange.upper():
                return self._to_instrument(rec)
        if candidates:
            return self._to_instrument(candidates[0])
        raise LookupError(
            f"Could not resolve spot instrument {symbol!r}. "
            "Check the master file / symbol name."
        )

    def resolve_futures(
        self, name: str = "BANKNIFTY", exchange: str = "NFO", contract: str = "near_month"
    ) -> Instrument:
        found = self.list_futures(name, exchange, include_expired=False)
        if not found:
            raise LookupError(f"No live {name} futures found on {exchange}.")
        if contract == "next_month" and len(found) > 1:
            return found[1]
        return found[0]

    def list_futures(
        self, name: str = "BANKNIFTY", exchange: str = "NFO", include_expired: bool = False
    ) -> list[Instrument]:
        records = self.load()
        today = _dt.date.today()
        found: list[Instrument] = []
        for rec in records:
            if str(rec.get("exch_seg", "")).upper() != exchange.upper():
                continue
            if str(rec.get("instrumenttype", "")).upper() != "FUTIDX":
                continue
            if str(rec.get("name", "")).upper() != name.upper():
                continue
            expiry = _parse_expiry(rec.get("expiry"))
            if expiry is None:
                continue
            if not include_expired and expiry < today:
                continue
            found.append(self._to_instrument(rec))
        found.sort(key=lambda i: i.expiry or _dt.date.max)  # type: ignore[arg-type]
        return found

    @staticmethod
    def _to_instrument(rec: dict) -> Instrument:
        return Instrument(
            token=str(rec.get("token", "")),
            symbol=str(rec.get("symbol", "")),
            name=str(rec.get("name", "")),
            exchange=str(rec.get("exch_seg", "")),
            instrument_type=str(rec.get("instrumenttype", "")),
            lot_size=int(float(rec.get("lotsize", 0) or 0)),
            tick_size=float(rec.get("tick_size", 0.05) or 0.05),
            expiry=_parse_expiry(rec.get("expiry")),
        )
