"""Forward-only OHLCV replay with the same signal, risk and ledger as public paper."""
from __future__ import annotations

import pandas as pd

from core.models import Instrument, RiskLimits
from core.order_executor import PaperExecutor
from core.pipeline import BASELINE, BENCHMARK, WARMUP_BARS, process_bar
from data_pipeline.bars import CRYPTO_INTERVAL_MS, utc_ms, validate_ohlcv


def run_backtest(df: pd.DataFrame, symbol: str = "BTC/USDT", evaluation_start: int = WARMUP_BARS, *,
                 instrument: Instrument | None = None, strategy: str = BASELINE,
                 limits: RiskLimits | None = None, source: str = "OHLCV replay",
                 keep_trace: bool = False, liquidate: bool = True) -> dict:
    instrument = instrument or Instrument(symbol, "REPLAY", "USDT", "crypto spot", "1m")
    interval = CRYPTO_INTERVAL_MS.get(instrument.timeframe)
    frame = validate_ohlcv(df, interval_ms=interval)
    if "closed_ms" not in frame:
        if interval is None:
            raise ValueError("session-based replay requires UTC bar close timestamps")
        frame["closed_ms"] = frame["timestamp"] + interval
    frame["closed_ms"] = pd.to_numeric(frame["closed_ms"], errors="raise")
    if (frame["closed_ms"].isna().any() or (frame["closed_ms"] % 1 != 0).any()
            or (frame["closed_ms"] <= frame["timestamp"]).any()
            or (frame["closed_ms"].iloc[:-1].to_numpy() > frame["timestamp"].iloc[1:].to_numpy()).any()
            or (interval is not None and (frame["closed_ms"] - frame["timestamp"] != interval).any())):
        raise ValueError("bar close timestamps are inconsistent with replay execution")
    account = PaperExecutor(limits, currency=instrument.currency)
    equity_curve = [account.equity]
    exposure = []
    events = []
    evaluation_start = max(WARMUP_BARS, int(evaluation_start))
    for index in range(evaluation_start, len(frame)):
        event = process_bar(frame.iloc[:index + 1], instrument, account, source, strategy=strategy,
                            benchmark_entry=index == evaluation_start)
        if keep_trace:
            events.append(event)
        equity_curve.append(account.equity)
        exposure.append(account.exposure / account.equity if account.equity > 0 else 0)
    if exposure and liquidate and account.positions:
        account.close(instrument.key, float(frame["close"].iloc[-1]), "BACKTEST_END",
                      now=utc_ms(frame["closed_ms"].iloc[-1]), instrument=instrument,
                      spread_bps=account.limits.spread_bps)
        equity_curve.append(account.equity)
    series = pd.Series(equity_curve, dtype=float)
    drawdown = 1 - series / series.cummax()
    start = float(account.limits.starting_capital)
    snapshot = account.snapshot()
    fills = [fill for order in snapshot["orders"] for fill in order["fills"].values()]
    return {
        "strategy": strategy, "trades": sum(bool(o["fills"]) and o["side"] == "SELL" for o in snapshot["orders"]),
        "fills": len(fills), "starting_equity": start, "ending_equity": account.equity,
        "return_pct": round(100 * (account.equity / start - 1), 6),
        "max_drawdown_pct": round(100 * max(float(drawdown.max()), snapshot["max_drawdown_pct"] / 100), 6),
        "turnover_pct": round(100 * sum(f["amount"] * f["price"] for f in fills) / start, 6),
        "mean_exposure_pct": round(100 * sum(exposure) / max(1, len(exposure)), 6),
        "fees": account.fees,
        "buys": sum(o["side"] == "BUY" for o in snapshot["orders"]),
        "buy_rejections": sum(o["side"] == "BUY" and o["status"] == "RISK_REJECTED" for o in snapshot["orders"]),
        "from": utc_ms(frame["timestamp"].iloc[evaluation_start]).isoformat() if exposure else None,
        "to": utc_ms(frame["closed_ms"].iloc[-1]).isoformat() if exposure else None,
        **({"events": events, "account": snapshot} if keep_trace else {}),
    }
