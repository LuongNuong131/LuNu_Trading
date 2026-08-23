"""Deterministic technical features and a conservative signal policy."""
from __future__ import annotations

import pandas as pd

from core.models import Signal


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def get_technical_context(df: pd.DataFrame) -> dict:
    if not isinstance(df, pd.DataFrame) or len(df) < 35:
        return {"ready": False, "reason": "at least 35 OHLCV rows are required"}
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        return {"ready": False, "reason": f"missing columns: {sorted(required - set(df.columns))}"}
    work = df.copy()
    close = pd.to_numeric(work["close"], errors="coerce")
    high = pd.to_numeric(work["high"], errors="coerce")
    low = pd.to_numeric(work["low"], errors="coerce")
    if close.isna().any() or high.isna().any() or low.isna().any() or (close <= 0).any():
        return {"ready": False, "reason": "invalid OHLC values"}
    rsi = calculate_rsi(close)
    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    true_range = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = true_range.rolling(14, min_periods=14).mean()
    last = float(close.iloc[-1])
    atr_value = float(atr.iloc[-1])
    rsi_value = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0
    macd_hist = float((macd - macd_signal).iloc[-1])
    prior_low = float(low.iloc[-11:-1].min())
    prior_high = float(high.iloc[-11:-1].max())
    return {
        "ready": True,
        "rsi": round(rsi_value, 4),
        "macd_hist": round(macd_hist, 8),
        "atr": round(atr_value, 8),
        "current_price": last,
        "liquidity_sweep_bullish": bool(float(low.iloc[-1]) < prior_low and last > prior_low),
        "liquidity_sweep_bearish": bool(float(high.iloc[-1]) > prior_high and last < prior_high),
    }


def deterministic_signal(context: dict) -> Signal:
    """A deliberately simple baseline; it is not a profitability claim."""
    if not context.get("ready") or context.get("atr", 0) <= 0:
        return Signal.HOLD
    rsi = context["rsi"]
    hist = context["macd_hist"]
    bullish = context.get("liquidity_sweep_bullish", False)
    bearish = context.get("liquidity_sweep_bearish", False)
    if bullish and hist > 0 and rsi < 70:
        return Signal.BUY
    if bearish and hist < 0 and rsi > 30:
        return Signal.SELL
    return Signal.HOLD
