import hmac
import io
import json
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from execution.binance_testnet import Client, Journal, Session, VenueError, decimal

ROOT = Path(__file__).resolve().parents[1]
MARKET = {
    "symbol": "ALTUSDT", "baseAsset": "ALT", "quoteAsset": "USDT", "status": "TRADING",
    "isSpotTradingAllowed": True, "orderTypes": ["LIMIT"],
    "filters": [
        {"filterType": "LOT_SIZE", "minQty": "0.01", "maxQty": "10000", "stepSize": "0.01"},
        {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "10000", "tickSize": "0.01"},
        {"filterType": "NOTIONAL", "minNotional": "5", "maxNotional": "50000"},
    ],
}


class FakeBinance:
    def __init__(self):
        self.calls = []
        self.orders = {}
        self.fail = False
        self.unmatched = False
        self.delay_fills = False
        self.submit_status = "FILLED"

    @staticmethod
    def http_error(url, status, code):
        return HTTPError(url, status, "stub", {}, io.BytesIO(json.dumps({"code": code, "msg": "hidden"}).encode()))

    def __call__(self, request, timeout):
        url = urlsplit(request.full_url)
        assert url.scheme == "https" and url.netloc == "testnet.binance.vision"
        query = {key: value[0] for key, value in parse_qs(url.query).items()}
        method = request.get_method()
        path = url.path
        self.calls.append((method, path, query))
        if path in {"/api/v3/order", "/api/v3/account", "/api/v3/myTrades"}:
            signed_query, signature = url.query.rsplit("&signature=", 1)
            assert signature == hmac.new(b"test-secret", signed_query.encode(), sha256).hexdigest()
            assert query["timestamp"] == "2000"
            assert request.get_header("X-mbx-apikey") == "test-key"
        if path == "/api/v3/time":
            result = {"serverTime": 2000}
        elif path == "/api/v3/exchangeInfo":
            result = {"symbols": [MARKET]}
        elif path == "/api/v3/account":
            result = {"canTrade": True, "balances": [{"asset": "USDT", "free": "10000"}, {"asset": "ALT", "free": "10000"}]}
        elif path == "/api/v3/myTrades":
            result = []
            if not self.delay_fills:
                order = next(order for order in self.orders.values() if order["orderId"] == int(query["orderId"]))
                trade = {"orderId": order["orderId"], "id": order["orderId"] + 1000, "qty": order["executedQty"],
                         "quoteQty": str(decimal(order["executedQty"]) * decimal("10")), "price": "10",
                         "commission": "0.01", "commissionAsset": "ALT" if order["side"] == "BUY" else "USDT"}
                result = [trade, trade]
        elif path == "/api/v3/order" and method == "POST":
            assert query["timeInForce"] == "IOC" and query["type"] == "LIMIT"
            identifier = query["newClientOrderId"]
            executed = (query["quantity"] if self.submit_status == "FILLED"
                        else "0.59" if self.submit_status == "EXPIRED" else "0")
            result = {"symbol": query["symbol"], "side": query["side"], "clientOrderId": identifier,
                      "orderId": 123 + len(self.orders), "status": self.submit_status, "executedQty": executed}
            self.orders[identifier] = result
            if self.fail:
                raise TimeoutError()
        elif path == "/api/v3/order":
            if self.unmatched:
                raise self.http_error(request.full_url, 400, -2013)
            result = self.orders.get(query.get("origClientOrderId"))
            if result is None:
                result = next(order for order in self.orders.values() if order["orderId"] == int(query["orderId"]))
            if method == "DELETE":
                result.update(status="CANCELED", clientOrderId="new-cancel-id")
        else:
            result = {}
        return io.BytesIO(json.dumps(result).encode())


class BinanceTestnetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.api = FakeBinance()
        self.client = Client("test-key", "test-secret", opener=self.api, now_ms=lambda: 1_000)
        self.journal = Journal(self.path, "test-key")
        self.addCleanup(lambda: self.journal.close())
        self.session = Session(self.client, self.journal, decimal(25))

    def plan(self, key="LNU-T-probe", size="1.00", side="BUY", stop="9.90"):
        rows = self.journal.rows()
        planned = self.session.preflight("ALTUSDT", side, decimal(size), decimal(10), key, rows,
                                         stop=decimal(stop) if side == "BUY" and stop is not None else None)
        return planned, rows

    def test_timeout_restart_lookup_and_partial_expiry_do_not_resend(self):
        self.api.fail = True
        self.api.submit_status = "EXPIRED"
        order = self.session.submit(*self.plan())
        self.assertEqual(order["status"], "PENDING_RECONCILIATION")
        self.journal.close()
        self.journal = Journal(self.path, "test-key")
        self.session = Session(self.client, self.journal, decimal(25))
        self.api.unmatched = True
        self.assertEqual(self.session.reconcile("LNU-T-probe")["status"], "PENDING_RECONCILIATION")
        with self.assertRaisesRegex(ValueError, "already journaled"):
            self.plan()
        self.api.unmatched = False
        self.api.delay_fills = True
        pending = self.session.reconcile("LNU-T-probe")
        self.assertEqual(pending["order_id"], 123)
        self.assertEqual(pending["status"], "PENDING_RECONCILIATION")
        self.api.delay_fills = False
        final = self.session.reconcile("LNU-T-probe")
        self.assertEqual(final["status"], "EXPIRED")
        self.assertEqual(len(final["fills"]), 1)
        self.assertEqual(final["fills"][0]["qty"], "0.59")
        with self.assertRaisesRegex(ValueError, "must reduce"):
            self.plan("LNU-T-excess", "0.59", "SELL")
        self.assertEqual(sum(method == "POST" for method, _, _ in self.api.calls), 1)

    def test_signed_submit_sell_and_cancel(self):
        buy = self.session.submit(*self.plan())
        self.assertEqual(buy["status"], "FILLED")
        self.assertLessEqual(buy["estimated_stop_loss"], 0.25)
        with self.assertRaisesRegex(ValueError, "close the testnet position"):
            self.plan("LNU-T-second")
        self.api.submit_status = "NEW"
        sell = self.session.submit(*self.plan("LNU-T-close", size="0.99", side="SELL"))
        self.assertEqual(sell["status"], "NEW")
        canceled = self.session.cancel("LNU-T-close")
        self.assertEqual(canceled["status"], "CANCELED")
        self.assertEqual(canceled["order_id"], sell["order_id"])

    def test_preflight_blocks_caps_filters_and_wrong_journal(self):
        with self.assertRaisesRegex(ValueError, "position cap"):
            self.plan(size="1.51")
        with self.assertRaisesRegex(ValueError, "LOT_SIZE"):
            self.plan(size="0.991")
        with self.assertRaisesRegex(ValueError, "notional"):
            self.plan(size="0.25")
        with self.assertRaisesRegex(ValueError, "requires --stop-price"):
            self.plan(stop=None)
        with self.assertRaisesRegex(ValueError, "risk-sized maximum"):
            self.plan(stop="9.60")
        with self.assertRaisesRegex(ValueError, "wider than the risk cap"):
            self.plan(stop="9")
        with self.assertRaisesRegex(ValueError, "another API key"):
            Journal(self.path, "another")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.client.request("GET", "@example.com")
        self.assertFalse(any(method == "POST" for method, _, _ in self.api.calls))

    def test_cli_requires_confirmation_and_public_denial_has_no_signed_retry(self):
        process = subprocess.run([sys.executable, str(ROOT / "backend_trading_core" / "binance_testnet.py"),
                                  "submit", "ALTUSDT", "BUY", "1", "10", "LNU-T-probe"],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 2)
        self.assertIn("--confirm-testnet", process.stderr)

        def denied(request, timeout):
            self.assertEqual(urlsplit(request.full_url).netloc, "testnet.binance.vision")
            self.assertFalse(request.header_items())
            raise FakeBinance.http_error(request.full_url, 451, 0)

        with self.assertRaisesRegex(VenueError, "HTTP 451"):
            Client("test-key", "test-secret", opener=denied).request("GET", "/api/v3/ping")
