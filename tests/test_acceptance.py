import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from backtest.runner import run_backtest
from core.event_engine import Event
from core.models import Instrument, OrderIntent, RiskLimits, Signal
from core.order_executor import Fill, OrderReport, PaperExecutor
from data_pipeline.bars import COLUMNS, REPLAY_CRYPTO, REPLAY_EQUITY, finalized_crypto, read_equity_csv
from data_pipeline.fixtures import crypto_fixture, equity_fixture_csv
from demo.runner import SAMPLE
from factor_zoo.technical import calculate_rsi, get_technical_context

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2025, 1, 2, 12, tzinfo=timezone.utc)


def intent(symbol, action, price, tag, **kwargs):
    return OrderIntent(symbol, action, price, 1.0, "test", kwargs.pop("now", NOW), signal_id=tag, **kwargs)


class FakeBroker:
    def __init__(self):
        self.submissions = 0
        self.lookups = 0
        self.reports = {}
        self.submit_report = OrderReport("OPEN")
        self.timeout = False

    def submit(self, order):
        self.submissions += 1
        self.reports[order["client_order_id"]] = self.submit_report
        if self.timeout:
            raise TimeoutError("reply lost after acceptance")
        return self.submit_report

    def lookup(self, order):
        self.lookups += 1
        return self.reports.get(order["client_order_id"])

    def cancel(self, order):
        previous = self.lookup(order)
        return OrderReport("CANCELED", previous.fills if previous else (), "canceled by test")


class LedgerTests(unittest.TestCase):
    def test_partial_cancel_and_sales_match_independent_accounting(self):
        limits = RiskLimits(starting_capital=1000, fee_rate=0.01, slippage_bps=0)
        broker = FakeBroker()
        account = PaperExecutor(limits, broker=broker)
        buy = account.execute(intent("A", Signal.BUY, 10, "buy"))
        first = Fill("buy-1", 2, 10, 0.20)
        second = Fill("buy-2", 1, 12, 0.12)
        account.apply_report(buy.client_order_id, OrderReport("OPEN", (first, first, second)))
        self.assertEqual(account.orders[buy.client_order_id]["status"], "PARTIALLY_FILLED")
        self.assertAlmostEqual(account.capital, 967.68)
        self.assertAlmostEqual(account.positions["A"].entry_price, 32 / 3)
        account.set_kill_switch(True)
        account.cancel(buy.client_order_id)
        self.assertEqual(account.orders[buy.client_order_id]["status"], "CANCELED")
        account.update_price("A", 15, now=NOW, trigger_exits=False)
        sale = account.execute(intent("A", Signal.SELL, 15, "sell"))
        self.assertTrue(sale.approved)
        fill = Fill("sell-1", 1, 15, 0.15)
        account.apply_report(sale.client_order_id, OrderReport("PARTIALLY_FILLED", (fill,)))
        self.assertAlmostEqual(account.capital, 982.53)
        self.assertAlmostEqual(account.realized_pnl, 14.85 - 32.32 / 3)
        account.apply_report(sale.client_order_id, OrderReport("FILLED", (fill, Fill("sell-2", 2, 15, 0.30))))
        self.assertAlmostEqual(account.capital, 1012.23)
        self.assertAlmostEqual(account.realized_pnl, 12.23)
        self.assertAlmostEqual(account.fees, 0.77)
        self.assertEqual(account.positions, {})

    def test_timeout_restart_and_same_bar_do_not_resubmit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ledger.sqlite3"
            broker = FakeBroker()
            broker.timeout = True
            entry = intent("A", Signal.BUY, 100, "timeout")
            first = PaperExecutor(path=path, broker=broker)
            decision = first.execute(entry)
            key = decision.client_order_id
            self.assertEqual(first.orders[key]["status"], "PENDING_RECONCILIATION")
            self.assertEqual(first.capital, 10000)
            first.set_kill_switch(True)
            first.shutdown()
            broker.reports.pop(key)
            restored = PaperExecutor(path=path, broker=broker)
            try:
                self.assertTrue(restored.killed)
                self.assertEqual(restored.risk.state.day, NOW.date())
                restored.reconcile()
                self.assertEqual(restored.orders[key]["status"], "PENDING_RECONCILIATION")
                self.assertEqual(restored.execute(entry).client_order_id, key)
                self.assertEqual(broker.submissions, 1)
                pending = restored.orders[key]
                fill = Fill("timeout-fill", pending["amount"], pending["price"],
                            pending["amount"] * pending["price"] * pending["fee_rate"])
                broker.reports[key] = OrderReport("FILLED", (fill,))
                restored.reconcile()
                restored.apply_report(key, broker.reports[key])
                self.assertEqual(restored.risk.state.high_water_mark, 10000)
                self.assertEqual(len(restored.orders[key]["fills"]), 1)
                self.assertAlmostEqual(restored.capital,
                                       10000 - pending["amount"] * pending["price"] - fill.fee)
            finally:
                restored.shutdown()
            again = PaperExecutor(path=path)
            try:
                self.assertAlmostEqual(again.positions["A"].amount, decision.amount)
                self.assertEqual(again.orders[key]["status"], "FILLED")
            finally:
                again.shutdown()

    def test_simultaneous_duplicate_intent_one_broker_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ledger.sqlite3"
            broker = FakeBroker()
            accounts = [PaperExecutor(path=path, broker=broker), PaperExecutor(path=path, broker=broker)]
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    ids = list(pool.map(lambda a: a.execute(intent("A", Signal.BUY, 100, "shared")).client_order_id,
                                        accounts))
                self.assertEqual(ids[0], ids[1])
                self.assertEqual(broker.submissions, 1)
            finally:
                for account in accounts:
                    account.shutdown()

    def test_broker_rejection_releases_all_reservations(self):
        broker = FakeBroker()
        broker.submit_report = OrderReport("REJECTED", reason="venue rejected")
        account = PaperExecutor(broker=broker)
        decision = account.execute(intent("A", Signal.BUY, 100, "first"))
        self.assertTrue(decision.approved)
        order = account.orders[decision.client_order_id]
        self.assertEqual(order["status"], "REJECTED")
        self.assertEqual(order["broker_reason"], "venue rejected")
        self.assertEqual(account.capital, 10000)
        self.assertEqual(account.positions, {})
        self.assertTrue(account.execute(intent("A", Signal.BUY, 100, "second")).approved)

    def test_risk_freshness_loss_limits_and_reduce_only(self):
        acc = PaperExecutor(RiskLimits(max_drawdown_pct=0.05))
        stale = intent("A", Signal.BUY, 100, "stale", known_at=NOW - timedelta(seconds=181), max_age_seconds=180)
        self.assertEqual(acc.execute(stale).reason, "market data stale")
        self.assertTrue(acc.execute(intent("A", Signal.BUY, 100, "buy")).approved)
        acc.update_price("A", 50, now=NOW, trigger_exits=False)
        self.assertEqual(acc.execute(intent("B", Signal.BUY, 100, "loss")).reason, "daily loss limit reached")
        tomorrow = NOW + timedelta(days=1)
        self.assertEqual(acc.execute(intent("B", Signal.BUY, 100, "dd", now=tomorrow)).reason,
                         "maximum drawdown limit reached")
        acc.set_kill_switch(True)
        self.assertEqual(acc.execute(intent("B", Signal.BUY, 100, "kill", now=tomorrow)).reason, "kill switch active")
        self.assertEqual(acc.execute(replace(stale, signal=Signal.SELL, signal_id="stale-sale")).reason, "market data stale")
        self.assertTrue(acc.execute(intent("A", Signal.SELL, 50, "sell", now=tomorrow)).approved)
        self.assertEqual(acc.execute(intent("B", Signal.SELL, 50, "short", now=tomorrow)).reason,
                         "no long position to close")

    def test_cash_reserve_and_price_quantity_precision(self):
        market = Instrument("A/USD", "TEST", "USD", "crypto spot", "1m", 0.1, 0.01, 0.02, 0.5)
        limits = RiskLimits(starting_capital=100, risk_per_trade=1, max_position_notional_pct=1,
                            max_total_exposure_pct=1, fee_rate=0.01)
        acc = PaperExecutor(limits, currency="USD")
        decision = acc.execute(intent(market.key, Signal.BUY, 10.04, "precision", instrument=market))
        order = acc.orders[decision.client_order_id]
        self.assertEqual(order["price"], 10.1)
        self.assertEqual(order["amount"], 9.8)
        self.assertAlmostEqual(acc.capital, 100 - 9.8 * 10.1 * 1.01)
        self.assertGreaterEqual(acc.capital, 0)


class MarketAndReplayTests(unittest.TestCase):
    def test_indicator_warmup_and_reference_values(self):
        self.assertTrue(pd.isna(calculate_rsi(pd.Series(range(14))).iloc[-1]))
        self.assertEqual(calculate_rsi(pd.Series(range(40))).iloc[-1], 100)
        self.assertEqual(calculate_rsi(pd.Series(range(40, 0, -1))).iloc[-1], 0)
        frame = pd.DataFrame({"open": [100] * 35, "high": [102] * 35, "low": [98] * 35,
                              "close": [100] * 35, "volume": [1] * 35})
        context = get_technical_context(frame)
        self.assertEqual((context["rsi"], context["atr"], context["macd_hist"]), (50, 4, 0))
        self.assertFalse(context["liquidity_sweep_bullish"])
        self.assertFalse(get_technical_context(frame.iloc[:-1])["ready"])

    def test_future_prices_cannot_change_past_signal_or_fill(self):
        frame = crypto_fixture().iloc[:90]
        base = run_backtest(frame, instrument=REPLAY_CRYPTO, keep_trace=True, liquidate=False)
        more = crypto_fixture().iloc[:120].copy()
        more.loc[90:, ["open", "high", "low", "close"]] *= 2
        changed = run_backtest(more, instrument=REPLAY_CRYPTO, keep_trace=True, liquidate=False)
        self.assertEqual(base["events"], changed["events"][:len(base["events"])])
        bought = next(event for event in base["events"] if event.get("risk", {}).get("approved"))
        self.assertEqual(bought["closed_at"], bought["execution_at"])
        assert_order = next(order for order in base["account"]["orders"]
                            if order["client_order_id"] == bought["risk"]["client_order_id"])
        self.assertGreater(assert_order["price"], bought["quote"])

    def test_crypto_validation_rejects_gaps_bad_prices_and_duplicate_rows(self):
        rows = crypto_fixture().iloc[:4][COLUMNS].to_numpy().tolist()
        now = datetime.fromtimestamp(rows[-1][0] / 1000, timezone.utc)
        self.assertEqual(len(finalized_crypto(rows, now)), 3)
        for mutation in (rows[:1] + rows[2:], rows[:2] + rows[1:], list(reversed(rows)),
                         [[rows[0][0], 100, 1, 90, 100, 1], *rows[1:]]):
            with self.subTest(rows=mutation), self.assertRaises(ValueError):
                finalized_crypto(mutation, now)

    def test_xnys_csv_validates_holidays_dst_early_close_and_affordable_fill(self):
        frame = read_equity_csv(SAMPLE)
        fixture = equity_fixture_csv()
        self.assertEqual(fixture.loc[fixture["session"] == "2024-11-29", "closed_at_utc"].item(),
                         "2024-11-29T18:00:00Z")
        self.assertNotIn("2024-11-28", fixture["session"].tolist())
        self.assertTrue(fixture.iloc[0]["closed_at_utc"].endswith("20:00:00Z"))
        self.assertTrue(fixture.loc[fixture["session"] == "2024-11-04", "closed_at_utc"].item().endswith("21:00:00Z"))
        frame[["open", "high", "low", "close"]] /= 50
        result = run_backtest(frame, instrument=REPLAY_EQUITY, keep_trace=True)
        self.assertGreater(result["fills"], 0)
        self.assertEqual(result["account"]["currency"], "USD")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.csv"
            missing_session = fixture.drop(index=1)
            missing_session.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "missing session"):
                read_equity_csv(path)

    def test_offline_entrypoint_reproducible_and_blocks_external_network(self):
        command = """
import runpy, sys
def no_network(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto'):
        raise RuntimeError('network access in offline demo')
sys.addaudithook(no_network)
sys.argv = [sys.argv[1], '--demo', '--output', sys.argv[2]]
sys.path.insert(0, str(__import__('pathlib').Path(sys.argv[0]).parent))
runpy.run_path(sys.argv[0], run_name='__main__')
"""
        with tempfile.TemporaryDirectory() as temp:
            entry = ROOT / "backend_trading_core" / "run_backend.py"
            outputs = [Path(temp) / str(index) for index in range(2)]
            env = {key: val for key, val in os.environ.items() if "API_KEY" not in key and key != "PYTHONPATH"}
            for path in outputs:
                subprocess.run([sys.executable, "-c", command, str(entry), str(path)], cwd=temp,
                               env=env, capture_output=True, check=True, timeout=90)
            content = [(path / "snapshot.json").read_bytes() for path in outputs]
            self.assertEqual(*content)
            snapshot = json.loads(content[0])
            crypto, equity = snapshot["accounts"]
            self.assertTrue(any(order["status"] == "FILLED" for order in crypto["orders"]))
            self.assertTrue(any(order["decision"]["reason"] == "size below minimum lot/quantity"
                                for order in equity["orders"]))
            report = (outputs[0] / "report.html").read_text(encoding="utf-8")
            self.assertIn(crypto["orders"][0]["client_order_id"], report)
            self.assertIn("Chỉ kiểm tra chức năng", report)

    def test_legacy_api_websocket_and_paper_runtime_use_actual_state(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "OMNI_QUANT_DB_PATH": str(Path(temp) / "api.sqlite3")
        }):
            from api.fastapi_gateway import app, db_client, runtime
            try:
                frame = crypto_fixture().iloc[:80]
                now = datetime.fromtimestamp(frame["closed_ms"].iloc[-1] / 1000, timezone.utc)
                instrument = replace(REPLAY_CRYPTO, venue="fake")
                runtime.configure("fake", [instrument.symbol], True)
                event = Event("NEW_BAR", {"instrument": instrument, "bars": frame, "observed_at": now})
                asyncio.run(runtime.handle_new_bar(event))
                asyncio.run(runtime.handle_new_bar(event))
                asyncio.run(runtime.feed_error(Event("FEED_ERROR", {"symbol": instrument.key, "error": "simulated"})))
                with TestClient(app) as client:
                    for route in ("/", "/health", "/api/stats", "/api/positions", "/api/logs", "/api/trades", "/api/audit",
                                  "/api/snapshot", "/dashboard"):
                        self.assertEqual(client.get(route).status_code, 200, route)
                    snapshot = client.get("/api/snapshot").json()["data"]
                    self.assertEqual(snapshot["watchlist"][0]["status"], "error")
                    self.assertEqual(len(snapshot["signals"]), 1)
                    with client.websocket_connect("/ws/snapshot") as websocket:
                        message = websocket.receive_json()
                        self.assertEqual(message["type"], "snapshot")
                        self.assertEqual(message["data"]["capital"], snapshot["accounts"][0]["capital"])
            finally:
                for account in runtime.accounts.values():
                    account.shutdown()
                db_client.conn.close()


if __name__ == "__main__":
    unittest.main()
