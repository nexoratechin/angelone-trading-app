"""Build the step-by-step HTML guide (English + Gujarati) from captured assets.

Output: pdf_assets/capture/guide.html  ->  printed to PDF by headless Chrome.
"""
from __future__ import annotations

import base64
import html
import re
from pathlib import Path

import openpyxl

ROOT = Path(".")
SHOTS = ROOT / "pdf_assets" / "screenshots"
CAP = ROOT / "pdf_assets" / "capture"
REPORTS = CAP / "reports"


def txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip("\n")


def img(name: str, caption: str) -> str:
    data = base64.b64encode((SHOTS / f"{name}.png").read_bytes()).decode("ascii")
    return (
        f'<figure><img alt="{html.escape(caption)}" '
        f'src="data:image/png;base64,{data}"/>'
        f'<figcaption>{caption}</figcaption></figure>'
    )


def term(title: str, text: str) -> str:
    return (
        f'<div class="term"><div class="term-bar">{html.escape(title)}</div>'
        f'<pre>{html.escape(text)}</pre></div>'
    )


def xlsx_table(path: Path, sheet: str, limit: int | None = None,
               transpose_kv: bool = False) -> str:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    rows = [r for r in rows if any(c is not None and str(c).strip() != "" for c in r)]
    if limit:
        rows = rows[:limit]
    if transpose_kv:
        body = "".join(
            f"<tr><th>{html.escape(str(r[0]))}</th><td>{html.escape(str(r[1]))}</td></tr>"
            for r in rows if len(r) >= 2
        )
        return f'<table class="kv">{body}</table>'
    out = ["<table class='grid'>"]
    header = rows[0]
    out.append("<thead><tr>" + "".join(f"<th>{html.escape(str(c or ''))}</th>" for c in header) + "</tr></thead><tbody>")
    for r in rows[1:]:
        cells = []
        for c in r:
            if isinstance(c, float):
                c = f"{c:,.2f}"
            cells.append(f"<td>{html.escape(str(c if c is not None else ''))}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def demo_excerpt() -> str:
    raw = txt(CAP / "demo_output.txt").splitlines()
    loglines = [ln for ln in raw if re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \|", ln)]
    head = loglines[:10]
    tail = loglines[-6:]
    mid = [
        "[ ... the engine repeats this every tick, every trading day ... ]",
    ]
    return "\n".join(head + mid + tail)


CSS = """
@page { size: A4; margin: 14mm 13mm 16mm 13mm; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", Arial, sans-serif; color: #1b2430; font-size: 10.5pt;
       line-height: 1.5; margin: 0; }
h1 { font-size: 22pt; color: #0b3d91; margin: 0 0 4px 0; }
h2 { font-size: 15pt; color: #0b3d91; border-bottom: 2px solid #0b3d91;
     padding-bottom: 4px; margin: 0 0 10px 0; }
h3 { font-size: 12pt; color: #123; margin: 16px 0 6px 0; }
p { margin: 6px 0; }
code, pre, .mono { font-family: "Cascadia Mono", Consolas, "Courier New", monospace; }
code { background: #eef2f7; padding: 1px 4px; border-radius: 3px; font-size: 9.5pt; }
.gu { color: #6a1b9a; font-family: "Nirmala UI", "Shruti", "Noto Sans Gujarati", sans-serif; }
.gu b { color: #4a148c; }
.section { page-break-before: always; }
.section:first-of-type { page-break-before: avoid; }
.lead { font-size: 11pt; color: #33475b; }
.note { background: #fff8e1; border-left: 4px solid #f9a825; padding: 8px 12px; margin: 10px 0; }
.warn { background: #ffebee; border-left: 4px solid #c62828; padding: 8px 12px; margin: 10px 0; }
.ok { background: #e8f5e9; border-left: 4px solid #2e7d32; padding: 8px 12px; margin: 10px 0; }
figure { margin: 12px 0; page-break-inside: avoid; }
figure img { width: 100%; height: auto; border: 1px solid #c8d0da; border-radius: 6px; }
figcaption { font-size: 8.5pt; color: #5b6b7c; margin-top: 4px; }
.term { border: 1px solid #23303f; border-radius: 6px; overflow: hidden; margin: 10px 0; }
.term-bar { background: #23303f; color: #cfe3ff; font-size: 8.5pt; padding: 4px 10px;
            font-family: Consolas, monospace; }
.term pre { background: #0f172a; color: #d6e2f0; margin: 0; padding: 10px 12px;
            font-size: 8.2pt; line-height: 1.35; white-space: pre-wrap;
            word-break: break-word; }
table.grid { border-collapse: collapse; width: 100%; font-size: 8pt; margin: 8px 0; }
table.grid th { background: #0b3d91; color: #fff; text-align: left; padding: 4px 6px; }
table.grid td { border-bottom: 1px solid #d8e0e9; padding: 3px 6px; }
table.grid tr:nth-child(even) td { background: #f4f7fb; }
table.kv { border-collapse: collapse; width: 100%; font-size: 9.5pt; margin: 8px 0; }
table.kv th { text-align: left; background: #eef2f7; padding: 4px 8px; width: 42%;
              border-bottom: 1px solid #d8e0e9; }
table.kv td { padding: 4px 8px; border-bottom: 1px solid #d8e0e9; }
ul, ol { margin: 6px 0 6px 18px; }
li { margin: 3px 0; }
.tree { font-family: Consolas, monospace; font-size: 8.5pt; background: #f5f7fa;
        border: 1px solid #d8e0e9; border-radius: 6px; padding: 10px 12px;
        white-space: pre; line-height: 1.35; }
.flow { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 8px 0 4px 0; }
.node { flex: 1 1 0; min-width: 92px; background: #e8f0fe; border: 1.5px solid #0b3d91;
        border-radius: 8px; padding: 8px 6px; text-align: center; font-size: 8.5pt;
        font-weight: 600; color: #0b3d91; }
.node .sub { display: block; font-weight: 400; color: #556; font-size: 7.5pt; }
.arrow { color: #0b3d91; font-weight: 700; }
.side { margin: 6px 0 0 0; font-size: 8.5pt; color: #556; }
.two-col { display: flex; gap: 14px; }
.two-col > div { flex: 1; }
.cover { text-align: center; margin-top: 90px; }
.cover .badge { display: inline-block; background: #0b3d91; color: #fff; border-radius: 20px;
                padding: 4px 16px; font-size: 9pt; margin-bottom: 18px; }
.cover .subtitle { color: #556; font-size: 12pt; margin-top: 6px; }
.cover .meta { color: #778; font-size: 9pt; margin-top: 40px; }
.toc li { margin: 5px 0; }
.kbd { background: #0f172a; color: #d6e2f0; padding: 1px 6px; border-radius: 4px;
       font-family: Consolas, monospace; font-size: 9pt; }
.tag { display: inline-block; background: #eef2f7; border: 1px solid #c8d0da; border-radius: 4px;
       padding: 0 6px; font-size: 8.5pt; font-family: Consolas, monospace; }
"""

# --------------------------------------------------------------------------
parts: list[str] = []
A = parts.append

A(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Bank Nifty Step-by-Step Guide</title><style>{CSS}</style></head><body>""")

# ----------------------------------------------------------------- cover
A("""
<div class="cover">
  <div class="badge">OFFLINE DEMO BUILD · NO REAL ORDERS</div>
  <h1>Bank Nifty Futures Trading App</h1>
  <div class="subtitle">Step-by-Step Guide — how it works, locally, from start to finish</div>
  <p class="gu" style="margin-top:26px;font-size:12pt">
    આ માર્ગદર્શિકા તમને સમજાવે છે કે આ એપ <b>શું કરે છે</b>, અંદરની
    <b>ફાઇલોનું કામ શું છે</b>, અને દરેક સ્ટેપ <b>કેવી રીતે ચલાવવો</b> —
    સ્ક્રીનશોટ સાથે.
  </p>
  <div class="note" style="max-width:560px;margin:30px auto;text-align:left">
    <b>What is inside:</b> a full walk-through of <code>setup → validate → check →
    demo → backtest → report → web → run → preflight → go-live</code>, with real
    screenshots captured from a local offline run.
  </div>
  <p class="warn" style="max-width:560px;margin:0 auto;text-align:left">
    <b>Not financial advice.</b> This is an execution framework. It can place real
    orders in live mode. Test in <i>paper</i> mode and on a backtest first.
  </p>
  <div class="meta">Generated from a local offline demo (synthetic data, paper
  execution). Date: 2026-10-04</div>
</div>
""")

# ----------------------------------------------------------------- TOC
A("""
<div class="section">
<h2>Contents</h2>
<ol class="toc">
  <li>What this app does &amp; the pipeline</li>
  <li>Project folder map — which file does what</li>
  <li>Setup — install, <code>.env</code>, credentials</li>
  <li>Step 1 — <code>validate</code> (check the strategy)</li>
  <li>Step 2 — <code>check</code> (broker connectivity, read-only)</li>
  <li>Step 3 — <code>demo</code> (offline run through the live pipeline)</li>
  <li>Step 4 — <code>backtest --synthetic</code> (strategy-only replay)</li>
  <li>Step 5 — <code>report</code> (Excel of every trade)</li>
  <li>Step 6 — <code>web</code> console tour (all 6 tabs)</li>
  <li>Step 7 — <code>run</code> paper trading (what happens every tick)</li>
  <li>Step 8 — Go-live safety (preflight, arm, kill switch)</li>
  <li>Step 9 — End-to-end trade walkthrough + the 15 rules</li>
  <li>Troubleshooting &amp; FAQ</li>
</ol>
</div>
""")

# ----------------------------------------------------------------- 1 overview
A(f"""
<div class="section">
<h2>1. What this app does &amp; the pipeline</h2>
<p class="lead">An event-driven Python app that trades <b>Bank Nifty near-month
futures</b> against <b>Bank Nifty Spot</b> signals. The same engine runs live
trading and backtesting, so the two can never drift apart. Strategies are plain
<b>YAML files</b> — you change rules without rewriting code.</p>
<p class="gu">આ એપ <b>Bank Nifty futures</b> ને Angel One SmartAPI દ્વારા ઓટોમેટિક
ટ્રેડ કરે છે. શરૂઆતમાં તે <b>paper</b> મોડમાં ચાલે છે — સાચા ભાવ મળે છે, પણ ખરેખર
ઓર્ડર મોકલાતો નથી. બધા નિયમો <b>YAML ફાઇલ</b>માં લખેલા છે, એટલે કોડ બદલ્યા વગર
સ્ટ્રેટેજી બદલી શકાય.</p>

<h3>The data pipeline (every tick travels this path)</h3>
<div class="flow">
  <div class="node">Market data<span class="sub">WebSocket ticks</span></div>
  <div class="arrow">&rarr;</div>
  <div class="node">Strategy engine<span class="sub">rules YAML</span></div>
  <div class="arrow">&rarr;</div>
  <div class="node">Signal<span class="sub">BUY / SELL intent</span></div>
  <div class="arrow">&rarr;</div>
  <div class="node">Risk manager<span class="sub">limits, kill switch</span></div>
  <div class="arrow">&rarr;</div>
  <div class="node">Order manager<span class="sub">sizing, dedupe</span></div>
  <div class="arrow">&rarr;</div>
  <div class="node">Executor<span class="sub">paper / live</span></div>
  <div class="arrow">&rarr;</div>
  <div class="node">Angel One<span class="sub">REST order</span></div>
</div>
<p class="side">&darr; positions / trade ledger &darr; &nbsp; SQLite (batched write) &darr; &nbsp; Excel report &amp; dashboard</p>

<div class="two-col" style="margin-top:12px">
<div>
<h3>Hot path = fast</h3>
<ul>
  <li>The engine does <b>in-memory math only</b> on the hot path: no DB reads,
      no Excel, no blocking I/O.</li>
  <li>Ticks arrive on a WebSocket thread and are handed to the asyncio loop.</li>
  <li>SQLite writes happen on a separate <b>batched writer thread</b>.</li>
</ul>
</div>
<div>
<h3>Two modes</h3>
<ul>
  <li><b>Paper (default):</b> real Angel One prices, simulated fills, zero real
      orders. Only the final broker call is swapped out.</li>
  <li><b>Live:</b> real orders, real money — behind a three-flag interlock.</li>
</ul>
</div>
</div>

<div class="ok"><b>Key idea.</b> Paper mode is not a toy. The strategy, risk gates,
position book and P&amp;L you watch are the real ones — only the fill is simulated.</div>
<p class="gu"><b>મુખ્ય વાત:</b> Paper મોડ નકલી નથી. સ્ટ્રેટેજી, રિસ્ક, પોઝિશન અને
P&amp;L બધું સાચું ચાલે છે — ફક્ત ઓર્ડર સાચો મોકલાતો નથી.</p>
</div>
""")

# ----------------------------------------------------------------- 2 folders
A(f"""
<div class="section">
<h2>2. Project folder map — which file does what</h2>
<p class="gu">નીચે પ્રોજેક્ટના ફોલ્ડરનું કામ ટૂંકમાં લખ્યું છે. દરેક ફોલ્ડરનું
એક જ જવાબદારી છે.</p>
<div class="tree">{html.escape('''Angelone app/
├── main.py                    convenience shim -> python main.py <command>
├── README.md                  full project docs
├── LIVE_TRADING.md            paper -> live operating manual
├── .env                       your secrets + settings (git-ignored)
├── strategy_versions/         v1_baseline.yaml, v2_example.yaml  (the RULES)
├── reports/                   generated Excel reports land here
├── data/                      SQLite DB, users, instruments, kill-switch file
├── logs/                      app log + dedicated trade log
└── banknifty_trading_app/     the application package
    ├── main.py                CLI entry point (all commands)
    ├── runtime.py             LIVE wiring: feed -> engine -> risk -> executor
    ├── config.py              typed settings + the live interlock
    ├── preflight.py           go-live checklist (read-only)
    ├── selftest.py            `check` connectivity self-test
    ├── demo.py                offline synthetic run through the LIVE pipeline
    ├── credentials.py         reads/writes .env safely
    ├── core/                  events, bus, models, in-memory state
    ├── angelone/              auth, REST, WebSocket, instruments (SmartAPI)
    ├── market_data/           feed, tick store, daily closes, staleness
    ├── strategy/              engine + indicators (the brain)
    ├── rules/                 base, registry, filters, entries, stops, targets
    ├── risk/                  manager, limits, kill switch
    ├── execution/             order manager, router, paper, live, recovery
    ├── positions/             book, ledger, P&L
    ├── backtest/              replay, run, data loader, costs, synthetic data
    ├── database/              SQL DDL, session, batched writer, repository
    ├── reports/               Excel writer
    ├── dashboard/             minimal read-only dashboard
    ├── web/                   full read-write web console (FastAPI + JS)
    ├── logging/               logging setup
    └── tests/                 pytest suite''')}</div>
<p class="note"><b>Rule of thumb:</b> to change <i>what</i> the strategy does, edit
YAML in <span class="tag">strategy_versions/</span>. To change the <i>engine</i>,
edit <span class="tag">strategy/engine.py</span> or add a rule in
<span class="tag">rules/</span>. Everything else (broker, data, DB) stays
untouched.</p>
</div>
""")

# ----------------------------------------------------------------- 3 setup
A(f"""
<div class="section">
<h2>3. Setup — install, <code>.env</code>, credentials</h2>
<p class="gu">પહેલા Python virtual environment બનાવો, પછી ડિપેન્ડન્સી ઇન્સ્ટોલ કરો,
અને <code>.env</code> ફાઇલમાં તમારી Angel One માહિતી ભરો.</p>
<h3>3.1 Create the environment &amp; install dependencies</h3>
{term("PowerShell", '''python -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt

copy .env.example .env      # then fill in your Angel One credentials''')}
<p>The offline commands (<code>demo</code>, <code>backtest --synthetic</code>,
<code>report</code>, and the <code>web</code> console) need <b>no broker
credentials</b>. Only <code>check</code>, paper <code>run</code> and live need
them.</p>

<h3>3.2 Fill in <code>.env</code></h3>
{term(".env (never commit this file)", '''MODE=paper
LIVE_TRADING=false
LIVE_ARMED=false          # master interlock - leave false until you go live
ANGEL_API_KEY=...
ANGEL_CLIENT_CODE=...
ANGEL_PIN=...
ANGEL_TOTP_SECRET=...''')}
<p>You can also let the app write the secrets for you (input is not echoed):</p>
{term("PowerShell", "python -m banknifty_trading_app.main setup")}
<p class="gu">અથવા <code>setup</code> કમાન્ડ વાપરો — તે તમારાથી માહિતી લઈને
સીધું <code>.env</code> માં લખે છે (ટાઇપ કરેલું સ્ક્રીન પર દેખાતું નથી).</p>
<div class="note"><b>Where to get the values:</b> SmartAPI portal &rarr; Create App
(API key), your Angel One login id (client code), 4-digit trading PIN, and the
<b>TOTP secret seed</b> (the base32 seed, not the rotating 6 digits).</div>
</div>
""")

# ----------------------------------------------------------------- 4 validate
A(f"""
<div class="section">
<h2>4. Step 1 — <code>validate</code></h2>
<p class="lead">Shows the <b>resolved</b> strategy and config: what the engine will
actually run, before you start anything.</p>
<p class="gu">આ કમાન્ડ બતાવે છે કે એપ કઈ સ્ટ્રેટેજી અને કઈ સેટિંગ વાપરવાની છે —
ટ્રેડિંગ શરૂ કરતાં પહેલાં ખાતરી કરવા માટે.</p>
{term("PowerShell:  python -m banknifty_trading_app.main validate", txt(CAP / "validate.txt"))}
<p><b>How to read it:</b> <code>instrument</code> = what it trades (BANKNIFTY
futures, 2 lots of 30). <code>reference</code> = the cross level is the close from
2 days before yesterday. <code>filters</code> = the 20 SMA filter.
<code>entry / stop / partial / trailing / expiry / reversal</code> = the rule
actually loaded for each stage. <code>Mode: paper</code> means live is disabled.</p>
</div>
""")

# ----------------------------------------------------------------- 5 check
A(f"""
<div class="section">
<h2>5. Step 2 — <code>check</code> (read-only)</h2>
<p class="lead">A connectivity self-test: login, LTP, daily candles and the
WebSocket. It <b>never places an order</b>.</p>
{term("PowerShell:  python -m banknifty_trading_app.main check", '''python -m banknifty_trading_app.main check
# -> logs in to Angel One, fetches an LTP + daily closes, then listens
#    for WebSocket ticks for a few seconds.
# -> NO orders are placed.''')}
<div class="note"><b>Needs credentials.</b> Run this on the machine that will
trade, during market hours. If the WebSocket step fails, check whether the market
is open — it usually still passes with a stale tick. Corporate TLS-inspecting
proxies are handled automatically (see Troubleshooting).</div>
<p class="gu">આ કમાન્ડ ફક્ત <b>ચેક</b> કરે છે કે Angel One સાથે જોડાણ થાય છે કે નહીં.
કોઈ ઓર્ડર મોકલાતો નથી. તેના માટે credentials જરૂરી છે.</p>
</div>
""")

# ----------------------------------------------------------------- 6 demo
A(f"""
<div class="section">
<h2>6. Step 3 — <code>demo</code> (offline, no broker)</h2>
<p class="lead">Feeds <b>synthetic</b> market data through the <b>real</b> live
pipeline: strategy engine, risk manager, order manager, paper executor, position
book, trade ledger, SQLite writer, Excel report. No credentials, no broker.</p>
<p class="gu">આ કમાન્ડ બ્રોકર વગર, બનાવેલા (synthetic) ડેટા પર <b>પૂરી પાઇપલાઇન</b>
ચલાવે છે. એટલે તમે credentials વગર જ આખી સિસ્ટમ ચાલતી જોઈ શકો.</p>
{term("PowerShell:  python -m banknifty_trading_app.main demo --start 2025-09-01 --end 2026-03-01", demo_excerpt())}
<p><b>What happened:</b> over the 6-month window the engine generated <b>46 trade
legs</b>, wrote every order / fill / trade / event to the SQLite DB, and produced
<span class="tag">demo_report.xlsx</span>. On this sample the realized P&amp;L was
<b>&minus;31,335</b> — a loss, which is exactly why you run a backtest and paper
trade <i>before</i> risking money.</p>
<p class="gu">અહીં 6 મહિનામાં 46 ટ્રેડ થયા અને એક Excel રિપોર્ટ બન્યો. આ સેમ્પલમાં
ખોટ (loss) આવી — તેથી જ પહેલા backtest અને paper trading કરવું જરૂરી છે.</p>
</div>
""")

# ----------------------------------------------------------------- 7 backtest
A(f"""
<div class="section">
<h2>7. Step 4 — <code>backtest --synthetic</code></h2>
<p class="lead">A <b>one-year</b> replay of the strategy on generated data, and an
Excel report of every trade, equity curve and event.</p>
{term("PowerShell:  python -m banknifty_trading_app.main backtest --synthetic --start 2025-09-01 --end 2026-03-01", txt(CAP / "backtest.txt"))}
<div class="warn"><b>Demo vs backtest — important difference.</b><br>
<b>demo</b> runs the strategy <i>and</i> the live execution path (paper broker,
risk, position book, DB).<br>
<b>backtest</b> exercises the strategy engine only, with a fill model and
charges. Use it to judge the rules; use <code>demo</code> to watch the plumbing.</div>
<p>Historical (non-synthetic) mode uses real Angel One candles — Spot for signals
and a stitched near-month futures series for fills — and caches chunks under
<span class="tag">data/history/</span>. That mode needs credentials and is
subject to the historical API's look-back limits.</p>
<p class="gu">Backtest = ફક્ત સ્ટ્રેટેજીની ટેસ્ટ (એક વર્ષ). Demo = આખી લાઇવ
પાઇપલાઇનની ટેસ્ટ. બંને એક જ નિયમો વાપરે છે.</p>
</div>
""")

# ----------------------------------------------------------------- 8 report
A(f"""
<div class="section">
<h2>8. Step 5 — <code>report</code> (Excel from the database)</h2>
<p class="lead">Regenerates an Excel workbook from whatever is stored in SQLite —
every trade, the summary metrics, the equity curve and the strategy events.</p>
{term("PowerShell:  python -m banknifty_trading_app.main report", txt(CAP / "report.txt"))}

<h3>Report &mdash; Summary sheet (from a real backtest run)</h3>
{xlsx_table(REPORTS / "backtest_v1_baseline_20261004.xlsx", "Summary", transpose_kv=True)}
<p class="gu">રિપોર્ટમાં <b>Summary</b> (કુલ આંકડા), <b>Trades</b> (દરેક ટ્રેડ),
<b>Equity</b> (ઇક્વિટી કર્વ) અને <b>Events</b> (સ્ટ્રેટેજી ઘટનાઓ) — ચાર શીટ હોય છે.</p>

<h3>Report &mdash; Trades sheet (first rows)</h3>
{xlsx_table(REPORTS / "backtest_v1_baseline_20261004.xlsx", "Trades", limit=13)}
</div>
""")

# ----------------------------------------------------------------- 9 web
A(f"""
<div class="section">
<h2>9. Step 6 — the <code>web</code> console (all 6 tabs)</h2>
<p class="lead">A full control panel — not a read-only page. Start/stop sessions,
edit strategies, run backtests, manage settings and hit the emergency stop.</p>
{term("PowerShell:  python -m banknifty_trading_app.main web", '''Bank Nifty web console: http://127.0.0.1:8080

No accounts yet - visit /register to create the first (admin) account''')}
<p class="gu">બ્રાઉઝરમાં <code>http://127.0.0.1:8080</code> ખોલો. ડાબી બાજુના
ટેબ વડે Dashboard, Trades, Events, Strategy, Backtest અને Settings જોઈ શકાય.
<b>Live</b> વિકલ્પ ત્યાં સુધી બંધ રહે છે જ્યાં સુધી CLI થી arm ન કરો.</p>

<h3>Tab 1 — Dashboard</h3>
{img("01_dashboard", "Dashboard: session, mode, kill switch, position, P&L, stop, Spot/Futures LTP, levels — updated live over WebSocket.")}
<p>Start a session from the <b>Start session</b> panel: choose <i>demo</i>
(offline), <i>paper</i> (real prices, simulated fills) or <i>LIVE</i> (locked). The
checkbox and the code-level arm switch are two independent gates for live.</p>

<h3>Tab 2 — Trades</h3>
{img("02_trades", "Trades: every closed leg with entry/exit, points, P&L, stop, exit reason, reversal and partial flags.")}

<h3>Tab 3 — Events</h3>
{img("03_events", "Events: the strategy's own decision log (day start, entry, stop hit, reversal, square-off).")}

<h3>Tab 4 — Strategy editor</h3>
{img("04_strategy", "Strategy: versioned YAML files. Saving validates against the rule registry before writing — rules are data, no code changes.")}
<p class="gu">અહીં YAML સ્ટ્રેટેજી સીધી બ્રાઉઝરમાં એડિટ કરી શકાય. સેવ કરતી વખતે
એપ નિયમો ચેક કરે છે. નવી વર્ઝન બનાવવા <b>+ New version</b> દબાવો.</p>

<h3>Tab 5 — Backtest</h3>
{img("05b_backtest_result", "Backtest: run a synthetic or historical backtest in the browser and download the Excel reports from the list.")}

<h3>Tab 6 — Settings</h3>
{img("06_settings", "Settings: safe, non-secret settings + the Angel One credential form. LIVE_ARMED is shown but cannot be changed here.")}
<p class="note">The console can <b>display</b> the arm state but <b>cannot change
it</b> — <code>LIVE_ARMED</code> is deliberately excluded from the editable
settings. Arming is CLI-only.</p>

<h3>Emergency — kill switch</h3>
{img("07_dashboard_killswitch", "Kill switch ACTIVE: blocks all new entries (exits are still allowed) across every session. Reset with 'Reset' or 'unkill'.")}
</div>
""")

# ----------------------------------------------------------------- 10 paper run
A(f"""
<div class="section">
<h2>10. Step 7 — <code>run</code> paper trading</h2>
<p class="lead">The real thing, minus the real order. It logs in to Angel One,
streams real WebSocket ticks for Spot and futures, computes real daily closes and
levels, and runs the real strategy/risk/order-manager pipeline — then simulates
the fill at the last tick &plusmn; <code>PAPER_SLIPPAGE_POINTS</code>.</p>
{term("PowerShell", '''python -m banknifty_trading_app.main run        # MODE=paper from .env''')}

<h3>What happens on one tick</h3>
<ol>
  <li><b>Tick arrives</b> on the WebSocket thread &rarr; handed to the asyncio loop.</li>
  <li><b>Strategy engine</b> updates indicators and checks every rule (SMA filter,
      reference cross, stop, partial, trailing, expiry).</li>
  <li>If a rule fires, it emits a <b>signal intent</b> (BUY/SELL, lots, reason).</li>
  <li><b>Risk manager</b> checks the kill switch, max lots, and daily loss/trade limits.</li>
  <li><b>Order manager</b> sizes the order, dedupes by intent id, and routes it.</li>
  <li>The <b>executor</b> fills it: paper = simulated at last tick; live = REST call
      to Angel One.</li>
  <li>The <b>position book &amp; ledger</b> update; the <b>batched writer</b> persists
      orders, fills, trades and events to SQLite.</li>
  <li>The <b>dashboard / web console</b> reflects the new state over WebSocket.</li>
</ol>
<p class="gu">દરેક tick પર: ભાવ આવે &rarr; સ્ટ્રેટેજી નિયમો ચેક થાય &rarr; સિગ્નલ
બને &rarr; રિસ્ક ચેક થાય &rarr; ઓર્ડર બને &rarr; paper માં નકલી ફિલ થાય (live માં
સાચો ઓર્ડર જાય) &rarr; P&amp;L અપડેટ &rarr; ડેટાબેઝમાં સેવ થાય.</p>

<div class="ok"><b>Paper trade for several full sessions</b>, including an expiry
day, before considering live. Watch the console, <span class="tag">logs/</span>,
and the Excel report. Paper fills are optimistic — real slippage is usually worse.</div>
</div>
""")

# ----------------------------------------------------------------- 11 go-live
A(f"""
<div class="section">
<h2>11. Step 8 — go-live safety</h2>
<p class="lead">Live trading requires <b>all three</b> flags, enforced in three
independent places. If any one is missing, no real order can go out.</p>
<table class="kv">
  <tr><th>Flag</th><th>Meaning</th><th>Default</th></tr>
  <tr><td><code>MODE=live</code></td><td>the session is a live session</td><td>paper</td></tr>
  <tr><td><code>LIVE_TRADING=true</code></td><td>the operator opted in</td><td>false</td></tr>
  <tr><td><code>LIVE_ARMED=true</code></td><td>master arm switch (CLI only)</td><td>false</td></tr>
</table>

<h3>Check the state any time</h3>
{term("PowerShell:  python -m banknifty_trading_app.main live-status", txt(CAP / "live_status.txt"))}

<h3>Preflight — the go-live checklist (read-only)</h3>
{term("PowerShell:  python -m banknifty_trading_app.main preflight", txt(CAP / "preflight.txt"))}
<p class="gu">Preflight દરેક વસ્તુ ચેક કરે છે: credentials, TLS/નેટવર્ક, lot size,
રિસ્ક લિમિટ, કિલ સ્વિચ, અને બ્રોકર પોઝિશન. <b>જ્યાં સુધી કોઈ FAIL હોય ત્યાં સુધી
<code>arm-live</code> ન કરો.</b> દરેક વસ્તુ <b>PASS / WARN / FAIL / INFO</b> માં
ગ્રેડ થાય છે.</p>

<h3>Arm / disarm &amp; the kill switch</h3>
{term("PowerShell", '''# 1. Arm the interlock (asks you to type ARM to confirm)
python -m banknifty_trading_app.main arm-live

# 2. Put it fully live in .env, then start:
#    MODE=live
#    LIVE_TRADING=true
python -m banknifty_trading_app.main run --live

# Emergency: block all new entries (exits still allowed)
python -m banknifty_trading_app.main kill "manual stop"
python -m banknifty_trading_app.main unkill

# Lock everything again -> LIVE_ARMED=false, LIVE_TRADING=false, MODE=paper
python -m banknifty_trading_app.main disarm-live''')}
<p class="warn"><b>Before you arm live:</b> preflight clean, you know how to
flatten manually from the broker terminal, <code>lot_size</code> in the YAML
matches the exchange, you funded the account for the worst case (including the
reversal chain), and the machine stays awake 09:15&ndash;15:30 IST.</p>
</div>
""")

# ----------------------------------------------------------------- 12 walkthrough
A(f"""
<div class="section">
<h2>12. Step 9 — end-to-end trade walkthrough</h2>
<p class="lead">One concrete example of how a signal becomes a trade, using the
baseline rules.</p>
<ol>
  <li>At market open the engine fetches the last daily Spot closes and computes
      the <b>reference close</b> (close from 2 days ago), <b>yesterday's close</b>
      and the <b>20-day SMA</b>.</li>
  <li>Spot <b>crosses above</b> the reference close <b>and</b> Spot &gt; SMA &rarr;
      <b>BUY 2 lots</b> of BANKNIFTY futures. (Below &rarr; SELL 2 lots.)</li>
  <li>The initial stop is yesterday's Spot close, used only if it is genuinely
      <i>protective</i> of the entry.</li>
  <li>From the next day the stop uses the previous day's close and only
      <b>ratchets</b> (never loosens).</li>
  <li>A <b>+1.7%</b> favourable move squares off <b>one</b> lot, carries the other,
      and the stop moves to breakeven.</li>
  <li>If the stop is hit, the position exits and <b>reverses 2 lots</b> the other
      way, under the same rules (with a cooldown and a daily cap).</li>
  <li>On the <b>last trading day</b> of the contract it squares off and carries
      nothing overnight.</li>
  <li>Every decision, order, fill and trade is written to SQLite and the trade
      log, then surfaced in the console and the Excel report.</li>
</ol>
<p class="gu">ઉદાહરણ: Spot ભાવ reference ભાવ ઉપર જાય + SMA ઉપર હોય તો BUY 2 lots.
સ્ટોપ ગયા તો exit અને ઊલટું 2 lots (reversal). +1.7% થાય તો એક lot book કરો,
બીજું ચાલુ રાખો. છેલ્લા દિવસે બધું બંધ કરો.</p>

<h3>The 15 rules as implemented</h3>
<table class="grid">
<thead><tr><th>#</th><th>Rule</th><th>Implementation</th></tr></thead>
<tbody>
<tr><td>1</td><td>Record recent daily Spot closes</td><td>fetched at start-up; used for levels + SMA</td></tr>
<tr><td>2</td><td>20 SMA filter</td><td>daily Spot closes fixed at prior close (no look-ahead)</td></tr>
<tr><td>3</td><td>BUY only above SMA / SELL only below</td><td><code>SmaFilter</code></td></tr>
<tr><td>4</td><td>Cross day-before-yesterday close &rarr; BUY 2 lots</td><td><code>ReferenceCrossEntry</code>, offset 2</td></tr>
<tr><td>5</td><td>Cross below it &rarr; SELL 2 lots</td><td>same rule, opposite side</td></tr>
<tr><td>6</td><td>Only one initial trade per cycle</td><td><code>initial_trade_done</code> guard</td></tr>
<tr><td>7</td><td>Trade day: yesterday's close = stop</td><td><code>DailyCloseStop.initial_stop</code></td></tr>
<tr><td>8</td><td>From next day: previous day's close, ratchet only</td><td><code>daily_stop(ratchet=True)</code></td></tr>
<tr><td>9</td><td>+1.7% &rarr; square off 1 lot, carry 1, trail</td><td><code>PercentPartialTarget</code></td></tr>
<tr><td>10</td><td>Stop hit &rarr; exit and reverse 2 lots</td><td><code>_stop_out</code> + reversal entry</td></tr>
<tr><td>11</td><td>Continue same rules after reversal</td><td>reversal uses the same rule objects</td></tr>
<tr><td>12</td><td>"2% move" trailing rule</td><td>configurable placeholder (no invented formula)</td></tr>
<tr><td>13</td><td>Last trading day &rarr; square off, no carry</td><td><code>AlwaysSquareOff</code></td></tr>
<tr><td>14</td><td>Record everything</td><td>SQLite + trade log</td></tr>
<tr><td>15</td><td>One-year backtest &rarr; Excel</td><td><code>backtest</code> + reports/excel</td></tr>
</tbody>
</table>
<div class="warn"><b>Known ambiguity:</b> the "2% move" trailing rule is
undefined in the notes. It ships as <code>percent_move_placeholder</code> with
<code>mode: disabled</code>. <code>custom</code> refuses to load until you
implement it. Decide the formula before trading it live.</div>
</div>
""")

# ----------------------------------------------------------------- 13 troubleshooting
A(f"""
<div class="section">
<h2>13. Troubleshooting &amp; FAQ</h2>

<h3>"CERTIFICATE_VERIFY_FAILED" on every HTTPS call</h3>
<p>A corporate proxy (Zscaler, Netskope, Fortinet...) re-signs TLS with its own
root CA. That CA is in the Windows store but not in <code>certifi</code>. The app
installs <code>truststore</code> at start-up so verification uses the OS store —
verification stays <b>ON</b>. Run <code>preflight</code> and look at the
<b>Network / TLS</b> section: it names the issuer. Disable with
<code>USE_SYSTEM_TRUST_STORE=false</code>.</p>

<h3>Login fails because of IP whitelisting</h3>
<p><code>smartapi-python</code> hardcodes its <code>X-ClientPublicIP</code> header.
The app patches it with your real public IP before login. If your outbound IP
<b>changes between requests</b> (multi-WAN / corporate proxy), no whitelist can
work — ask Angel One to remove the IP restriction, or run on a VPS with a stable
address.</p>

<h3>"sb.append is not a function" / a smartapi import error</h3>
<p><code>smartapi-python</code> does not declare all of its own dependencies.
<code>requirements.txt</code> pins <code>logzero</code> and <code>six</code>
explicitly because a missing one makes the SDK fail at import.</p>

<h3>Do I need an Angel One account to try it?</h3>
<p>No. <code>demo</code>, <code>backtest --synthetic</code>, <code>report</code>
and the <code>web</code> console all run fully offline. Credentials are needed
only for <code>check</code>, paper <code>run</code> and live.</p>

<h3>How do I change the strategy?</h3>
<p>Edit YAML in <span class="tag">strategy_versions/</span> — e.g. 20 &rarr; 30
SMA (<code>filters[0].period: 30</code>), 1.7% &rarr; 2%
(<code>partial.percent: 2.0</code>), 2 &rarr; 3 lots
(<code>instrument.lots: 3</code>). Add a new file and set
<code>ACTIVE_STRATEGY=v2_myrules</code> in <code>.env</code>. Nothing else
changes.</p>

<h3>How do I know the code is safe?</h3>
<p>Paper by default &middot; three-flag live interlock enforced in three places
&middot; the web console cannot arm live &middot; duplicate-order protection
&middot; order-status confirmation with timeout &middot; WebSocket auto-reconnect
&middot; crash recovery + position reconciliation at start-up &middot; stale
market-data detection &middot; emergency kill switch &middot; maximum-lot
protection &middot; full logging.</p>

<p class="gu"><b>યાદ રાખો:</b> આ ફાઇનાન્શિયલ એડવાઇસ નથી. પહેલા backtest અને paper
trading કરો. Live arm કરતાં પહેલાં preflight સાફ હોવું જોઈએ.</p>
</div>
""")

# ----------------------------------------------------------------- footer
A("""
<div class="section">
<h2>Quick reference</h2>
<table class="grid">
<thead><tr><th>Command</th><th>What it does</th><th>Needs broker?</th></tr></thead>
<tbody>
<tr><td><code>validate</code></td><td>show resolved strategy + config</td><td>no</td></tr>
<tr><td><code>setup</code></td><td>write credentials to .env (no echo)</td><td>no</td></tr>
<tr><td><code>check</code></td><td>connectivity self-test (no orders)</td><td>yes</td></tr>
<tr><td><code>demo</code></td><td>offline synthetic run through the live pipeline</td><td>no</td></tr>
<tr><td><code>backtest --synthetic</code></td><td>one-year strategy replay &rarr; Excel</td><td>no</td></tr>
<tr><td><code>backtest --start .. --end ..</code></td><td>historical backtest &rarr; Excel</td><td>yes</td></tr>
<tr><td><code>report</code></td><td>regenerate Excel from SQLite</td><td>no</td></tr>
<tr><td><code>web</code></td><td>full read-write console at 127.0.0.1:8080</td><td>no</td></tr>
<tr><td><code>run</code></td><td>paper (default) / live per .env</td><td>yes</td></tr>
<tr><td><code>preflight [--online]</code></td><td>go-live checklist (read-only)</td><td>optional</td></tr>
<tr><td><code>arm-live / disarm-live</code></td><td>arm / lock the live interlock</td><td>no</td></tr>
<tr><td><code>live-status</code></td><td>show the interlock state</td><td>no</td></tr>
<tr><td><code>kill "why" / unkill</code></td><td>emergency stop / reset</td><td>no</td></tr>
</tbody>
</table>
<p class="meta" style="color:#778;font-size:9pt;margin-top:30px">
Bank Nifty Futures Trading App — Step-by-Step Guide. Offline demo build,
2026-10-04. Not financial advice.</p>
</div>
</body></html>
""")

out = CAP / "guide.html"
out.write_text("".join(parts), encoding="utf-8")
print(f"wrote {out} ({out.stat().st_size//1024} KB)")
