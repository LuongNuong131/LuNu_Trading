"""Versioned, causal, long-only signal policies evaluated on finalized candles."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.models import Signal
from factor_zoo.technical import calculate_rsi, deterministic_signal

BASELINE = "sweep-v1"
TREND = "sweep-ema26-v1"
ICT = "ict-fvg-v1"
DONCHIAN = "donchian-20-10-v1"
PULLBACK = "ema-pullback-v1"
REVERSION = "bollinger-reentry-v1"
BENCHMARK = "buy-hold-capped-v1"
CASH = "cash-v1"
PAPER_STRATEGIES = (BASELINE, TREND, ICT, DONCHIAN, PULLBACK, REVERSION)
RESEARCH_STRATEGIES = (*PAPER_STRATEGIES, BENCHMARK, CASH)


@dataclass(frozen=True)
class Setup:
    signal: Signal
    reason: str
    conditions: str
    features: dict | None = None
    structural_stop: float | None = None


def _sweep(frame: pd.DataFrame, context: dict, *, trend: bool = False) -> Setup:
    signal = deterministic_signal(context)
    conditions = "BUY: 10-bar low sweep, positive MACD histogram, RSI < 70; SELL: opposite sweep, negative histogram, RSI > 30"
    if trend:
        conditions += "; BUY requires close above EMA26"
    if signal is Signal.BUY:
        if trend and context["current_price"] <= context["ema26"]:
            return Setup(Signal.HOLD, "BUY sweep conditions passed but close is at or below EMA26", conditions)
        return Setup(signal, "Low swept previous 10-bar low and recovered; MACD histogram > 0; RSI < 70", conditions)
    if signal is Signal.SELL:
        return Setup(signal, "High swept previous 10-bar high and fell back; MACD histogram < 0; RSI > 30; close long only", conditions)
    return Setup(signal, "Sweep, momentum or positive ATR conditions did not align", conditions)


def _ict(frame: pd.DataFrame, context: dict) -> Setup:
    """Define an ICT-inspired three-candle pattern without right-hand pivots."""
    anchor, displacement, confirmation = (frame.iloc[index] for index in (-3, -2, -1))
    prior = frame.iloc[-13:-3]
    high, low = float(prior["high"].max()), float(prior["low"].min())
    body = float(displacement["close"] - displacement["open"])
    gap_up = float(confirmation["low"] - anchor["high"])
    gap_down = float(anchor["low"] - confirmation["high"])
    previous = frame.iloc[:-2]
    true_range = pd.concat([previous["high"] - previous["low"],
                            (previous["high"] - previous["close"].shift()).abs(),
                            (previous["low"] - previous["close"].shift()).abs()], axis=1).max(axis=1)
    base_atr = float(true_range.tail(14).mean())
    sweep_up = anchor["low"] < low < anchor["close"]
    sweep_down = anchor["high"] > high > anchor["close"]
    shift_up = displacement["close"] > high and body >= 0.8 * base_atr
    shift_down = displacement["close"] < low and -body >= 0.8 * base_atr
    gap_floor = 0.1 * base_atr
    features = {"ict_prior_high": high, "ict_prior_low": low, "ict_pre_displacement_atr": base_atr,
                "ict_displacement_body": body, "ict_bullish_sweep": bool(sweep_up),
                "ict_bearish_sweep": bool(sweep_down), "ict_bullish_shift": bool(shift_up),
                "ict_bearish_shift": bool(shift_down), "ict_fvg_up": gap_up, "ict_fvg_down": gap_down}
    conditions = ("Three closed bars: 10-bar liquidity sweep and reclaim, displacement of at least 0.8× prior ATR "
                  "closing through the prior range, then a same-direction three-candle FVG of at least 0.1× ATR")
    if base_atr > 0 and sweep_up and shift_up and gap_up >= gap_floor:
        return Setup(Signal.BUY, "Bullish sweep, structure shift and fair value gap confirmed",
                     conditions, features, float(anchor["low"]))
    if base_atr > 0 and sweep_down and shift_down and gap_down >= gap_floor:
        return Setup(Signal.SELL, "Bearish ICT pattern; close existing long only", conditions, features)
    return Setup(Signal.HOLD, "ICT sweep, displacement, structure shift and gap did not all confirm", conditions, features)


def _donchian(frame: pd.DataFrame, context: dict) -> Setup:
    upper = float(frame["high"].iloc[-21:-1].max())
    lower = float(frame["low"].iloc[-11:-1].min())
    close = context["current_price"]
    conditions = "BUY: close above prior 20-bar high and EMA26; SELL: close below prior 10-bar low"
    features = {"donchian_entry_high": upper, "donchian_exit_low": lower}
    if close > upper and close > context["ema26"]:
        return Setup(Signal.BUY, "Breakout above the prior 20-bar high in an uptrend", conditions, features)
    if close < lower:
        return Setup(Signal.SELL, "Close below the 10-bar exit channel", conditions, features)
    return Setup(Signal.HOLD, "No confirmed Donchian breakout or exit", conditions, features)


def _pullback(frame: pd.DataFrame, context: dict) -> Setup:
    close = frame["close"]
    fast = close.ewm(span=12, adjust=False).mean()
    prev_rsi = float(calculate_rsi(close).iloc[-2])
    features = {"ema12": float(fast.iloc[-1]), "prior_rsi": prev_rsi}
    conditions = ("BUY: EMA12 above EMA26, previous close below EMA12, current close above EMA12 and RSI crosses 50; "
                  "SELL: close below EMA26")
    if (fast.iloc[-1] > context["ema26"] and close.iloc[-2] <= fast.iloc[-2]
            and close.iloc[-1] > fast.iloc[-1] and prev_rsi <= 50 < context["rsi"]):
        return Setup(Signal.BUY, "Trend pullback reclaimed EMA12 and RSI 50", conditions, features)
    if close.iloc[-1] < context["ema26"]:
        return Setup(Signal.SELL, "Close lost EMA26", conditions, features)
    return Setup(Signal.HOLD, "Trend pullback conditions did not align", conditions, features)


def _reversion(frame: pd.DataFrame, context: dict) -> Setup:
    closes = frame["close"]
    prior = closes.iloc[-22:-2]
    mean = float(prior.mean())
    lower = mean - 2 * float(prior.std(ddof=0))
    conditions = ("BUY: previous close below the lower 20-bar band, current close back inside and below the mean; "
                  "SELL: close at or above the mean")
    features = {"bollinger_mean": mean, "bollinger_lower": lower}
    if closes.iloc[-2] < lower <= closes.iloc[-1] < mean:
        return Setup(Signal.BUY, "Close re-entered the band after overshooting the lower boundary", conditions, features)
    if closes.iloc[-1] >= mean:
        return Setup(Signal.SELL, "Mean reversion reached the center band", conditions, features)
    return Setup(Signal.HOLD, "No confirmed lower-band re-entry", conditions, features)


def evaluate_strategy(strategy: str, frame: pd.DataFrame, context: dict) -> Setup:
    if strategy not in RESEARCH_STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy}")
    if not context["ready"] or context["atr"] <= 0:
        return Setup(Signal.HOLD, context.get("reason", "nonpositive ATR"), "Requires valid, warmed-up finalized candles")
    if strategy == BASELINE:
        return _sweep(frame, context)
    if strategy == TREND:
        return _sweep(frame, context, trend=True)
    if strategy == BENCHMARK:
        return Setup(Signal.BUY, "Buy and hold at common risk and position caps", "One entry per evaluation window")
    if strategy == CASH:
        return Setup(Signal.HOLD, "Keep initial cash", "Cash benchmark")
    return {ICT: _ict, DONCHIAN: _donchian, PULLBACK: _pullback, REVERSION: _reversion}[strategy](frame, context)
