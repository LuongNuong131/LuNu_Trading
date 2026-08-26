"""Paper-only execution engine. Live execution is intentionally not implemented."""
from __future__ import annotations

import uuid
from dataclasses import asdict
from threading import RLock

from core.models import OrderIntent, Position, RiskLimits, RiskDecision, Side, Signal
from core.risk_manager import RiskManager


class PaperExecutor:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()
        self.risk = RiskManager(self.limits)
        self.capital = self.limits.starting_capital  # free cash
        self.realized_pnl = 0.0
        self.positions: dict[str, Position] = {}
        self.closed_trades: list[dict] = []
        self._latest_prices: dict[str, float] = {}
        self._lock = RLock()

    def _mark_value(self, position: Position, price: float) -> float:
        return position.amount * price

    def _unrealized(self, position: Position, price: float) -> float:
        direction = 1.0 if position.side is Side.BUY else -1.0
        gross = direction * (price - position.entry_price) * position.amount
        fees = price * position.amount * self.limits.fee_rate
        return gross - fees

    @property
    def equity(self) -> float:
        with self._lock:
            marked_value = sum(
                self._mark_value(position, self._latest_prices.get(symbol, position.entry_price))
                for symbol, position in self.positions.items()
            )
            return self.capital + marked_value

    def update_price(self, symbol: str, current_price: float) -> list[dict]:
        if current_price <= 0:
            return []
        with self._lock:
            self._latest_prices[symbol] = current_price
            position = self.positions.get(symbol)
            if position is None:
                return []
            hit_stop = (position.side is Side.BUY and current_price <= position.stop_loss) or (position.side is Side.SELL and current_price >= position.stop_loss)
            hit_target = (position.side is Side.BUY and current_price >= position.take_profit) or (position.side is Side.SELL and current_price <= position.take_profit)
            if hit_stop:
                return [self.close(symbol, current_price, "STOP_LOSS")]
            if hit_target:
                return [self.close(symbol, current_price, "TAKE_PROFIT")]
            return []

    def execute(self, intent: OrderIntent) -> RiskDecision:
        with self._lock:
            if intent.signal not in (Signal.BUY, Signal.SELL):
                return RiskDecision(False, "HOLD signal")
            existing = self.positions.get(intent.symbol)
            if existing and existing.side.value == intent.signal.value:
                return RiskDecision(False, "same-side position already open")
            if existing:
                self.close(intent.symbol, intent.price, "SIGNAL_REVERSAL")
            decision = self.risk.evaluate(intent, self.equity, self.positions)
            if not decision.approved:
                return decision
            side = Side(intent.signal.value)
            entry = self._slipped_price(intent.price, side, entering=True)
            max_notional = self.equity * self.limits.max_position_notional_pct
            amount = min(decision.amount, max_notional / entry)
            notional = amount * entry
            entry_fee = notional * self.limits.fee_rate
            if notional + entry_fee > self.capital:
                return RiskDecision(False, "insufficient free cash")
            position = Position(intent.symbol, side, entry, amount, decision.stop_loss or 0.0, decision.take_profit or 0.0)
            self.capital -= notional + entry_fee
            self.positions[intent.symbol] = position
            self._latest_prices[intent.symbol] = intent.price
            return RiskDecision(True, "approved", amount, decision.stop_loss, decision.take_profit, notional)

    def _slipped_price(self, price: float, side: Side, entering: bool) -> float:
        bps = self.limits.slippage_bps / 10_000.0
        sign = 1.0 if side is Side.BUY else -1.0
        if not entering:
            sign *= -1.0
        return price * (1.0 + sign * bps)

    def close(self, symbol: str, price: float, reason: str) -> dict:
        with self._lock:
            position = self.positions.pop(symbol, None)
            self._latest_prices.pop(symbol, None)
            if position is None:
                return {"status": "NOOP", "symbol": symbol, "reason": "not_open"}
            exit_price = self._slipped_price(price, position.side, entering=False)
            proceeds = exit_price * position.amount
            exit_fee = proceeds * self.limits.fee_rate
            gross = (exit_price - position.entry_price) * position.amount if position.side is Side.BUY else (position.entry_price - exit_price) * position.amount
            entry_fee = position.entry_price * position.amount * self.limits.fee_rate
            pnl = gross - entry_fee - exit_fee
            self.capital += proceeds - exit_fee
            self.realized_pnl += pnl
            trade = {
                "order_id": uuid.uuid4().hex[:12].upper(),
                "symbol": symbol,
                "side": position.side.value,
                "entry_price": position.entry_price,
                "exit_price": exit_price,
                "amount": position.amount,
                "pnl": pnl,
                "reason": reason,
                "status": "CLOSED",
            }
            self.closed_trades.append(trade)
            return trade

    def snapshot(self) -> dict:
        with self._lock:
            open_positions = []
            for symbol, position in self.positions.items():
                item = asdict(position)
                current_price = self._latest_prices.get(symbol, position.entry_price)
                item["current_price"] = current_price
                item["unrealized_pnl"] = self._unrealized(position, current_price)
                open_positions.append(item)
            equity = self.equity
            return {
                "mode": "paper",
                "capital": self.capital,
                "equity": equity,
                "realized_pnl": self.realized_pnl,
                "unrealized_pnl": equity - self.capital - sum(position.notional for position in self.positions.values()),
                "open_positions": open_positions,
                "closed_trades": self.closed_trades[-50:],
            }


executor = PaperExecutor()
