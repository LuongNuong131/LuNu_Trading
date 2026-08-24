"""Walk-forward validation helpers for research, not performance promises."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest.runner import run_backtest


@dataclass(frozen=True)
class ValidationWindow:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def rolling_windows(length: int, train_size: int, test_size: int, step: int | None = None) -> list[ValidationWindow]:
    if min(length, train_size, test_size) <= 0 or train_size + test_size > length:
        return []
    step = step or test_size
    windows: list[ValidationWindow] = []
    start = 0
    while start + train_size + test_size <= length:
        windows.append(ValidationWindow(start, start + train_size, start + train_size, start + train_size + test_size))
        start += step
    return windows


def summarize_returns(results: list[dict]) -> dict:
    if not results:
        return {"windows": 0, "profitable_windows": 0, "median_return_pct": 0.0, "worst_return_pct": 0.0, "worst_drawdown_pct": 0.0}
    returns = pd.Series([float(item.get("return_pct", 0.0)) for item in results])
    drawdowns = pd.Series([float(item.get("max_drawdown_pct", 0.0)) for item in results])
    return {"windows": len(results), "profitable_windows": int((returns > 0).sum()), "median_return_pct": round(float(returns.median()), 6), "worst_return_pct": round(float(returns.min()), 6), "worst_drawdown_pct": round(float(drawdowns.max()), 6)}


def walk_forward_validate(df: pd.DataFrame, symbol: str = "BTC/USDT", train_size: int = 500, test_size: int = 100) -> dict:
    """Evaluate only forward test segments; the train segment is reserved for strategy selection."""
    windows = rolling_windows(len(df), train_size, test_size)
    results = []
    for window in windows:
        test = df.iloc[window.test_start:window.test_end].copy()
        # Include only a warm-up prefix from the training boundary, never future test rows.
        warmup = df.iloc[max(0, window.test_start - 35):window.test_end].copy()
        result = run_backtest(warmup, symbol)
        result["window"] = {"train_start": window.train_start, "train_end": window.train_end, "test_start": window.test_start, "test_end": window.test_end}
        result["test_rows"] = len(test)
        results.append(result)
    return {"summary": summarize_returns(results), "windows": results}
