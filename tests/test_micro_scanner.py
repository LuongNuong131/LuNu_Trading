import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core.event_engine import Event
from core.micro import micro_limits
from core.models import Instrument, OrderIntent, Signal
from core.order_executor import PaperExecutor
from data_pipeline.bars import REPLAY_CRYPTO, utc_ms
from data_pipeline.fixtures import crypto_fixture
from data_pipeline.scanner import Quote, ScanSettings, SpotScanner

NOW = datetime(2025, 1, 2, tzinfo=timezone.utc)


def ticker(bid=9.99, ask=10.01, volume=300_000, **extra):
    return {"bid": bid, "ask": ask, "quoteVolume": volume, **extra}


def market(base, *, spot=True, active=True, quote="USD"):
    return {"base": base, "quote": quote, "spot": spot, "active": active}


class ScannerTests(unittest.TestCase):
    def test_quote_validation_and_stale_timestamps(self):
        for bad in (ticker(bid=11), ticker(ask=float("nan")), ticker(bid=None), ticker(timestamp="bad")):
            self.assertIsNone(Quote.from_ticker(bad, NOW))
        raw = ticker(timestamp=(NOW - timedelta(seconds=46)).timestamp() * 1000)
        self.assertFalse(Quote.from_ticker(raw, NOW).fresh(NOW, 45))
        raw = ticker(quoteVolume=None, baseVolume=100)
        self.assertEqual(Quote.from_ticker(raw, NOW).volume, 1_000)

    def test_bounded_rotation_prioritizes_holds_and_filters_markets(self):
        markets = {f"{s}/USD": market(s) for s in ("A", "B", "C", "NEW", "WIDE", "USDC", "A3L")}
        markets["OFF/USD"] = market("OFF", active=False)
        markets["FUT/USD"] = market("FUT", spot=False)
        quotes = {symbol: ticker() for symbol in markets}
        quotes["A/USD"] = ticker(volume=500_000)
        quotes["NEW/USD"] = ticker(volume=1)
        quotes["WIDE/USD"] = ticker(bid=8)
        scanner = SpotScanner(ScanSettings("USD", batch_size=2))
        first, _, summary = scanner.select(markets, quotes, NOW, {"OLD/USD"})
        self.assertEqual(first, ["OLD/USD", "A/USD"])
        self.assertEqual(summary["universe"], 7)
        self.assertEqual(summary["eligible"], 3)
        self.assertEqual(summary["excluded"], {"stable or leveraged base": 2, "low 24h quote volume": 1,
                                               "spread too wide": 1})
        scanner.record_attempts(first)
        second, _, _ = scanner.select(markets, quotes, NOW, {"OLD/USD"})
        scanner.record_attempts(second)
        third, _, _ = scanner.select(markets, quotes, NOW, {"OLD/USD"})
        self.assertEqual(second, ["OLD/USD", "B/USD"])
        self.assertEqual(third, ["OLD/USD", "C/USD"])

    def test_batched_feed_reads_only_selected_and_reports_bad_candles(self):
        from data_pipeline.market_feed import MarketFeed

        class Exchange:
            markets = {f"{s}/USD": market(s) for s in ("A", "B", "C")}
            has = {"fetchTickers": True}
            requests = 0

            async def fetch_tickers(self):
                self.requests += 1
                return {symbol: ticker() for symbol in self.markets}

        async def run():
            feed = MarketFeed("test", scan_settings=ScanSettings("USD", batch_size=2))
            feed.exchange = Exchange()
            feed._markets_loaded_at = asyncio.get_running_loop().time()
            requests = []

            async def read_bar(symbol):
                requests.append(symbol)
                if symbol == "B/USD":
                    raise ValueError("missing or misaligned OHLCV bars")
                return {"bars": None}

            with patch.object(feed, "_read_bar", read_bar):
                with patch("data_pipeline.market_feed.event_engine.put", return_value=True) as publish:
                    await feed._scan_once(set())
            payload = publish.call_args.args[0].data
            self.assertEqual(feed.exchange.requests, 1)
            self.assertEqual(requests, ["A/USD", "B/USD"])
            self.assertEqual(payload["summary"]["completed"], 1)
            self.assertIn("B/USD", payload["errors"])

        asyncio.run(run())


class MicroPaperTests(unittest.TestCase):
    def test_risk_rejects_unaffordable_orders_and_sizes_stop_including_costs(self):
        instrument = Instrument("ALT/USD", "test", "USD", "crypto spot", "1m", 0.01, 0.01, 0.01, 5)
        for capital in (10, 25, 50):
            with self.subTest(capital=capital):
                account = PaperExecutor(micro_limits(capital), currency="USD")
                intent = OrderIntent(instrument.key, Signal.BUY, 10, 0.1, "test", NOW,
                                     instrument=instrument, spread_bps=12)
                decision = account.execute(intent)
                if capital == 10:
                    self.assertEqual(decision.reason, "size below minimum notional")
                    self.assertEqual(account.capital, 10)
                    continue
                self.assertTrue(decision.approved)
                order = account.orders[decision.client_order_id]
                stop_fill = decision.stop_loss * (1 - 0.0016)
                stop_fill = int(stop_fill * 100) / 100
                loss_per_unit = order["price"] - stop_fill + (order["price"] + stop_fill) * 0.004
                self.assertAlmostEqual(decision.estimated_stop_loss, loss_per_unit * decision.amount)
                self.assertLessEqual(decision.estimated_stop_loss, capital * 0.01)
                self.assertLessEqual(decision.notional, capital * 0.60)

    def test_missing_quote_cannot_increase_exposure(self):
        account = PaperExecutor(micro_limits(25), currency="USD")
        decision = account.execute(OrderIntent("test:ALT/USD", Signal.BUY, 10, 0.1, "test", NOW))
        self.assertFalse(decision.approved)
        self.assertIn("fresh bid/ask", decision.reason)
        self.assertEqual(account.equity, 25)

    def test_existing_default_ledger_loads_without_new_limits_field(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.sqlite3"
            account = PaperExecutor(path=path)
            account.shutdown()
            with sqlite3.connect(path) as db:
                state = json.loads(db.execute("SELECT state FROM paper_accounts").fetchone()[0])
                state["limits"].pop("include_costs_in_risk")
                db.execute("UPDATE paper_accounts SET state = ?", (json.dumps(state),))
            db.close()
            restored = PaperExecutor(path=path)
            try:
                self.assertEqual(restored.capital, 10_000)
            finally:
                restored.shutdown()

    def test_scan_prioritizes_tighter_executable_setup_and_preserves_one_wallet(self):
        from core.runtime import PaperRuntime
        from db.duckdb_client import DuckDBClient
        from demo.report import render_report

        bars = crypto_fixture().iloc[:38].copy()
        bars[["open", "high", "low", "close"]] /= 1_000
        now = utc_ms(bars["closed_ms"].iloc[-1]) + timedelta(seconds=2)
        worse, better, stale, narrow = instruments = [
            replace(REPLAY_CRYPTO, venue="test", symbol=f"{base}/USD", currency="USD",
                    minimum_notional=5, tick=0.01) for base in ("WIDE", "TIGHT", "STALE", "COST")
        ]
        quotes = [Quote(59, 8, 900_000, now), Quote(59, 2, 400_000, now),
                  Quote(59, 1, 900_000, now - timedelta(seconds=46)), Quote(59, 29, 500_000, now)]
        readings = [{"instrument": instrument, "bars": bars, "quote": quote}
                    for instrument, quote in zip(instruments, quotes)]
        shallow_bars = readings[-1]["bars"].copy()
        # Keep candle direction and sweep, while making the ATR too small to clear round-trip costs.
        for field in ("open", "high", "low", "close"):
            shallow_bars[field] = 59 + (shallow_bars[field] - 59) / 4
        readings[-1]["bars"] = shallow_bars
        settings = ScanSettings("USD")
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "MARKET_SCAN_QUOTE": "USD", "PAPER_MICRO_CAPITAL": "25", "PAPER_TAKER_FEE_BPS": "40",
            "PAPER_KILL_SWITCH": "false",
        }):
            db = DuckDBClient(Path(temp) / "micro.sqlite3")
            runtime = PaperRuntime(db)
            runtime.configure("test", [], True, "USD")
            event = Event("SCAN_BATCH", {"readings": readings, "errors": {}, "exchange": "test",
                                        "summary": {"quote": "USD", "status": "ready"}, "settings": settings,
                                        "observed_at": now})
            try:
                asyncio.run(runtime.handle_scan(event))
                asyncio.run(runtime.handle_scan(event))
                snapshot = runtime.snapshot(include_signals=True)
                self.assertEqual(len(snapshot["accounts"]), 1)
                account = snapshot["accounts"][0]
                orders = {item["symbol"]: item for item in account["orders"]}
                self.assertEqual(orders[better.key]["status"], "FILLED")
                self.assertEqual(orders[worse.key]["decision"]["reason"], "max open positions reached")
                self.assertIn("fresh bid/ask", orders[stale.key]["decision"]["reason"])
                self.assertEqual(orders[narrow.key]["decision"]["reason"],
                                 "modeled target too small versus round-trip costs")
                self.assertEqual(runtime.priority_symbols("test"), {"TIGHT/USD"})
                self.assertEqual(len(snapshot["signals"]), len(instruments))
                self.assertIn("Quét spot", render_report(snapshot))
            finally:
                for account in runtime.accounts.values():
                    account.shutdown()
                db.conn.close()


if __name__ == "__main__":
    unittest.main()
