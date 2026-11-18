"""Manually exercise Binance Spot Testnet with a separate local journal."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from decimal import DecimalException
from pathlib import Path

from execution.binance_testnet import Client, Journal, Session, VenueError, decimal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=Path("data/binance-spot-testnet.sqlite3"))
    parser.add_argument("--budget", default="25", help="testnet USDT wallet budget, 10 to 50")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("ping", help="probe the public testnet host without credentials")
    actions.add_parser("status", help="list journal entries without submitting orders")
    for name in ("preview", "submit"):
        action = actions.add_parser(name, help="validate testnet spot filters and wallet caps")
        action.add_argument("symbol")
        action.add_argument("side", choices=("BUY", "SELL"))
        action.add_argument("quantity")
        action.add_argument("limit_price")
        action.add_argument("client_id")
        action.add_argument("--stop-price", help="sizing input for BUY; no protective stop is sent")
        if name == "submit":
            action.add_argument("--confirm-testnet", action="store_true", required=True)
    actions.add_parser("reconcile").add_argument("client_id")
    cancel = actions.add_parser("cancel")
    cancel.add_argument("client_id")
    cancel.add_argument("--confirm-testnet", action="store_true", required=True)
    args = parser.parse_args()
    key = os.getenv("BINANCE_TESTNET_API_KEY", "")
    secret = os.getenv("BINANCE_TESTNET_API_SECRET", "")
    journal = None
    try:
        client = Client(key, secret)
        if args.action == "ping":
            client.request("GET", "/api/v3/ping")
            result = {"mode": "Binance Spot Testnet", "status": "reachable"}
        else:
            if not key or not secret:
                raise ValueError("set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET in the process environment")
            journal = Journal(args.journal, key)
            session = Session(client, journal, decimal(args.budget))
            if args.action == "status":
                result = journal.rows()
            elif args.action in {"preview", "submit"}:
                previous = journal.rows()
                planned = session.preflight(args.symbol.upper(), args.side, decimal(args.quantity),
                                            decimal(args.limit_price), args.client_id, previous,
                                            stop=decimal(args.stop_price) if args.stop_price else None)
                result = session.submit(planned, previous) if args.action == "submit" else {**planned, "status": "PREVIEW"}
            else:
                action = session.reconcile if args.action == "reconcile" else session.cancel
                result = action(args.client_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (VenueError, ValueError, OSError, sqlite3.Error, DecimalException, KeyError, IndexError, TypeError) as exc:
        message = str(exc) if isinstance(exc, (VenueError, ValueError)) else type(exc).__name__
        parser.exit(2, f"Testnet check failed: {message}\n")
    finally:
        if journal is not None:
            journal.close()


if __name__ == "__main__":
    main()
