"""Manual Binance Spot Testnet orders; the only configured host is the sandbox."""
from __future__ import annotations

import hmac
import json
import re
import sqlite3
import time
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from core.micro import micro_limits
from core.models import Instrument, OrderIntent, Position, Side, Signal, utc_now
from core.risk_manager import RiskManager

TESTNET = "https://testnet.binance.vision"
TERMINAL = {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH"}
VALID_STATUS = TERMINAL | {"NEW", "PARTIALLY_FILLED", "PENDING_NEW", "PENDING_CANCEL"}
ZERO = Decimal("0")
ROUTES = {
    ("GET", "/api/v3/ping"): False,
    ("GET", "/api/v3/time"): False,
    ("GET", "/api/v3/exchangeInfo"): False,
    ("GET", "/api/v3/account"): True,
    ("GET", "/api/v3/myTrades"): True,
    ("GET", "/api/v3/order"): True,
    ("POST", "/api/v3/order"): True,
    ("DELETE", "/api/v3/order"): True,
}


class VenueError(RuntimeError):
    def __init__(self, status: int, code: int | None = None):
        super().__init__(f"Binance Spot Testnet HTTP {status}" + (f" code {code}" if code is not None else ""))
        self.status = status
        self.code = code


class UnknownResult(VenueError):
    """The operation might have reached Binance."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, key="", secret="", *, opener=None, now_ms=None):
        self.key = key
        self._secret = secret.encode()
        self._open = opener if opener is not None else build_opener(NoRedirect()).open
        self._now = now_ms if now_ms is not None else lambda: time.time_ns() // 1_000_000
        self._offset = None

    def request(self, method, path, params=None, *, signed=False):
        if ROUTES.get((method, path)) is not signed:
            raise ValueError("unsupported Binance Spot Testnet route or authentication mode")
        fields = dict(params or {})
        if {"timestamp", "recvWindow", "signature"}.intersection(fields):
            raise ValueError("these request parameters are added by the testnet signer")
        headers = {}
        if signed:
            if not self.key or not self._secret:
                raise ValueError("Binance Spot Testnet credentials are required")
            if self._offset is None:
                start = self._now()
                server = int(self.request("GET", "/api/v3/time")["serverTime"])
                self._offset = server - (start + self._now()) // 2
            fields.update(timestamp=self._now() + self._offset, recvWindow=5000)
            signature = hmac.new(self._secret, urlencode(fields).encode(), sha256).hexdigest()
            fields["signature"] = signature
            headers["X-MBX-APIKEY"] = self.key
        suffix = "?" + urlencode(fields) if fields else ""
        request = Request(TESTNET + path + suffix, headers=headers, method=method)
        try:
            with self._open(request, timeout=10) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read())
                code = payload.get("code") if isinstance(payload, dict) else None
            except (OSError, ValueError):
                code = None
            code = code if isinstance(code, int) else None
            error = UnknownResult if exc.code >= 500 or exc.code in (408, 429) or code in (-1006, -1007) else VenueError
            raise error(exc.code, code) from None
        except (OSError, URLError, ValueError):
            raise UnknownResult(0) from None


class Journal:
    """Commit the client ID before any write to the exchange."""
    def __init__(self, path: Path, key: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        fingerprint = sha256(key.encode()).hexdigest()
        try:
            with self._db:
                if self._db.execute("SELECT 1 FROM sqlite_master WHERE name = 'paper_accounts'").fetchone():
                    raise ValueError("use a separate SQLite file for the testnet journal")
                self._db.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT)")
                self._db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (fingerprint,))
                if self._db.execute("SELECT owner FROM settings").fetchone()[0] != fingerprint:
                    raise ValueError("testnet journal belongs to another API key; use its own journal")
                self._db.execute("CREATE TABLE IF NOT EXISTS testnet_orders (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
        except BaseException:
            self._db.close()
            raise

    def close(self):
        self._db.close()

    def rows(self):
        return [json.loads(row[0]) for row in self._db.execute("SELECT state FROM testnet_orders ORDER BY rowid")]

    def reserve(self, planned, previous):
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if self.rows() != previous:
                raise ValueError("testnet journal changed during preflight; review current status")
            self._db.execute("INSERT INTO testnet_orders VALUES (?, ?)",
                             (planned["client_id"], json.dumps(planned)))

    def get(self, client_id):
        row = self._db.execute("SELECT state FROM testnet_orders WHERE id = ?", (client_id,)).fetchone()
        if row is None:
            raise ValueError("client ID is absent from the testnet journal")
        return json.loads(row[0])

    def update(self, row):
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            current = self.get(row["client_id"])
            if current["status"] not in TERMINAL:
                self._db.execute("UPDATE testnet_orders SET state = ? WHERE id = ?",
                                 (json.dumps(row), row["client_id"]))
                return row
            return current


def valid_client_id(value: str) -> str:
    if not re.fullmatch(r"LNU-T-[a-zA-Z0-9_-]{1,24}", value):
        raise ValueError("client ID must start with LNU-T- and use up to 24 letters, digits, hyphens or underscores after it")
    return value


def decimal(value) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("expected a finite decimal")
    return result


def _bounds(value: Decimal, minimum, maximum, step="0"):
    lo, hi, increment = decimal(minimum), decimal(maximum), decimal(step)
    return (not lo or value >= lo) and (not hi or value <= hi) and (not increment or value % increment == 0)


def _holdings(rows):
    coins = {}
    cashflow = ZERO
    for row in rows:
        if not row.get("fills"):
            continue
        base = row["base"]
        for fill in row["fills"]:
            side = 1 if row["side"] == "BUY" else -1
            fee = decimal(fill["commission"])
            base_fee = fee if fill["commissionAsset"] == base else ZERO
            quote_fee = fee if fill["commissionAsset"] == "USDT" else ZERO
            coins[base] = coins.get(base, ZERO) + side * decimal(fill["qty"]) - base_fee
            cashflow -= side * decimal(fill["quoteQty"]) + quote_fee
    return coins, cashflow


class Session:
    """One manual testnet slot and separate cash accounting per journal."""
    def __init__(self, client: Client, journal: Journal, budget: Decimal):
        if not budget.is_finite() or not 10 <= budget <= 50:
            raise ValueError("testnet budget must be between 10 and 50 USDT")
        self.client = client
        self.journal = journal
        self.budget = budget

    def preflight(self, symbol, side, quantity, price, client_id, rows, *, stop=None):
        valid_client_id(client_id)
        if not re.fullmatch(r"[A-Z0-9]{4,24}", symbol) or not symbol.endswith("USDT"):
            raise ValueError("only native Binance USDT spot symbols are supported by this test harness")
        if side not in {"BUY", "SELL"} or quantity <= 0 or price <= 0:
            raise ValueError("invalid side, quantity or limit price")
        if side == "BUY" and (stop is None or not ZERO < stop < price):
            raise ValueError("BUY requires --stop-price below the entry limit for risk sizing")
        if any(row["client_id"] == client_id for row in rows):
            raise ValueError("client ID already journaled; reconcile it instead")
        if any(row["status"] not in TERMINAL for row in rows):
            raise ValueError("reconcile every pending testnet order before entering another")
        if any(decimal(row["budget"]) != self.budget for row in rows):
            raise ValueError("testnet budget differs from existing journal")
        info = self.client.request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})["symbols"][0]
        if (info["symbol"] != symbol or info["status"] != "TRADING" or info["quoteAsset"] != "USDT"
                or not info.get("isSpotTradingAllowed") or "LIMIT" not in info["orderTypes"]):
            raise ValueError("market is unavailable for spot LIMIT orders")
        lot = next((rule for rule in info["filters"] if rule["filterType"] == "LOT_SIZE"), None)
        tick = next((rule for rule in info["filters"] if rule["filterType"] == "PRICE_FILTER"), None)
        notionals = [rule for rule in info["filters"] if rule["filterType"] in {"MIN_NOTIONAL", "NOTIONAL"}]
        if not lot or not tick or not notionals:
            raise ValueError("exchange did not report the required spot filters")
        if not _bounds(quantity, lot["minQty"], lot["maxQty"], lot["stepSize"]):
            raise ValueError("quantity outside LOT_SIZE or not aligned to step size")
        if not _bounds(price, tick["minPrice"], tick["maxPrice"], tick["tickSize"]):
            raise ValueError("price outside PRICE_FILTER or not aligned to tick size")
        cost = quantity * price
        if not all(_bounds(cost, rule["minNotional"], rule.get("maxNotional", "0")) for rule in notionals):
            raise ValueError("order outside exchange notional limits")
        account = self.client.request("GET", "/api/v3/account", signed=True)
        if not account.get("canTrade"):
            raise ValueError("testnet account cannot trade")
        free = {balance["asset"]: decimal(balance["free"]) for balance in account["balances"]}
        coins, cashflow = _holdings(rows)
        available = min(self.budget + cashflow, free.get("USDT", ZERO))
        base = info["baseAsset"]
        positions = {}
        if side == "BUY":
            if any(amount > 0 for amount in coins.values()):
                raise ValueError("close the testnet position tracked by this journal first")
            if cost > self.budget * Decimal("0.60") or cost * Decimal("1.004") > available:
                raise ValueError("order exceeds the 60% testnet position cap or available cash plus fee reserve")
        else:
            held = min(coins.get(base, ZERO), free.get(base, ZERO))
            if quantity > held:
                raise ValueError("sale must reduce a long acquired via this journal and covered by the testnet balance")
            positions[symbol] = Position(symbol, Side.BUY, float(price), float(held), 0.0, 0.0)
        instrument = Instrument(symbol, "binance-spot-testnet", "USDT", "crypto spot", "manual",
                                float(tick["tickSize"]), float(lot["stepSize"]), float(lot["minQty"]),
                                max(float(rule["minNotional"]) for rule in notionals))
        manager = RiskManager(micro_limits(float(self.budget)))
        now = utc_now()
        stop_price = float(stop) if side == "BUY" else None
        intent = OrderIntent(symbol, Signal(side), float(price), float(price - stop) if stop_price else 0.0,
                             "manual spot testnet", now, instrument=instrument, spread_bps=0.0,
                             structural_stop=stop_price)
        equity = float(min(self.budget, self.budget + cashflow))
        risk = manager.evaluate(intent, equity, positions, now, price=float(price), cash=float(available),
                                exposure=sum(p.notional for p in positions.values()), pending=set(), killed=False)
        if not risk.approved:
            raise ValueError(f"testnet risk rejected: {risk.reason}")
        if quantity > decimal(risk.amount):
            raise ValueError("quantity exceeds the risk-sized maximum")
        estimated_loss = (None if risk.estimated_stop_loss is None
                          else risk.estimated_stop_loss * float(quantity) / risk.amount)
        return {"client_id": client_id, "symbol": symbol, "side": side, "base": base,
                "quantity": format(quantity, "f"), "price": format(price, "f"),
                "budget": str(self.budget), "status": "PENDING_SUBMIT", "notional": str(cost),
                "risk_stop": risk.stop_loss, "estimated_stop_loss": estimated_loss}

    def submit(self, planned, previous):
        self.journal.reserve(planned, previous)
        params = {"symbol": planned["symbol"], "side": planned["side"], "type": "LIMIT", "timeInForce": "IOC",
                  "quantity": planned["quantity"], "price": planned["price"],
                  "newClientOrderId": planned["client_id"], "newOrderRespType": "RESULT"}
        try:
            order = self.client.request("POST", "/api/v3/order", params, signed=True)
        except UnknownResult as exc:
            return self.journal.update({**planned, "status": "PENDING_RECONCILIATION", "message": str(exc)})
        except VenueError as exc:
            # Binance may explicitly return a duplicate client ID. Preserve uncertainty on order endpoint rejects.
            status = "PENDING_RECONCILIATION" if exc.code == -2010 else "REJECTED"
            return self.journal.update({**planned, "status": status, "message": str(exc)})
        return self._record(planned, order)

    def _record(self, row, order):
        if not isinstance(order, dict) or order.get("symbol") != row["symbol"] or order.get("status") not in VALID_STATUS:
            return self.journal.update({**row, "status": "PENDING_RECONCILIATION", "message": "unexpected order response"})
        order_id = order.get("orderId")
        known = row.get("order_id")
        matching = (order_id == known if known is not None
                    else row["client_id"] in (order.get("clientOrderId"), order.get("origClientOrderId")))
        if not isinstance(order_id, int) or not matching or order.get("side", row["side"]) != row["side"]:
            return self.journal.update({**row, "status": "PENDING_RECONCILIATION", "message": "order identity not confirmed"})
        pending = {**row, "order_id": order_id, "status": "PENDING_RECONCILIATION"}
        pending.pop("message", None)
        pending = self.journal.update(pending)
        if pending["status"] in TERMINAL:
            return pending
        try:
            executed = decimal(order["executedQty"])
            if not ZERO <= executed <= decimal(row["quantity"]):
                raise ValueError("unmatched executed quantity")
            fills = []
            if executed > ZERO:
                response = self.client.request("GET", "/api/v3/myTrades",
                                               {"symbol": row["symbol"], "orderId": order_id}, signed=True)
                fields = ("id", "qty", "quoteQty", "commission", "commissionAsset", "price")
                unique = {fill["id"]: {key: fill[key] for key in fields}
                          for fill in response if fill["orderId"] == order_id}
                fills = list(unique.values())
                if any(decimal(fill["qty"]) <= ZERO or decimal(fill["quoteQty"]) <= ZERO
                       or decimal(fill["commission"]) < ZERO for fill in fills):
                    raise ValueError("invalid venue fill")
                if sum(decimal(fill["qty"]) for fill in fills) != executed:
                    return self.journal.update({**pending, "message": "awaiting complete trade history"})
        except (VenueError, ArithmeticError, ValueError, KeyError, TypeError):
            return self.journal.update({**pending, "message": "awaiting complete trade history"})
        return self.journal.update({**pending, "status": order["status"], "executed_qty": str(executed), "fills": fills})

    def reconcile(self, client_id):
        row = self.journal.get(valid_client_id(client_id))
        if row["status"] in TERMINAL:
            return row
        params = {"symbol": row["symbol"]}
        if "order_id" in row:
            params["orderId"] = row["order_id"]
        else:
            params["origClientOrderId"] = client_id
        try:
            order = self.client.request("GET", "/api/v3/order", params, signed=True)
        except VenueError as exc:
            return self.journal.update({**row, "status": "PENDING_RECONCILIATION", "message": str(exc)})
        return self._record(row, order)

    def cancel(self, client_id):
        row = self.reconcile(client_id)
        if row["status"] in TERMINAL or "order_id" not in row:
            return row
        try:
            order = self.client.request("DELETE", "/api/v3/order",
                                        {"symbol": row["symbol"], "orderId": row["order_id"]}, signed=True)
        except VenueError as exc:
            return self.journal.update({**row, "status": "PENDING_RECONCILIATION", "message": str(exc)})
        return self._record(row, order)
