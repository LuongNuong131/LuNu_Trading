"""Cash ledger and order reconciliation shared by paper, replay and tests."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from math import isfinite
from pathlib import Path
from threading import RLock
from typing import Protocol

from core.models import Instrument, OrderIntent, Position, RiskDecision, RiskLimits, Side, Signal, as_dict, utc_now
from core.risk_manager import RiskManager, RiskState, quantize

OPEN = {"PENDING_SUBMIT", "PENDING_RECONCILIATION", "OPEN", "PARTIALLY_FILLED"}
TERMINAL = {"FILLED", "CANCELED", "REJECTED"}


@dataclass(frozen=True)
class Fill:
    fill_id: str
    amount: float
    price: float
    fee: float


@dataclass(frozen=True)
class OrderReport:
    status: str
    fills: tuple[Fill, ...] = ()
    reason: str = ""


class Broker(Protocol):
    def submit(self, order: dict) -> OrderReport: ...
    def lookup(self, order: dict) -> OrderReport | None: ...
    def cancel(self, order: dict) -> OrderReport: ...


class ImmediatePaperBroker:
    """A deterministic in-process broker. The stored order is its replay input."""

    def submit(self, order: dict) -> OrderReport:
        return self.lookup(order)

    def lookup(self, order: dict) -> OrderReport:
        fill = Fill("paper-" + order["client_order_id"], order["amount"], order["price"],
                    order["amount"] * order["price"] * order["fee_rate"])
        return OrderReport("FILLED", (fill,))

    def cancel(self, order: dict) -> OrderReport:
        return self.lookup(order)


class PaperExecutor:
    def __init__(self, limits: RiskLimits | None = None, *, currency: str = "USDT",
                 path: str | Path | None = None, broker: Broker | None = None) -> None:
        self.limits = limits or RiskLimits()
        self.currency = currency
        self.broker = broker or ImmediatePaperBroker()
        self.risk = RiskManager(self.limits)
        self.capital = self.limits.starting_capital
        self.realized_pnl = self.fees = 0.0
        self.positions: dict[str, Position] = {}
        self.marks: dict[str, float] = {}
        self.orders: dict[str, dict] = {}
        self.closed_trades: list[dict] = []
        self.killed = False
        self._lock = RLock()
        self._db = None
        if path is not None:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(path, check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS paper_accounts (currency TEXT PRIMARY KEY, state TEXT NOT NULL)")
            with self._transaction():
                pass

    def _state(self) -> dict:
        risk = asdict(self.risk.state)
        risk["day"] = risk["day"].isoformat() if risk["day"] else None
        return as_dict({
            "limits": asdict(self.limits), "capital": self.capital, "fees": self.fees,
            "realized_pnl": self.realized_pnl, "positions": {key: asdict(p) for key, p in self.positions.items()},
            "marks": self.marks, "orders": self.orders, "closed_trades": self.closed_trades,
            "risk": risk, "kill_switch": self.killed,
        })

    def _restore(self) -> None:
        row = self._db.execute("SELECT state FROM paper_accounts WHERE currency = ?", (self.currency,)).fetchone()
        if not row:
            return
        state = json.loads(row[0])
        if RiskLimits(**state["limits"]) != self.limits:
            raise ValueError("paper account limits changed; select a new paper database")
        self.capital, self.fees, self.realized_pnl = state["capital"], state["fees"], state["realized_pnl"]
        self.positions = {
            key: Position(**{**p, "side": Side(p["side"]), "opened_at": datetime.fromisoformat(p["opened_at"])})
            for key, p in state["positions"].items()
        }
        self.marks, self.orders, self.closed_trades = state["marks"], state["orders"], state["closed_trades"]
        risk = state["risk"]
        self.risk.state = RiskState(
            date.fromisoformat(risk["day"]) if risk["day"] else None,
            risk["day_start_equity"], risk["high_water_mark"], risk.get("max_drawdown", 0.0),
            datetime.fromisoformat(risk["as_of"]) if risk.get("as_of") else None,
        )
        self.killed = state["kill_switch"]

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self._db is None:
                yield
                return
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                self._restore()
                try:
                    yield
                    self._db.execute("INSERT OR REPLACE INTO paper_accounts VALUES (?, ?)",
                                     (self.currency, json.dumps(self._state(), allow_nan=False)))
                except BaseException:
                    self._db.rollback()
                    self._restore()
                    raise

    def shutdown(self) -> None:
        if self._db is not None:
            with self._lock:
                self._db.close()

    @property
    def exposure(self) -> float:
        return sum(p.amount * self.marks.get(key, p.entry_price) for key, p in self.positions.items())

    @property
    def equity(self) -> float:
        with self._lock:
            return self.capital + self.exposure

    def set_kill_switch(self, active: bool) -> None:
        with self._transaction():
            self.killed = active

    def update_price(self, symbol: str, current_price: float, *, now: datetime | None = None,
                     instrument: Instrument | None = None, trigger_exits: bool = True,
                     data_status: str = "ready", spread_bps: float | None = None) -> list[dict]:
        if not isfinite(current_price) or current_price <= 0 or data_status != "ready":
            return []
        now = now or utc_now()
        with self._transaction():
            self.marks[symbol] = current_price
            self.risk.mark(self.equity, now)
            position = self.positions.get(symbol)
        if position is None or not trigger_exits:
            return []
        reason = None
        if current_price <= position.stop_loss:
            reason = "STOP_LOSS"
        elif current_price >= position.take_profit:
            reason = "TAKE_PROFIT"
        if reason:
            trade = self.close(symbol, current_price, reason, now=now, instrument=instrument, spread_bps=spread_bps)
            return [trade] if trade["status"] == "CLOSED" else []
        return []

    def execute(self, intent: OrderIntent) -> RiskDecision:
        now = intent.generated_at
        key = intent.signal_id
        if key is None and intent.source_bar_timestamp is not None:
            key = f"{intent.symbol}:{intent.source_bar_timestamp}:{intent.signal.value}"
        key = key or uuid.uuid4().hex
        client_id = "LNU-" + uuid.uuid5(uuid.NAMESPACE_URL, f"{self.currency}:{key}").hex[:24]
        with self._transaction():
            if client_id in self.orders:
                return RiskDecision(**self.orders[client_id]["decision"])
            adjustment = self.risk.one_way_adjustment(intent.spread_bps)
            if intent.spread_bps is not None and (not isfinite(intent.spread_bps) or intent.spread_bps < 0):
                adjustment = float("nan")
            buy = intent.signal is Signal.BUY
            price = intent.price * (1 + adjustment if buy else 1 - adjustment)
            instrument = intent.instrument
            if instrument and isfinite(price):
                price = quantize(price, instrument.tick, up=buy)
            pending = [order for order in self.orders.values() if order["status"] in OPEN]
            reserved = sum((o["amount"] - o["filled"]) * o["price"] * (1 + o["fee_rate"])
                           for o in pending if o["side"] == "BUY")
            pending_exposure = sum((o["amount"] - o["filled"]) * o["price"]
                                   for o in pending if o["side"] == "BUY")
            decision = self.risk.evaluate(
                intent, self.equity, self.positions, now, price=price, cash=self.capital - reserved,
                exposure=self.exposure + pending_exposure, pending={o["symbol"] for o in pending},
                killed=self.killed,
            )
            if instrument and instrument.currency != self.currency:
                decision = RiskDecision(False, "instrument currency differs from paper account")
            if intent.signal is Signal.HOLD:
                return decision
            decision = replace(decision, client_order_id=client_id)
            order = {
                "signal_id": key, "client_order_id": client_id, "symbol": intent.symbol,
                "created_at": now.isoformat(), "side": intent.signal.value, "reason": intent.reason,
                "evidence": intent.evidence,
                "decision": asdict(decision), "amount": decision.amount,
                "price": price if isfinite(price) else None, "fee_rate": self.limits.fee_rate,
                "mark_price": intent.price if intent.spread_bps is not None and isfinite(intent.price) else None,
                "status": "PENDING_SUBMIT" if decision.approved else "RISK_REJECTED",
                "filled": 0.0, "fills": {}, "currency": self.currency,
                "account_before": self._balance(),
            }
            self.orders[client_id] = order
        if decision.approved:
            self._contact(order.copy(), self.broker.submit)
        return decision

    def _contact(self, order: dict, operation) -> None:
        try:
            report = operation(order)
        except (TimeoutError, ConnectionError):
            report = None
        if report is not None:
            self.apply_report(order["client_order_id"], report)
        else:
            with self._transaction():
                current = self.orders[order["client_order_id"]]
                if current["status"] in OPEN:
                    current["status"] = "PENDING_RECONCILIATION"

    def reconcile(self) -> None:
        for order in self.snapshot()["orders"]:
            if order["status"] in OPEN:
                self._contact(order, self.broker.lookup)

    def cancel(self, client_order_id: str) -> None:
        order = next(o for o in self.snapshot()["orders"] if o["client_order_id"] == client_order_id)
        if order["status"] in OPEN:
            self._contact(order, self.broker.cancel)

    def apply_report(self, client_order_id: str, report: OrderReport) -> None:
        with self._transaction():
            order = self.orders[client_order_id]
            if order["status"] not in OPEN:
                return
            if report.status not in {"OPEN", "PARTIALLY_FILLED"} | TERMINAL:
                raise ValueError("invalid broker order status")
            unseen = {}
            for fill in report.fills:
                if fill.fill_id in order["fills"]:
                    previous = order["fills"][fill.fill_id]
                    if any(previous[key] != value for key, value in asdict(fill).items()):
                        raise ValueError("broker changed a recorded fill")
                    continue
                if fill.fill_id in unseen:
                    if unseen[fill.fill_id] != fill:
                        raise ValueError("conflicting fills with the same ID")
                    continue
                if not fill.fill_id or not all(map(isfinite, (fill.amount, fill.price, fill.fee))):
                    raise ValueError("invalid fill")
                if fill.amount <= 0 or fill.price <= 0 or fill.fee < 0:
                    raise ValueError("invalid fill")
                unseen[fill.fill_id] = fill
            filled = order["filled"] + sum(fill.amount for fill in unseen.values())
            if filled > order["amount"] + max(1e-14, order["amount"] * 1e-12):
                raise ValueError("fills exceed order quantity")
            full = abs(filled - order["amount"]) <= max(1e-14, order["amount"] * 1e-12)
            if report.status == "FILLED" and not full:
                raise ValueError("filled status without full quantity")
            if report.status == "REJECTED" and filled > 0:
                raise ValueError("rejected orders cannot have fills")
            if order["side"] == "BUY":
                cost = sum(fill.amount * fill.price + fill.fee for fill in unseen.values())
                reserved = sum((other["amount"] - other["filled"]) * other["price"] * (1 + other["fee_rate"])
                               for other in self.orders.values() if other is not order
                               and other["side"] == "BUY" and other["status"] in OPEN)
                if cost > self.capital - reserved + 1e-9:
                    raise ValueError("fill exceeds available cash")
            elif sum(fill.amount for fill in unseen.values()) > (self.positions[order["symbol"]].amount + 1e-14):
                raise ValueError("fill exceeds the open long")
            if order["side"] == "SELL" and any(fill.fee > fill.amount * fill.price for fill in unseen.values()):
                raise ValueError("sell fill fee exceeds its proceeds")
            for fill in unseen.values():
                self._book_fill(order, fill)
            order["filled"] = filled
            order["status"] = ("FILLED" if full else "PARTIALLY_FILLED"
                               if filled and report.status == "OPEN" else report.status)
            order["broker_reason"] = report.reason
            self.risk.mark(self.equity, datetime.fromisoformat(order["created_at"]))

    def _book_fill(self, order: dict, fill: Fill) -> None:
        symbol = order["symbol"]
        position = self.positions.get(symbol)
        gross = fill.amount * fill.price
        self.fees += fill.fee
        pnl = 0.0
        if order["side"] == "BUY":
            self.capital -= gross + fill.fee
            if position is None:
                self.marks.setdefault(symbol, order.get("mark_price") or fill.price)
                self.positions[symbol] = Position(
                    symbol, Side.BUY, fill.price, fill.amount, order["decision"]["stop_loss"],
                    order["decision"]["take_profit"], datetime.fromisoformat(order["created_at"]), fill.fee,
                )
            else:
                position.entry_price = (position.notional + gross) / (position.amount + fill.amount)
                position.amount += fill.amount
                position.entry_fees += fill.fee
        else:
            entry_fee = position.entry_fees * fill.amount / position.amount
            pnl = gross - position.entry_price * fill.amount - entry_fee - fill.fee
            self.capital += gross - fill.fee
            self.realized_pnl += pnl
            position.amount -= fill.amount
            position.entry_fees -= entry_fee
            trade = {"order_id": order["client_order_id"], "fill_id": fill.fill_id, "symbol": symbol,
                     "side": Side.BUY.value, "entry_price": position.entry_price, "exit_price": fill.price,
                     "amount": fill.amount, "pnl": pnl, "reason": order["reason"], "status": "CLOSED"}
            self.closed_trades.append(trade)
            if position.amount <= max(1e-14, fill.amount * 1e-12):
                del self.positions[symbol]
                self.marks.pop(symbol, None)
        self.risk.mark(self.equity, datetime.fromisoformat(order["created_at"]))
        order["fills"][fill.fill_id] = {
            **asdict(fill), "realized_pnl": pnl, "cash_delta": (-gross if order["side"] == "BUY" else gross) - fill.fee,
            "position_delta": fill.amount if order["side"] == "BUY" else -fill.amount,
            "account_after": self._balance(),
        }

    def close(self, symbol: str, price: float, reason: str, *, now: datetime | None = None,
              instrument: Instrument | None = None, spread_bps: float | None = None) -> dict:
        now = now or utc_now()
        decision = self.execute(OrderIntent(symbol, Signal.SELL, price, 0.0, reason, now,
                                            instrument=instrument,
                                            signal_id=f"{symbol}:{now.isoformat()}:{reason}", spread_bps=spread_bps))
        if decision.approved and self.closed_trades and self.closed_trades[-1]["order_id"] == decision.client_order_id:
            return self.closed_trades[-1]
        return {"status": "NOOP", "symbol": symbol, "reason": decision.reason}

    def _balance(self) -> dict:
        return {"capital": self.capital, "equity": self.equity, "fees": self.fees,
                "realized_pnl": self.realized_pnl, "exposure": self.exposure,
                "unrealized_pnl": sum(self.marks.get(key, p.entry_price) * p.amount - p.notional - p.entry_fees
                                      for key, p in self.positions.items()),
                "drawdown_pct": 100 * self.risk.drawdown(self.equity),
                "max_drawdown_pct": 100 * self.risk.state.max_drawdown}

    def snapshot(self) -> dict:
        with self._lock:
            if self._db is not None:
                self._restore()
            return as_dict({"mode": "paper", "currency": self.currency, **self._balance(),
                            "risk_limits": asdict(self.limits),
                            "kill_switch": self.killed, "open_positions": [asdict(p) for p in self.positions.values()],
                            "closed_trades": self.closed_trades[-50:], "orders": list(self.orders.values())})
