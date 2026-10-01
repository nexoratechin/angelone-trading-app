"""Command-line entry point.

    python -m banknifty_trading_app.main run            # paper or live per .env
    python -m banknifty_trading_app.main web            # full read-write web console
    python -m banknifty_trading_app.main setup          # securely write credentials to .env
    python -m banknifty_trading_app.main check          # connectivity self-test (no orders)
    python -m banknifty_trading_app.main backtest --synthetic
    python -m banknifty_trading_app.main backtest --start 2025-09-01 --end 2026-09-01
    python -m banknifty_trading_app.main demo           # offline: synthetic data through the LIVE pipeline
    python -m banknifty_trading_app.main report
    python -m banknifty_trading_app.main validate
    python -m banknifty_trading_app.main kill "manual stop"
    python -m banknifty_trading_app.main unkill
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import sys
from pathlib import Path

from .config import Settings, get_settings
from .logging import setup_logging
from .rules import load_spec


def _bootstrap(strategy: str | None = None) -> tuple[Settings, object]:
    settings = get_settings()
    if strategy:
        settings.active_strategy = strategy
    settings.ensure_dirs()
    setup_logging(settings.log_dir)
    spec = load_spec(settings.strategy_path())
    return settings, spec


def cmd_run(args: argparse.Namespace) -> int:
    from .runtime import TradingRuntime

    settings, spec = _bootstrap(args.strategy)
    if args.live:
        settings.mode = "live"
        settings.live_trading = True
    runtime = TradingRuntime(settings, spec)
    try:
        asyncio.run(runtime.start())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from .backtest.run import run_historical, run_synthetic

    settings, spec = _bootstrap(args.strategy)
    start = _dt.date.fromisoformat(args.start) if args.start else None
    end = _dt.date.fromisoformat(args.end) if args.end else None
    if args.synthetic:
        result = run_synthetic(settings, spec, start, end, args.output)
    else:
        result = run_historical(settings, spec, start, end, args.output)
    print(
        f"\nBacktest complete: {len(result.trades)} legs, "
        f"net P&L {result.total_pnl:,.2f}, report in {settings.report_dir}"
    )
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    from .credentials import setup_env_interactive

    setup_env_interactive()
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from .selftest import run_selftest

    settings, spec = _bootstrap(args.strategy)
    return run_selftest(settings, spec, ws_seconds=args.ws_seconds)


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import DemoRunner

    settings, spec = _bootstrap(args.strategy)
    start = _dt.date.fromisoformat(args.start) if args.start else _dt.date.today() - _dt.timedelta(days=180)
    end = _dt.date.fromisoformat(args.end) if args.end else _dt.date.today()
    out = asyncio.run(DemoRunner(settings, spec).run(start, end, args.output))
    print(f"\nDEMO finished. Report: {out}")
    print(f"Trades persisted to: {settings.db_path}  (run 'report' to regenerate)")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Serve the read-only dashboard from the offline demo engine (no broker needed)."""
    import time

    from .dashboard.app import start_dashboard
    from .demo import DemoRunner

    settings, spec = _bootstrap(args.strategy)
    start = _dt.date.fromisoformat(args.start) if args.start else _dt.date.today() - _dt.timedelta(days=120)
    end = _dt.date.fromisoformat(args.end) if args.end else _dt.date.today()

    runner = DemoRunner(settings, spec)
    server = start_dashboard(settings, None, runner)
    print(f"\nDashboard: http://{settings.dashboard_host}:{settings.dashboard_port}  (Ctrl+C to stop)")
    print("Replaying the demo now so the page updates live...\n")
    try:
        asyncio.run(runner.run(start, end, output=args.output, verbose_every=0, pace_s=args.pace))
        print("\nDemo replay finished. Dashboard stays live until you press Ctrl+C.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        if server is not None:
            server.should_exit = True
        print("\nDashboard stopped.")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    """Run the full read-write web console."""
    from .web.api import run_web

    settings, spec = _bootstrap(args.strategy)
    if args.host:
        settings.web_host = args.host
    if args.port:
        settings.web_port = args.port
    print(f"\nBank Nifty web console: http://{settings.web_host}:{settings.web_port}\n")
    run_web(settings)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .database.repository import Repository
    from .database.session import create_database
    from .reports.excel import write_report

    settings, spec = _bootstrap(args.strategy)
    repo = Repository(create_database(settings.db_path))
    rows = repo.all_trades()
    if not rows:
        print("No trades found in database.")
        return 1
    out = Path(args.output) if args.output else Path(settings.report_dir) / "live_trades_report.xlsx"
    write_report(out, rows, events=repo.recent_events(2000), meta={"source": "sqlite", "strategy": spec.version})
    print(f"Wrote report: {out}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    settings, spec = _bootstrap(args.strategy)
    print(f"Strategy: {spec.version}")
    print(f"  description : {spec.description.strip()[:80]}")
    print(f"  instrument  : {spec.instrument.symbol} {spec.instrument.contract} x{spec.instrument.lots} lots ({spec.instrument.lot_size}/lot)")
    print(f"  reference   : offset {spec.reference_offset_days} day(s) before yesterday")
    print(f"  SMA period  : {spec.sma_period}")
    print(f"  filters     : {[type(f).rule_name for f in spec.filters]}")
    print(f"  entry       : {type(spec.entry).rule_name}")
    print(f"  stop        : {type(spec.stop).rule_name}")
    print(f"  partial     : {type(spec.partial).rule_name}")
    print(f"  trailing    : {type(spec.trailing).rule_name if spec.trailing else 'none'}")
    print(f"  expiry      : {type(spec.expiry).rule_name}")
    print(f"  reversal    : {'on' if spec.reversal_enabled else 'off'} x{spec.reversal_lots}")
    print(f"\nMode: {settings.mode} (live trading {'ENABLED' if settings.is_live else 'disabled'})")
    print(f"Credentials: {settings.redacted_credentials()}")
    return 0


def cmd_kill(args: argparse.Namespace) -> int:
    from .risk.kill_switch import KillSwitch

    settings, _ = _bootstrap(args.strategy)
    KillSwitch(settings.kill_switch_file).trip(args.reason or "manual kill")
    print(f"Kill switch tripped: {settings.kill_switch_file}")
    return 0


def cmd_unkill(args: argparse.Namespace) -> int:
    from .risk.kill_switch import KillSwitch

    settings, _ = _bootstrap(args.strategy)
    KillSwitch(settings.kill_switch_file).reset()
    print("Kill switch reset")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="banknifty", description="Bank Nifty futures trading app")
    parser.add_argument("--strategy", help="strategy version name or YAML path override")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="start paper/live trading")
    p_run.add_argument("--live", action="store_true", help="force live mode (still requires credentials)")
    p_run.set_defaults(func=cmd_run)

    p_bt = sub.add_parser("backtest", help="run a backtest and write an Excel report")
    p_bt.add_argument("--synthetic", action="store_true", help="use generated data (no broker needed)")
    p_bt.add_argument("--start", help="YYYY-MM-DD")
    p_bt.add_argument("--end", help="YYYY-MM-DD")
    p_bt.add_argument("--output", help="output .xlsx path")
    p_bt.set_defaults(func=cmd_backtest)

    p_demo = sub.add_parser("demo", help="offline demo: synthetic data through the LIVE pipeline (no broker)")
    p_demo.add_argument("--start", help="YYYY-MM-DD")
    p_demo.add_argument("--end", help="YYYY-MM-DD")
    p_demo.add_argument("--output", help="output .xlsx path")
    p_demo.set_defaults(func=cmd_demo)

    p_dash = sub.add_parser("dashboard", help="serve the read-only dashboard from the offline demo engine")
    p_dash.add_argument("--start", help="YYYY-MM-DD")
    p_dash.add_argument("--end", help="YYYY-MM-DD")
    p_dash.add_argument("--output", help="output .xlsx path")
    p_dash.add_argument("--pace", type=float, default=0.2, help="seconds to pause per trading day")
    p_dash.set_defaults(func=cmd_dashboard)

    p_web = sub.add_parser("web", help="full read-write web console (start/stop, strategy editor, backtests)")
    p_web.add_argument("--host", help="bind host (default from .env WEB_HOST)")
    p_web.add_argument("--port", type=int, help="bind port (default from .env WEB_PORT)")
    p_web.set_defaults(func=cmd_web)

    p_rep = sub.add_parser("report", help="regenerate an Excel report from the database")
    p_rep.add_argument("--output", help="output .xlsx path")
    p_rep.set_defaults(func=cmd_report)

    p_val = sub.add_parser("validate", help="validate strategy/config")
    p_val.set_defaults(func=cmd_validate)

    p_setup = sub.add_parser("setup", help="securely write Angel One credentials to .env")
    p_setup.set_defaults(func=cmd_setup)

    p_check = sub.add_parser("check", help="Angel One connectivity self-test (read-only, no orders)")
    p_check.add_argument("--ws-seconds", type=float, default=8.0, help="seconds to listen for WebSocket ticks")
    p_check.set_defaults(func=cmd_check)

    p_kill = sub.add_parser("kill", help="trip the emergency kill switch")
    p_kill.add_argument("reason", nargs="?", help="reason text")
    p_kill.set_defaults(func=cmd_kill)

    p_unkill = sub.add_parser("unkill", help="reset the kill switch")
    p_unkill.set_defaults(func=cmd_unkill)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # surface cleanly instead of a raw traceback
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
