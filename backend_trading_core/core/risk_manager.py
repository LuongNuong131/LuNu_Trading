"""Common pre-trade checks for cash accounts and long-only spot."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from math import isfinite

from core.models import OrderIntent, Position, RiskDecision, RiskLimits, Signal


def quantize(value: float, step: float, *, up: bool = False) -> float:
    if not step:
        return value
    increment = Decimal(str(step))
    units = Decimal(str(value)) / increment
    return float(units.to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * increment)


@dataclass
class RiskState:
    day: date | None = None
    day_start_equity: float = 0.0
    high_water_mark: float = 0.0
    max_drawdown: float = 0.0
    as_of: datetime | None = None


class RiskManager:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()
        self.state = RiskState()

    def mark(self, equity: float, now: datetime) -> None:
        now = max(self.state.as_of or now, now)
        self.state.as_of = now
        today = now.astimezone(timezone.utc).date()
        if self.state.day != today:
            self.state.day = today
            self.state.day_start_equity = equity
        self.state.high_water_mark = max(self.state.high_water_mark, equity)
        self.state.max_drawdown = max(self.state.max_drawdown, self.drawdown(equity))

    def drawdown(self, equity: float) -> float:
        peak = self.state.high_water_mark
        return max(0.0, 1 - equity / peak) if peak else 0.0

    def stop_distance(self, price: float, atr: float, structural_stop: float | None = None) -> float | None:
        minimum = price * self.limits.min_stop_distance_pct
        maximum = price * self.limits.max_stop_distance_pct
        if structural_stop is not None:
            distance = price - structural_stop
            if not isfinite(structural_stop) or structural_stop <= 0 or not 0 < distance <= maximum:
                return None
            return max(distance, minimum)
        return min(max(atr * 1.5, minimum), maximum)

    def one_way_adjustment(self, spread_bps: float | None) -> float:
        spread = self.limits.spread_bps if spread_bps is None else spread_bps
        return (self.limits.slippage_bps + spread / 2) / 10_000

    def evaluate(
        self, intent: OrderIntent, equity: float, positions: dict[str, Position], now: datetime,
        *, price: float, cash: float, exposure: float, pending: set[str], killed: bool,
    ) -> RiskDecision:
        self.mark(equity, now)
        if intent.signal is Signal.HOLD:
            return RiskDecision(False, "HOLD signal")
        if not intent.symbol or not isfinite(price) or price <= 0:
            return RiskDecision(False, "invalid execution price")
        if intent.data_status != "ready":
            return RiskDecision(False, f"market data {intent.data_status}")
        if intent.known_at is not None:
            age = (now - intent.known_at).total_seconds()
            if age < 0:
                return RiskDecision(False, "signal data from the future")
            if intent.max_age_seconds is not None and age > intent.max_age_seconds:
                return RiskDecision(False, "market data stale")
        if intent.symbol in pending:
            return RiskDecision(False, "order already pending reconciliation or fill")
        existing = positions.get(intent.symbol)
        if intent.signal is Signal.SELL:
            if existing is None:
                return RiskDecision(False, "no long position to close")
            return RiskDecision(True, "approved to reduce long exposure", existing.amount,
                                notional=existing.amount * price)
        if killed:
            return RiskDecision(False, "kill switch active")
        if intent.entry_block_reason:
            return RiskDecision(False, intent.entry_block_reason)
        if self.limits.include_costs_in_risk and intent.spread_bps is None:
            return RiskDecision(False, "fresh bid/ask required for micro entries")
        if existing:
            return RiskDecision(False, "same-side position already open")
        if not isfinite(intent.atr) or intent.atr <= 0:
            return RiskDecision(False, "invalid ATR")
        if len(set(positions) | pending) >= self.limits.max_open_positions:
            return RiskDecision(False, "max open positions reached")
        if equity <= 0:
            return RiskDecision(False, "non-positive equity")
        start = self.state.day_start_equity
        if start and 1 - equity / start >= self.limits.max_daily_loss_pct:
            return RiskDecision(False, "daily loss limit reached")
        if self.drawdown(equity) >= self.limits.max_drawdown_pct:
            return RiskDecision(False, "maximum drawdown limit reached")

        structural_stop = intent.structural_stop
        if structural_stop is not None and intent.price <= structural_stop:
            return RiskDecision(False, "market price crossed the structural stop before entry")
        stop = self.stop_distance(price, intent.atr, structural_stop)
        if stop is None:
            return RiskDecision(False, "structural stop invalid or wider than the risk cap")
        if stop <= 0:
            return RiskDecision(False, "invalid stop distance")
        budget = min(equity * self.limits.max_position_notional_pct,
                     equity * self.limits.max_total_exposure_pct - exposure)
        if budget <= 1e-9:
            return RiskDecision(False, "portfolio exposure limit reached")
        if cash <= 0:
            return RiskDecision(False, "insufficient free cash")
        instrument = intent.instrument
        stop_price = price - stop
        stop_fill_price = stop_price * (1 - self.one_way_adjustment(intent.spread_bps))
        if instrument:
            stop_fill_price = quantize(stop_fill_price, instrument.tick)
        fee_rate = self.limits.fee_rate
        stop_loss_per_unit = price - stop_fill_price + (price + stop_fill_price) * fee_rate
        risk_per_unit = stop_loss_per_unit if self.limits.include_costs_in_risk else stop
        amount = min(equity * self.limits.risk_per_trade / risk_per_unit, budget / price,
                     cash / (price * (1 + fee_rate)))
        if instrument:
            amount = quantize(amount, instrument.lot)
            if amount <= 0 or amount < instrument.minimum_amount:
                return RiskDecision(False, "size below minimum lot/quantity")
            if amount * price < instrument.minimum_notional:
                return RiskDecision(False, "size below minimum notional")
        if amount <= 0:
            return RiskDecision(False, "position size is zero")
        return RiskDecision(True, "approved to open a cash long", amount, stop_price,
                            price + 2 * stop, amount * price,
                            estimated_stop_loss=amount * stop_loss_per_unit)
