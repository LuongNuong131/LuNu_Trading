"""LuNu Trading public-data paper runner and loopback API."""
from __future__ import annotations

import asyncio
import logging
import os
import webbrowser
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from api.fastapi_gateway import app, runtime
from core.event_engine import event_engine
from data_pipeline.market_feed import market_feed

log = logging.getLogger("lunu")


DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8000
DASHBOARD_URL = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}/dashboard"


async def _open_when_ready(server: uvicorn.Server) -> None:
    while not server.started:
        await asyncio.sleep(0.1)
    try:
        if await asyncio.to_thread(webbrowser.open, DASHBOARD_URL):
            return
    except (OSError, webbrowser.Error):
        pass
    log.info("Open the paper dashboard in your browser: %s", DASHBOARD_URL)


async def run(*, open_dashboard: bool = False) -> None:
    if os.getenv("LIVE_TRADING_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("This entrypoint only supports paper trading")
    feed_enabled = os.getenv("MARKET_FEED_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    scan_quote = market_feed.scanner.settings.quote if market_feed.scanner else None
    runtime.configure(market_feed.exchange_id, market_feed.symbols, feed_enabled, scan_quote)
    event_engine.register("NEW_BAR", runtime.handle_new_bar)
    event_engine.register("FEED_ERROR", runtime.feed_error)
    event_engine.register("SCAN_BATCH", runtime.handle_scan)
    event_engine.register("SCAN_ERROR", runtime.scan_error)
    event_engine.start()
    feed_task = market_feed.start(lambda: runtime.priority_symbols(market_feed.exchange_id)) if feed_enabled else None
    log.info("Paper dashboard: %s (public feed %s)", DASHBOARD_URL,
             "enabled" if feed_enabled else "disabled")
    server = uvicorn.Server(uvicorn.Config(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, log_level="warning"))
    browser_task = asyncio.create_task(_open_when_ready(server)) if open_dashboard else None
    try:
        await server.serve()
    finally:
        if browser_task is not None:
            browser_task.cancel()
            await asyncio.gather(browser_task, return_exceptions=True)
        await market_feed.stop()
        if feed_task is not None:
            feed_task.cancel()
            await asyncio.gather(feed_task, return_exceptions=True)
        await event_engine.stop_async()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
