"""LuNu Trading paper runner.

The default decision path is deterministic and risk-gated. LLM output is
advisory only and cannot create an order intent.
"""
from __future__ import annotations

import asyncio
import logging

import pandas as pd
import uvicorn

from api.fastapi_gateway import app
from core.event_engine import Event, event_engine
from core.models import OrderIntent
from core.order_executor import executor
from data_pipeline.market_feed import market_feed
from db.duckdb_client import db_client
from factor_zoo.technical import deterministic_signal, get_technical_context

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("lunu")


async def handle_new_bar(event: Event) -> None:
    symbol = event.data["symbol"]
    bars = event.data.get("bars", [])
    bars_15m = event.data.get("bars_15m", [])
    if len(bars) < 35:
        return
    columns = ["timestamp", "open", "high", "low", "close", "volume"]
    df_1m = pd.DataFrame(bars, columns=columns)
    price = float(df_1m["close"].iloc[-1])
    executor.update_price(symbol, price)
    context = get_technical_context(df_1m)
    signal = deterministic_signal(context)
    reason = f"rsi={context.get('rsi')} macd_hist={context.get('macd_hist')} atr={context.get('atr')}"
    bar_id = str(int(bars[-1][0]))
    db_client.insert_audit_event(f"bar:{symbol}:{bar_id}", "BAR_PROCESSED", symbol, {"bar_id": bar_id, "price": price})
    db_client.insert_ai_log("deterministic_signal", symbol, signal.value, reason)
    db_client.insert_audit_event(f"signal:{symbol}:{bar_id}", "SIGNAL_GENERATED", symbol, {"signal": signal.value, "reason": reason})
    if signal.value == "HOLD":
        return
    intent = OrderIntent(symbol=symbol, signal=signal, price=price, atr=float(context["atr"]), reason=reason, source_bar_timestamp=int(bars[-1][0]))
    decision = executor.execute(intent)
    status = "APPROVED" if decision.approved else f"REJECTED:{decision.reason}"
    db_client.insert_audit_event(f"risk:{symbol}:{bar_id}", "RISK_DECISION", symbol, {"approved": decision.approved, "reason": decision.reason, "amount": decision.amount})
    db_client.insert_trade(intent.source_bar_timestamp and f"SIG-{intent.source_bar_timestamp}" or "SIG-UNKNOWN", symbol, signal.value, price, decision.amount, status, {"reason": reason})
    log.info("%s %s approved=%s reason=%s", symbol, signal.value, decision.approved, decision.reason)


async def run() -> None:
    event_engine.register("NEW_BAR", handle_new_bar)
    event_engine.start()
    feed_task = market_feed.start()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning"))
    try:
        await server.serve()
    finally:
        await market_feed.stop()
        await event_engine.stop_async()
        feed_task.cancel()
        await asyncio.gather(feed_task, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
