"""Domain models for safe, deterministic paper trading."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class RiskLimits:
    starting_capital: float = 10_000.0
    risk_per_trade: float = 0.01
    max_position_notional_pct: float = 0.25
    max_open_positions: int = 3
    max_daily_loss_pct: float = 0.03
    max_drawdown_pct: float = 0.10
    fee_rate: float = 0.0006
    slippage_bps: float = 5.0
    min_stop_distance_pct: float = 0.002
    max_stop_distance_pct: float = 0.05


@dataclass
class Position:
    symbol: str
    side: Side
    entry_price: float
    amount: float
    stop_loss: float
    take_profit: float
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def notional(self) -> float:
        return self.entry_price * self.amount


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    signal: Signal
    price: float
    atr: float
    reason: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_bar_timestamp: int | None = None


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    amount: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    notional: float = 0.0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_dict(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: as_dict(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [as_dict(v) for v in value]
    return value
