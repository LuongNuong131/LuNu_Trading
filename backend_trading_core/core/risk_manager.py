"""Fail-closed risk checks for paper trading."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from core.models import OrderIntent, Position, RiskDecision, RiskLimits, Side, Signal


@dataclass
class RiskState:
    day: date | None = None
    day_start_equity: float = 0.0
    high_water_mark: float = 0.0


class RiskManager:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()
        self.state = RiskState()

    def _roll_day(self, equity: float, now: datetime) -> None:
        today = now.astimezone(timezone.utc).date()
        if self.state.day != today:
            self.state.day = today
            self.state.day_start_equity = equity
        self.state.high_water_mark = max(self.state.high_water_mark or equity, equity)

    def evaluate(
        self,
        intent: OrderIntent,
        equity: float,
        positions: dict[str, Position],
        now: datetime | None = None,
    ) -> RiskDecision:
        now = now or datetime.now(timezone.utc)
        self._roll_day(equity, now)
        if intent.signal is Signal.HOLD:
            return RiskDecision(False, "HOLD signal")
        if not intent.symbol or intent.price <= 0 or intent.atr <= 0:
            return RiskDecision(False, "invalid price or ATR")
        if len(positions) >= self.limits.max_open_positions and intent.symbol not in positions:
            return RiskDecision(False, "max open positions reached")
        if equity <= 0:
            return RiskDecision(False, "non-positive equity")
        daily_loss = max(0.0, (self.state.day_start_equity - equity) / self.state.day_start_equity) if self.state.day_start_equity else 0.0
        drawdown = max(0.0, (self.state.high_water_mark - equity) / self.state.high_water_mark) if self.state.high_water_mark else 0.0
        if daily_loss >= self.limits.max_daily_loss_pct:
            return RiskDecision(False, "daily loss limit reached")
        if drawdown >= self.limits.max_drawdown_pct:
            return RiskDecision(False, "maximum drawdown limit reached")

        stop_distance = max(intent.atr * 1.5, intent.price * self.limits.min_stop_distance_pct)
        stop_distance = min(stop_distance, intent.price * self.limits.max_stop_distance_pct)
        if stop_distance <= 0:
            return RiskDecision(False, "invalid stop distance")
        amount = (equity * self.limits.risk_per_trade) / stop_distance
        position_cap = equity * self.limits.max_position_notional_pct
        total_exposure = sum(position.notional for position in positions.values() if position.symbol != intent.symbol)
        portfolio_cap = max(0.0, equity * self.limits.max_total_exposure_pct - total_exposure)
        notional_cap = min(position_cap, portfolio_cap)
        amount = min(amount, notional_cap / intent.price)
        if amount <= 0:
            return RiskDecision(False, "position size is zero")
        side = Side.BUY if intent.signal is Signal.BUY else Side.SELL
        stop_loss = intent.price - stop_distance if side is Side.BUY else intent.price + stop_distance
        reward_distance = stop_distance * 2.0
        take_profit = intent.price + reward_distance if side is Side.BUY else intent.price - reward_distance
        return RiskDecision(True, "approved", amount, stop_loss, take_profit, amount * intent.price)
