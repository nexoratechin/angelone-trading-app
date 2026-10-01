# Bank Nifty Futures Automated Trading App (Angel One SmartAPI)

An event-driven Python application that trades **Bank Nifty near-month futures**
against **Bank Nifty Spot** signals, using the exact rule set from the original
strategy notes. The strategy is described by **versioned YAML specs** and
executed by a single engine shared by **live trading and backtesting**, so the
two cannot drift apart.

> ⚠️ **Not financial advice. This is an execution framework.** It will place real
> orders in live mode. Test in paper mode and on a backtest first. There is
> **no guarantee of millisecond execution** - broker, network and exchange
> latency dominate. Use at your own risk.

---

## Pipeline

```
MARKET DATA ──> STRATEGY / RULE ENGINE ──> SIGNAL ──> RISK MANAGER ──> ORDER MANAGER ──> ANGEL ONE
   (feed)            (rules YAML)          (intent)     (limits)         (fills)          (REST)
                                          └────────────> positions/ledger ──> SQLite (batched)
```

* **Hot path** does in-memory math only: no DB reads/writes, no Excel, no blocking I/O.
* Ticks arrive on a WebSocket thread and are handed to the asyncio loop; SQLite
  writes happen on a separate batched writer thread.
* **Paper mode is the default.** Live requires `MODE=live` **and** `LIVE_TRADING=true`.

---

## Quick start

```bash
python -m venv .venv
# Windows:  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
# macOS/Linux: source .venv/bin/activate && pip install -r requirements.txt

copy .env.example .env      # then fill in your Angel One credentials
```

`.env` (never commit it):

```
MODE=paper
LIVE_TRADING=false
ANGEL_API_KEY=...
ANGEL_CLIENT_CODE=...
ANGEL_PIN=...
ANGEL_TOTP_SECRET=...
```

Angel One prerequisites: an app registered on the SmartAPI portal (API key), the
account PIN, and a TOTP secret. You may need to whitelist your static IP.

### Commands

```bash
python -m banknifty_trading_app.main validate          # show resolved strategy + config
python -m banknifty_trading_app.main web               # FULL read-write web console
python -m banknifty_trading_app.main setup             # write credentials to .env (no echo)
python -m banknifty_trading_app.main check             # connectivity self-test (login, LTP, WebSocket - no orders)
python -m banknifty_trading_app.main run               # paper (default) / live per .env
python -m banknifty_trading_app.main demo              # offline: synthetic data through the LIVE pipeline
python -m banknifty_trading_app.main backtest --synthetic
python -m banknifty_trading_app.main backtest --start 2025-09-01 --end 2026-09-01
python -m banknifty_trading_app.main report            # Excel from the live database
python -m banknifty_trading_app.main kill "reason"     # trip the kill switch
python -m banknifty_trading_app.main unkill
```

`backtest --synthetic` needs **no broker credentials** and produces a full
one-year Excel report from generated data. Historical mode uses Angel One
candles (Spot for signals, a stitched near-month futures series for fills).

`demo` also needs no credentials: it feeds synthetic data through the **live**
pipeline (engine → risk → order manager → paper executor → SQLite → Excel), so
you can watch the real execution components work and get a populated database:

```bash
python -m banknifty_trading_app.main demo --start 2025-09-01 --end 2026-03-01
python -m banknifty_trading_app.main report        # regenerate Excel from the DB
```

## Web console (read + write)

```bash
python -m banknifty_trading_app.main web           # http://127.0.0.1:8080
```

A full control panel, not a read-only view:

* **Dashboard** - live state over WebSocket (position, P&L, stop, levels,
  Spot/Futures LTP, trades today).
* **Start / stop sessions** - `demo` (offline), `paper`, or `live`. Live
  requires the confirmation checkbox *and* `confirm_live=true` on the API.
* **Strategy** - list, create, edit and validate YAML versions in the browser,
  and set the active version (validated against the rule registry before save).
* **Settings** - edit non-secret settings; **credentials** form writes
  `ANGEL_*` into `.env`.
* **Backtest** - run synthetic/historical backtests and download the Excel
  reports.
* **Emergency** - trip/reset the kill switch.

Security: binds to `127.0.0.1` by default. If you expose it, set `WEB_TOKEN`
and every `/api` call plus the WebSocket then requires the token
(`X-Auth-Token` header / `?token=` on the socket).

---

## Strategy rules (as implemented)

| # | Rule | Implementation |
|---|------|----------------|
| 1 | Record recent daily Spot closes | Daily closes fetched at start-up; used for levels + SMA |
| 2 | 20 SMA filter | Daily Spot closes, fixed at the prior close (no look-ahead) |
| 3 | BUY only above SMA / SELL only below | `SmaFilter` |
| 4 | Spot crosses **day-before-yesterday** close → BUY 2 lots | `ReferenceCrossEntry`, `reference_offset_days: 2` |
| 5 | Spot crosses below it → SELL 2 lots | same rule, opposite side |
| 6 | Only one initial trade per cycle | `initial_trade_done` guard, `one_trade_scope: cycle` |
| 7 | Trade day: yesterday's close = support/resistance | `DailyCloseStop.initial_stop` |
| 8 | From next day: previous day's close, **ratcheting only** | `DailyCloseStop.daily_stop(ratchet=True)` |
| 9 | +1.7% favourable → square off 1 lot, carry 1, trail | `PercentPartialTarget` (stop → breakeven) |
| 10 | Stop hit → exit and reverse 2 lots | `_stop_out` + reversal entry |
| 11 | Continue same rules after reversal | reversal uses the same rule objects |
| 12 | "2% move" trailing rule | **configurable placeholder** - no invented formula |
| 13 | Last trading day → square off, no carry | `AlwaysSquareOff` + scheduled fallback |
| 14 | Record everything | SQLite (`strategy_events`, `orders`, `fills`, `trades`) + trade log |
| 15 | One-year backtest → Excel | `backtest` command + `reports/excel.py` |

All levels/triggers are read from **Spot**; only order placement targets the
**futures** contract.

### Known ambiguities (handled, not hidden)

* **The "2% move" trailing rule is undefined.** It is exposed as
  `trailing.type: percent_move_placeholder` with a `mode`
  (`disabled` | `move_stop_to_entry` | `trail_percent` | `exit_remaining`).
  `custom` refuses to load until you implement it. Default is `disabled`.
* **Reversal stop churn.** A reversal entered exactly at yesterday's close would
  have its stop *on the entry line*. Two safeguards are configurable:
  `reversal.cooldown_s` (default 60s) and `reversal.max_reversals_per_day`
  (default 3). You can also set `reversal.stop_mode: next_day` or add
  `reversal.buffer_points`.
* **Stop on the trigger day.** `stops.protective_only: true` uses yesterday's
  close only when it is genuinely protective of the entry (below a long, above a
  short); otherwise it falls back to the entry price. Set `false` for the literal
  reading.

---

## Changing the strategy - no rewrite required

Everything below is a **YAML edit** in `strategy_versions/`:

* 20 → 30 SMA: `filters[0].period: 30`
* 1.7% → 2%: `partial.percent: 2.0`
* 2 → 3 lots: `instrument.lots: 3`
* change stop/trailing formulas, add filters, time windows, min-distance entries
* reversal on/off, lots, stop mode, buffers, caps
* expiry behaviour

### Strategy versioning

Add a new file `strategy_versions/v2_myrules.yaml` (copy `v2_example.yaml`), then
set `ACTIVE_STRATEGY=v2_myrules` in `.env`. Nothing else changes - the Angel One
connection, market data, risk, execution, database, reporting and dashboard are
untouched.

### Adding a brand-new rule

1. Subclass the right interface in `rules/` (e.g. `Filter`, `EntryRule`,
   `StopRule`, `TrailingRule`, `ExpiryRule`).
2. Register it: `@register_filter("my_filter")`.
3. Reference it from YAML: `- type: my_filter`.

`rules/registry.py` maps names to classes; the engine only knows the interfaces.

### Live / backtest parity

Both `runtime.py` (live) and `backtest/replay.py` drive the **same**
`strategy/engine.py` with the **same** rule objects. Only the data adapter and
the fill mechanism differ. `tests/test_backtest.py` asserts determinism.

---

## Reliability & safety features

Paper by default · explicit live opt-in · duplicate-order protection (intent ids
**and** broker-side order tags) · order-status confirmation with timeout ·
WebSocket auto-reconnect with exponential backoff · API error handling ·
crash recovery + position reconciliation against the broker at start-up · stale
market-data detection · emergency kill switch (file or `kill` command) ·
maximum-lot protection · optional daily-loss and max-trades limits · full logging
(app + dedicated trade log) · batched SQLite persistence.

---

## Project layout

```
banknifty_trading_app/
  main.py          runtime.py        config.py
  core/            events, bus, models, state
  angelone/        auth, rest, websocket, instruments, constants
  market_data/     feed, store, daily_closes, staleness
  strategy/        engine, indicators
  rules/           base, registry, filters, entries, stops, targets, trailing, expiry, spec
  risk/            manager, kill_switch, limits
  execution/       order_manager, router, paper, live, reconciler, recovery
  positions/       book, ledger, pnl
  backtest/        replay, run, data_loader, costs, synthetic
  database/        models (SQL DDL), session, writer, repository
  reports/         excel
  dashboard/       app (read-only FastAPI)
  web/             controller, api (full read-write console), static/ (UI)
  logging/         setup
  tests/
strategy_versions/ v1_baseline.yaml, v2_example.yaml
```

---

## Notes & caveats

* The Angel One layer wraps only official `smartapi-python`
  (`SmartConnect`, `SmartWebSocketV2`). Endpoints/tokens are resolved from the
  published instrument master - **no tokens are hard-coded**. The wrapper is
  **verified line-by-line against the official SDK source**
  (`generateSession`'s nested token shape, `ltpData` / `orderBook` / `position`,
  `placeOrder(dict)` / `placeOrderFullResponse`, and the WebSocket's
  already-parsed paise payload). Run `main check` on your machine to confirm the
  live login + WebSocket before trading.
* Bank Nifty futures **lot size is configurable** (`instrument.lot_size`),
  defaulted to 30. Confirm the current NSE value before going live.
* Historical backtests need broker credentials and are subject to the historical
  API's look-back limits; fetched chunks are cached under `data/history/`.
* Backtest cost/charge rates in `backtest/costs.py` are **defaults to tune**.
* Intra-bar ordering (stop vs target in the same bar) is configurable via
  `BACKTEST_INTRABAR=conservative|optimistic`.

## Tests

```bash
python -m pytest -q banknifty_trading_app/tests
```

Covers rules, engine scenarios (entry, partial, reversal, expiry, one-trade,
protective stop, reversal cap), level computation, backtest determinism,
report generation and persistence round-trip.
