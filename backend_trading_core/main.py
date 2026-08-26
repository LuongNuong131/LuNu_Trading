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
from config import settings
from core.event_engine import Event, event_engine
from core.models import OrderIntent
from core.order_executor import executor
from data_pipeline.market_feed import market_feed
from db.duckdb_client import db_client
from factor_zoo.technical import deterministic_signal, get_technical_context

logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("lunu")


def _persist_closed_trades(closed_trades: list[dict]) -> None:
    for trade in closed_trades:
        db_client.insert_trade(
            trade["order_id"],
            trade["symbol"],
            trade["side"],
            trade["exit_price"],
            trade["amount"],
            trade["status"],
            {
                "entry_price": trade["entry_price"],
                "exit_price": trade["exit_price"],
                "pnl": trade["pnl"],
                "reason": trade["reason"],
            },
        )
        db_client.insert_audit_event(
            f"close:{trade['order_id']}",
            "POSITION_CLOSED",
            trade["symbol"],
            {"reason": trade["reason"], "pnl": trade["pnl"], "exit_price": trade["exit_price"]},
        )


async def handle_new_bar(event: Event) -> None:
    symbol = event.data["symbol"]
    bars = event.data.get("bars", [])
    if len(bars) < 35:
        return
    columns = ["timestamp", "open", "high", "low", "close", "volume"]
    df_1m = pd.DataFrame(bars, columns=columns)
    price = float(df_1m["close"].iloc[-1])
    closed_trades = executor.update_price(symbol, price)
    _persist_closed_trades(closed_trades)
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
    db_client.insert_trade(f"SIG-{symbol.replace('/', '-')}-{bar_id}", symbol, signal.value, price, decision.amount, status, {"reason": reason, "stop_loss": decision.stop_loss, "take_profit": decision.take_profit})
    log.info("%s %s approved=%s reason=%s", symbol, signal.value, decision.approved, decision.reason)


async def handle_feed_error(event: Event) -> None:
    payload = event.data
    symbol = payload.get("symbol")
    error = payload.get("error", "unknown feed error")
    failure_count = payload.get("failure_count", 0)
    log.warning("market feed error symbol=%s failure_count=%s error=%s", symbol, failure_count, error)
    db_client.insert_audit_event(
        f"feed-error:{symbol}:{failure_count}",
        "FEED_ERROR",
        symbol,
        {"error": error, "failure_count": failure_count},
    )


async def run() -> None:
    event_engine.register("NEW_BAR", handle_new_bar)
    event_engine.register("FEED_ERROR", handle_feed_error)
    event_engine.start()
    feed_task = market_feed.start() if settings.market_feed_enabled else None
    if not settings.market_feed_enabled:
        log.info("Market feed disabled; API is running in paper/dashboard mode")
    server = uvicorn.Server(uvicorn.Config(app, host=settings.api_host, port=settings.api_port, log_level=settings.log_level.lower()))
    try:
        await server.serve()
    finally:
        await market_feed.stop()
        await event_engine.stop_async()
        if feed_task is not None:
            feed_task.cancel()
            await asyncio.gather(feed_task, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
