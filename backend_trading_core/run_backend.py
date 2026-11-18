"""Windows-friendly entrypoint for the local paper server and offline demo."""
from __future__ import annotations

import argparse
import asyncio
import logging

from pathlib import Path
from zipfile import BadZipFile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--demo", action="store_true", help="run the offline replay and write an HTML report")
    modes.add_argument("--research-crypto", type=Path, help="compare strategies offline using an OHLCV CSV or Binance kline zip")
    parser.add_argument("--open-dashboard", action="store_true", help="open the browser after the paper server binds")
    parser.add_argument("--output", type=Path, default=Path("demo_output"))
    parser.add_argument("--equity-csv", type=Path, help="replace sample equity data with raw XNYS daily data")
    parser.add_argument("--equity-symbol", default="DEMO")
    parser.add_argument("--crypto-symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", choices=("1m", "5m", "15m", "1h"), default="1h")
    parser.add_argument("--research-capital", type=float, default=25)
    parser.add_argument("--research-fee-bps", type=float, default=10)
    parser.add_argument("--research-spread-bps", type=float, default=6)
    parser.add_argument("--research-tick", type=float, default=0.01)
    parser.add_argument("--research-lot", type=float, default=0.00001)
    parser.add_argument("--research-min-notional", type=float, default=5)
    parser.add_argument("--research-warmup", type=int, default=60)
    parser.add_argument("--research-window", type=int, default=168)
    args = parser.parse_args()
    if args.open_dashboard and (args.demo or args.research_crypto):
        parser.error("--open-dashboard requires the paper server")
    if args.research_crypto:
        from backtest.experiment import run_crypto_research

        try:
            run_crypto_research(
                args.output, args.research_crypto, symbol=args.crypto_symbol, timeframe=args.timeframe,
                capital=args.research_capital, fee_bps=args.research_fee_bps, spread_bps=args.research_spread_bps,
                tick=args.research_tick, lot=args.research_lot, minimum_notional=args.research_min_notional,
                warmup=args.research_warmup, window=args.research_window,
            )
        except (OSError, ValueError, BadZipFile) as exc:
            parser.exit(2, f"Research failed: {exc}\n")
        print((args.output / "report.html").resolve())
        return
    if args.demo:
        from demo.runner import run_demo

        try:
            snapshot = run_demo(args.output, args.equity_csv, args.equity_symbol)
        except (OSError, ValueError) as exc:
            parser.exit(2, f"Replay failed: {exc}\n")
        for account in snapshot["accounts"]:
            filled = sum(len(order["fills"]) for order in account["orders"])
            blocked = sum(order["status"] == "RISK_REJECTED" for order in account["orders"])
            print(f"{account['currency']} cash {account['capital']:.2f} | {filled} fills | {blocked} risk blocks")
        print((args.output / "report.html").resolve())
        return
    if args.equity_csv or args.equity_symbol != "DEMO":
        parser.error("equity CSV options require --demo")

    from main import run

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        asyncio.run(run(open_dashboard=args.open_dashboard))
    except KeyboardInterrupt:
        print("Backend stopped by user.", flush=True)


if __name__ == "__main__":
    main()
