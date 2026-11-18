"""Windows-friendly entrypoint for the local paper server and offline demo."""
from __future__ import annotations

import argparse
import asyncio
import logging

from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="run the offline replay and write an HTML report")
    parser.add_argument("--output", type=Path, default=Path("demo_output"))
    parser.add_argument("--equity-csv", type=Path, help="replace sample equity data with raw XNYS daily data")
    parser.add_argument("--equity-symbol", default="DEMO")
    args = parser.parse_args()
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
        asyncio.run(run())
    except KeyboardInterrupt:
        print("Backend stopped by user.", flush=True)


if __name__ == "__main__":
    main()
