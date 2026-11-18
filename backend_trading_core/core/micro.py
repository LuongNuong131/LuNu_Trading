"""Opt-in risk limits for a small, single-currency paper wallet."""
from __future__ import annotations

import os
from math import isfinite

from core.models import RiskLimits

DOLLAR_QUOTES = {"USD", "USDT", "USDC"}


def micro_limits(capital: float, taker_fee_bps: float = 40) -> RiskLimits:
    if not isfinite(capital) or not 10 <= capital <= 50:
        raise ValueError("PAPER_MICRO_CAPITAL must be between 10 and 50")
    if not isfinite(taker_fee_bps) or not 0 < taker_fee_bps <= 100:
        raise ValueError("PAPER_TAKER_FEE_BPS must be positive and at most 100")
    return RiskLimits(starting_capital=capital, risk_per_trade=0.01,
                      max_position_notional_pct=0.60, max_total_exposure_pct=0.60,
                      max_open_positions=1, max_daily_loss_pct=0.02, max_drawdown_pct=0.05,
                      fee_rate=taker_fee_bps / 10_000, slippage_bps=10, include_costs_in_risk=True)


def limits_from_env() -> tuple[str | None, RiskLimits | None]:
    capital = os.getenv("PAPER_MICRO_CAPITAL")
    if capital is None:
        return None, None
    quote = os.getenv("MARKET_SCAN_QUOTE", "").strip().upper()
    if quote not in DOLLAR_QUOTES:
        raise ValueError("micro paper requires MARKET_SCAN_QUOTE=USD, USDT or USDC")
    return quote, micro_limits(float(capital), float(os.getenv("PAPER_TAKER_FEE_BPS", "40")))
