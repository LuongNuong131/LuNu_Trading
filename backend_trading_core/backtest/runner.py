"""Small reproducible OHLCV backtest using the same paper risk engine."""
from __future__ import annotations

import pandas as pd

from core.models import OrderIntent
from core.order_executor import PaperExecutor
from factor_zoo.technical import deterministic_signal, get_technical_context


def run_backtest(df: pd.DataFrame, symbol: str = "BTC/USDT", evaluation_start: int = 35) -> dict:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        raise ValueError(f"missing columns: {sorted(required - set(df.columns))}")
    if len(df) < 36:
        return {"trades": 0, "starting_equity": 0.0, "ending_equity": 0.0, "return_pct": 0.0, "max_drawdown_pct": 0.0}
    executor = PaperExecutor()
    equity_curve = [executor.equity]
    evaluation_start = max(35, int(evaluation_start))
    for index in range(35, len(df)):
        history = df.iloc[:index]
        bar = df.iloc[index]
        open_price = float(bar["open"])
        close_price = float(bar["close"])
        executor.update_price(symbol, open_price)
        if index < evaluation_start:
            continue
        context = get_technical_context(history)
        signal = deterministic_signal(context)
        if signal.value != "HOLD":
            executor.execute(OrderIntent(symbol, signal, open_price, float(context["atr"]), "backtest", source_bar_timestamp=int(bar["timestamp"])))
        executor.update_price(symbol, close_price)
        equity_curve.append(executor.equity)
    if executor.positions:
        executor.close(symbol, float(df.iloc[-1]["close"]), "BACKTEST_END")
    series = pd.Series(equity_curve, dtype=float)
    high_water = series.cummax()
    drawdown = ((high_water - series) / high_water.replace(0, pd.NA)).fillna(0.0)
    start = float(executor.limits.starting_capital)
    end = float(executor.capital)
    return {"trades": len(executor.closed_trades), "starting_equity": start, "ending_equity": end, "return_pct": round((end / start - 1) * 100, 6), "max_drawdown_pct": round(float(drawdown.max()) * 100, 6)}
