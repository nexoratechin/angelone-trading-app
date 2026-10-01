"""Convenience shim so you can run ``python main.py <command>`` from the repo root."""

from banknifty_trading_app.main import main

if __name__ == "__main__":
    raise SystemExit(main())
