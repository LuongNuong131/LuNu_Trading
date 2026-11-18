"""Build evidence from bars known before execution and send intents to one ledger."""
from __future__ import annotations

from dataclasses import asdict

from core.models import Instrument, OrderIntent, Signal, utc_now
from core.order_executor import PaperExecutor
from data_pipeline.bars import utc_ms
from factor_zoo.technical import deterministic_signal, get_technical_context

BASELINE = "sweep-v1"
TREND = "sweep-ema26-v1"
BENCHMARK = "buy-hold-capped-v1"
WARMUP_BARS = 35
HISTORY_BARS = 99


def signal_evidence(history, instrument: Instrument, source: str, strategy: str = BASELINE) -> dict:
    last = history.iloc[-1]
    wall = utc_ms(last["closed_ms"])
    context = get_technical_context(history.tail(HISTORY_BARS))
    signal = deterministic_signal(context)
    if not context["ready"]:
        reason = context["reason"]
    elif strategy == BENCHMARK:
        signal, reason = Signal.BUY, "Buy and hold at common risk and position caps"
    elif signal is Signal.BUY:
        reason = "Low swept previous 10-bar low and recovered; MACD histogram > 0; RSI < 70"
        if strategy == TREND and context["current_price"] <= context["ema26"]:
            signal, reason = Signal.HOLD, "BUY conditions passed; close at or below EMA26"
    elif signal is Signal.SELL:
        reason = "High swept previous 10-bar high and fell back; MACD histogram < 0; RSI > 30; close long only"
    else:
        reason = "Sweep, momentum or positive ATR conditions did not align"
    conditions = "BUY: low sweep, positive MACD histogram, RSI < 70; SELL: high sweep, negative histogram, RSI > 30"
    if strategy == TREND:
        conditions += "; BUY also requires a close above EMA26"
    return {
        "signal_id": f"{source}:{instrument.key}:{instrument.timeframe}:{strategy}:{int(last['closed_ms'])}",
        "instrument": asdict(instrument), "symbol": instrument.key, "source": source,
        "timeframe": instrument.timeframe, "closed_at": wall.isoformat(), "closed": True,
        "strategy": strategy, "signal": signal.value, "reason": reason, "indicators": context,
        "conditions": conditions,
        "invalidation": "HOLD on failed conditions or nonpositive ATR; risk may reject stale data or unavailable size",
    }


def process_bar(frame, instrument: Instrument, account: PaperExecutor, source: str, *,
                strategy: str = BASELINE, paper_now=None, max_age_seconds: float = 180,
                benchmark_entry: bool = True, other_quotes_fresh: bool = True) -> dict:
    """Replay at next open. Public paper uses the next finalized close observed at poll time."""
    history, bar = frame.iloc[:-1], frame.iloc[-1]
    event = signal_evidence(history, instrument, source, strategy)
    price = float(bar["close"] if paper_now is not None else bar["open"])
    now = paper_now or utc_ms(bar["timestamp"])
    fresh_at = utc_ms(history["closed_ms"].iloc[-1])
    age = (now - fresh_at).total_seconds()
    status = "stale" if paper_now is not None and age > max_age_seconds else "ready"
    event.update(execution_at=now.isoformat(), quote=price, data_status=status, data_age_seconds=age,
                 quote_closed_at=utc_ms(bar["closed_ms"]).isoformat())
    closed = account.update_price(instrument.key, price, now=now, instrument=instrument,
                                  trigger_exits=strategy != BENCHMARK, data_status=status)
    action = Signal(event["signal"])
    if action is not Signal.HOLD and (strategy != BENCHMARK or benchmark_entry) and not closed:
        intent_status = "held-position quote stale" if status == "ready" and action is Signal.BUY and not other_quotes_fresh else status
        intent = OrderIntent(instrument.key, action, price, event["indicators"]["atr"], event["reason"],
                             now, int(history["timestamp"].iloc[-1]), instrument, event["signal_id"],
                             fresh_at, max_age_seconds if paper_now is not None else None, intent_status, event.copy())
        event["risk"] = asdict(account.execute(intent))
    if closed:
        event["execution_note"] = "Existing exit triggered at the reference price; no new strategy order placed"
    if paper_now is None:
        account.update_price(instrument.key, float(bar["close"]), now=utc_ms(bar["closed_ms"]),
                             instrument=instrument, trigger_exits=strategy != BENCHMARK)
    return event
