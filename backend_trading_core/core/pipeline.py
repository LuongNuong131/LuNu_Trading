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


def prepare_bar(frame, instrument: Instrument, source: str, *, strategy: str = BASELINE,
                paper_now=None, max_age_seconds: float = 180) -> dict:
    history, bar = frame.iloc[:-1], frame.iloc[-1]
    event = signal_evidence(history, instrument, source, strategy)
    price = float(bar["close"] if paper_now is not None else bar["open"])
    now = paper_now or utc_ms(bar["timestamp"])
    age = (now - utc_ms(history["closed_ms"].iloc[-1])).total_seconds()
    status = "stale" if paper_now is not None and age > max_age_seconds else "ready"
    event.update(execution_at=now.isoformat(), quote=price, data_status=status, data_age_seconds=age,
                 quote_closed_at=utc_ms(bar["closed_ms"]).isoformat())
    return event


def process_bar(frame, instrument: Instrument, account: PaperExecutor, source: str, *,
                strategy: str = BASELINE, paper_now=None, max_age_seconds: float = 180,
                benchmark_entry: bool = True, other_quotes_fresh: bool = True, prepared: dict | None = None,
                scan_quote: float | None = None, spread_bps: float | None = None,
                entry_block_reason: str | None = None, scanning: bool = False) -> dict:
    """Replay at next open. Scanner paper uses observed midpoint plus modeled costs."""
    history, bar = frame.iloc[:-1], frame.iloc[-1]
    event = prepared if prepared is not None else prepare_bar(
        frame, instrument, source, strategy=strategy, paper_now=paper_now, max_age_seconds=max_age_seconds,
    )
    price = scan_quote if scan_quote is not None else event["quote"]
    event["quote"] = price
    now = paper_now or utc_ms(bar["timestamp"])
    status = event["data_status"]
    fresh_at = utc_ms(history["closed_ms"].iloc[-1])
    holding = instrument.key in account.positions
    closed = []
    if not scanning or holding:
        closed = account.update_price(instrument.key, price, now=now, instrument=instrument,
                                      trigger_exits=strategy != BENCHMARK, data_status=status,
                                      spread_bps=spread_bps)
    action = Signal(event["signal"])
    should_trade = action is not Signal.HOLD and (strategy != BENCHMARK or benchmark_entry)
    if scanning and action is Signal.SELL and not holding:
        should_trade = False
        event["execution_note"] = "SELL is close-only; no open long"
    if should_trade and not closed:
        intent_status = status
        if status == "ready" and action is Signal.BUY and not other_quotes_fresh:
            intent_status = "held-position quote stale"
        intent = OrderIntent(instrument.key, action, price, event["indicators"]["atr"], event["reason"],
                             now, int(history["timestamp"].iloc[-1]), instrument, event["signal_id"],
                             fresh_at, max_age_seconds if paper_now is not None else None, intent_status,
                             event.copy(), spread_bps, entry_block_reason)
        event["risk"] = asdict(account.execute(intent))
    if closed:
        event["execution_note"] = "Existing exit triggered at the reference price; no new strategy order placed"
    if paper_now is None:
        account.update_price(instrument.key, float(bar["close"]), now=utc_ms(bar["closed_ms"]),
                             instrument=instrument, trigger_exits=strategy != BENCHMARK)
    return event
