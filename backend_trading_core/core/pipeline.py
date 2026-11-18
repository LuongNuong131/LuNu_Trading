"""Build evidence from bars known before execution and send intents to one ledger."""
from __future__ import annotations

from dataclasses import asdict

from core.models import Instrument, OrderIntent, Signal, utc_now
from core.order_executor import PaperExecutor
from data_pipeline.bars import utc_ms
from factor_zoo.strategies import BASELINE, BENCHMARK, TREND, evaluate_strategy
from factor_zoo.technical import get_technical_context

WARMUP_BARS = 35
HISTORY_BARS = 99


def signal_evidence(history, instrument: Instrument, source: str, strategy: str = BASELINE) -> dict:
    last = history.iloc[-1]
    wall = utc_ms(last["closed_ms"])
    window = history.tail(HISTORY_BARS)
    context = get_technical_context(window)
    setup = evaluate_strategy(strategy, window, context)
    if setup.features:
        context.update(setup.features)
    structural_stop = setup.structural_stop - instrument.tick if setup.structural_stop is not None else None
    return {
        "signal_id": f"{source}:{instrument.key}:{instrument.timeframe}:{strategy}:{int(last['closed_ms'])}",
        "instrument": asdict(instrument), "symbol": instrument.key, "source": source,
        "timeframe": instrument.timeframe, "closed_at": wall.isoformat(), "closed": True,
        "strategy": strategy, "signal": setup.signal.value, "reason": setup.reason, "indicators": context,
        "conditions": setup.conditions, "structural_stop": structural_stop,
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
    if paper_now is None and spread_bps is None and account.limits.include_costs_in_risk:
        spread_bps = account.limits.spread_bps
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
                             event.copy(), spread_bps, entry_block_reason, event["structural_stop"])
        event["risk"] = asdict(account.execute(intent))
    if closed:
        event["execution_note"] = "Existing exit triggered at the reference price; no new strategy order placed"
    if paper_now is None:
        end = utc_ms(bar["closed_ms"])
        position = account.positions.get(instrument.key)
        if position and strategy != BENCHMARK:
            # At the open gaps were handled already. If both levels trade, assume the stop first.
            trigger = None
            if bar["low"] <= position.stop_loss:
                trigger = position.stop_loss
            elif bar["high"] >= position.take_profit:
                trigger = position.take_profit
            if trigger is not None:
                account.update_price(instrument.key, trigger, now=end, instrument=instrument,
                                     spread_bps=spread_bps)
        account.update_price(instrument.key, float(bar["close"]), now=end,
                             instrument=instrument, trigger_exits=False)
    return event
