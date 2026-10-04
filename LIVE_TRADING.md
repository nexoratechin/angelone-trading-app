# Paper first, then live

This is the operating manual for the two phases of this app:

* **Phase 1 - PAPER** (what you should do now): real Angel One prices,
  **simulated fills, zero real orders.**
* **Phase 2 - LIVE** (only when you decide): real orders, real money.

Live trading is not a checkbox here. It is behind a **master interlock** that has
to be armed deliberately from the command line, and it can be disarmed again at
any moment.

---

## 1. How the interlock works

Live trading requires **all three** of these to be true:

| Flag | Meaning | Default |
|---|---|---|
| `MODE=live` | the session is a live session | `paper` |
| `LIVE_TRADING=true` | the operator opted in | `false` |
| `LIVE_ARMED=true` | **the master arm switch** | `false` |

If any one of them is missing, `settings.is_live` is `False` and the app cannot
place a real order. The check is enforced in **three independent places**, so a
single missed check cannot let an order through:

1. `config.py` - `is_live` requires all three flags.
2. `execution/router.py` - `build_executor` is the only place a `LiveExecutor`
   is created. If live was *requested* but not armed it raises
   `LiveTradingLocked` rather than quietly falling back to paper (an operator
   who thinks they are live must never be silently getting paper fills).
3. `execution/live.py` - the arm state is re-checked at construction **and
   immediately before every single broker call**, so disarming mid-session stops
   the very next order.

The web console can **display** the arm state but **cannot change it**: the
settings form deliberately excludes `LIVE_ARMED`.

Check the state at any time:

```bash
python -m banknifty_trading_app.main live-status
```

---

## 2. Phase 1 - paper trading (start here)

Paper mode logs in to Angel One, streams real WebSocket ticks from the real
Bank Nifty spot and futures contracts, computes real daily closes and levels, runs
the real strategy/risk/order-manager pipeline - and then simulates the fill at
the last tick ± `PAPER_SLIPPAGE_POINTS` instead of calling the broker.

Nothing is sent to the exchange. Your account cannot lose money.

### What you need on your side

| # | Item | Where to get it |
|---|---|---|
| 1 | Angel One trading account | angelone.in |
| 2 | SmartAPI app + **API key** | smartapi.angelone.in → Create App |
| 3 | **Client code** (e.g. `A12345`) | your Angel One login id |
| 4 | **PIN** (4-digit) | your Angel One trading PIN |
| 5 | **TOTP secret** (base32 seed) | shown once when you enable TOTP in the SmartAPI portal - it is the *seed*, not the rotating 6 digits |
| 6 | **Static IP whitelisted** | SmartAPI app settings, if the key is IP-locked |
| 7 | A machine that stays awake 09:15-15:30 IST | Windows PC, VPS, etc. |
| 8 | Reliable internet | the WebSocket auto-reconnects, but gaps lose ticks |

### If you are on a corporate network

Two things can block you before credentials even matter. `main preflight` checks
both and names the cause:

1. **TLS inspection.** A proxy (Zscaler, Netskope, Fortinet, ...) re-signs
   HTTPS with its own root CA. That CA is in the Windows store but not in
   `certifi`, so every request fails with `CERTIFICATE_VERIFY_FAILED`. The app
   ships `truststore` and verifies against the OS store instead - verification
   stays **on**. Look for the `Network / TLS` block in preflight output; it
   prints the issuer, e.g. `Zscaler Inc.`.
2. **IP whitelisting.** `smartapi-python` hardcodes its `X-ClientPublicIP`
   header, so your whitelist would never match; the app patches it with your
   real IP before login. **But if your outbound public IP changes between
   requests** (common on multi-WAN or proxied corporate networks - we measured
   three different egress addresses on one machine), no whitelist can be
   reliable. Ask Angel One to remove the IP restriction, or run this on a VPS
   with a stable address.

  Also note: a proxy that inspects TLS can see this traffic, including your API
  key and PIN, exactly as it can for any other HTTPS site. Prefer a VPS you
  control for live trading.

Put 2-5 into `.env`:

```bash
python -m banknifty_trading_app.main setup      # input is not echoed
```

Then verify the account actually works end to end:

```bash
python -m banknifty_trading_app.main check      # login, LTP, daily candles, WebSocket
python -m banknifty_trading_app.main validate   # prints resolved strategy + config
```

`check` is read-only and never places an order. If the WebSocket step fails,
check whether the market is open - it usually still passes with a stale tick.

### Run it

```bash
python -m banknifty_trading_app.main run        # MODE=paper from .env
```

or drive it from the browser:

```bash
python -m banknifty_trading_app.main web        # http://127.0.0.1:8080
```

### What to watch while paper trading

* The console dashboard: position, P&L, stop level, levels, spot/futures LTP.
* `logs/` app log and the dedicated trade log.
* `python -m banknifty_trading_app.main report` → Excel of every trade.
* Whether entries fire when you would expect them to.
* Whether the simulated fills are optimistic (they will be - live slippage is
  usually worse than `PAPER_SLIPPAGE_POINTS`).

Paper trade for **several full sessions**, including an expiry day, before you
consider arming live.

---

## 3. Before you go live - run the preflight

```bash
python -m banknifty_trading_app.main preflight            # offline checks
python -m banknifty_trading_app.main preflight --online   # logs in to Angel One too
```

`--online` is still **entirely read-only** - it never places, modifies or cancels
an order. It checks:

* the interlock flags,
* **TLS: whether HTTPS to Angel One verifies, and who signed the certificate**
  (catches a corporate inspecting proxy by name),
* the instrument master is reachable,
* credentials present + login accepted,
* `MAX_LOTS` vs the strategy's lot count,
* `MAX_DAILY_LOSS` / `MAX_TRADES_PER_DAY` set (warns if you have no daily stop),
* kill switch not tripped,
* the strategy's `lot_size` **against the broker's real lot size** (a mismatch is
  a hard FAIL - a wrong lot size silently sends the wrong quantity),
* spot and futures instruments resolve to real tokens,
* expiry square-off time is before the close,
* your current broker positions are flat,
* the trailing-rule placeholder is still undecided (warns),
* and a list of operational things only you can confirm.

Everything is graded `PASS` / `WARN` / `FAIL` / `INFO`, and the exit code is
non-zero if anything is blocking. **Do not arm live while anything is `FAIL`.**

---

## 4. Phase 2 - going live

Only after the preflight is clean and you are satisfied with paper results.

```bash
# 1. Arm the interlock (asks you to type ARM to confirm)
python -m banknifty_trading_app.main arm-live

# 2. Choose how you start.
#    Option A - arm only; .env still says paper, so you stay in paper.
#               (useful: the console's LIVE button unlocks, but you choose when)
#    Option B - edit .env and set MODE=live and LIVE_TRADING=true
#               then start:
python -m banknifty_trading_app.main run --live
```

`--live` without `LIVE_ARMED=true` exits immediately with code 2 and tells you to
arm it. It will not silently trade, and it will not silently paper-trade either.

### The kill switch

```bash
python -m banknifty_trading_app.main kill "reason"
```

Blocks all new entries (exits still allowed) across every session. Reset with
`unkill`. Test it during paper trading so you know it works.

### Locking it again

```bash
python -m banknifty_trading_app.main disarm-live
```

This writes `LIVE_ARMED=false`, `LIVE_TRADING=false`, `MODE=paper` - a full
return to the safe state. Do this whenever you stop for the day if you are not
planning to trade tomorrow.

---

## 5. Things this app will not do for you

Being explicit, because these matter more than the code:

* **It is not a guarantee of profit.** The strategy in `strategy_versions/` is
  your rule set; the framework only executes it faithfully.
* **The "2% move" trailing rule is an undecided placeholder** and defaults to
  `disabled`. Decide its formula in the strategy YAML before trading it live.
* **`lot_size` in the YAML must match the exchange.** NSE changes Bank Nifty lot
  sizes from time to time. `preflight --online` fails if it disagrees.
* **Market orders have no price control.** With `ORDER_TYPE=MARKET` you fill at
  whatever the book gives you, especially in the opening minutes.
* **Latency is real.** Broker, network and exchange latency dominate any
  in-process speed; there is no millisecond guarantee.
* **A stop is not a guarantee.** A gap or a fast market can fill far worse than
  the stop level.
* **Keep the machine awake.** OS sleep, hibernate or a dead network during the
  session means missed exits.
* **Fund the account for the worst case**, including the reversal chain
  (`reversal_lots` × `max_reversals_per_day`) - not just the first entry.
* **Know how to flatten manually** from the Angel One terminal. If this process
  dies, that is your backstop.
* **This is not financial advice.** You are responsible for every order that goes
  out under your API key.

---

## 6. Quick reference

```bash
# state
python -m banknifty_trading_app.main live-status
python -m banknifty_trading_app.main preflight --online

# paper
python -m banknifty_trading_app.main run
python -m banknifty_trading_app.main web

# live
python -m banknifty_trading_app.main arm-live
python -m banknifty_trading_app.main run --live
python -m banknifty_trading_app.main disarm-live

# emergency
python -m banknifty_trading_app.main kill "reason"
python -m banknifty_trading_app.main unkill
```
