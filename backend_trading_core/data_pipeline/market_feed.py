"""Read-only OHLCV adapter. It never creates or submits exchange orders."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from core.event_engine import Event, event_engine


class MarketFeed:
    def __init__(self, exchange_id: str | None = None, symbols: list[str] | None = None, poll_seconds: int | None = None) -> None:
        self.exchange_id = exchange_id or os.getenv("MARKET_EXCHANGE", "binance")
        self.symbols = symbols or [s.strip() for s in os.getenv("MARKET_SYMBOLS", "BTC/USDT").split(",") if s.strip()]
        self.poll_seconds = max(15, int(poll_seconds or os.getenv("MARKET_POLL_SECONDS", "60")))
        self.exchange: Any = None
        self.active = False
        self._last_bar: dict[tuple[str, str], int] = {}

    async def _connect(self) -> None:
        if self.exchange is not None:
            return
        try:
            import ccxt.async_support as ccxt
        except ImportError as exc:
            raise RuntimeError("ccxt is required only to run the market feed; install requirements.txt") from exc
        exchange_cls = getattr(ccxt, self.exchange_id)
        self.exchange = exchange_cls({"enableRateLimit": True, "options": {"defaultType": "spot"}})

    async def fetch_once(self, symbol: str) -> None:
        await self._connect()
        results = await asyncio.gather(self.exchange.fetch_ohlcv(symbol, "1m", limit=100), self.exchange.fetch_ohlcv(symbol, "15m", limit=40))
        payload = {"symbol": symbol, "timeframe": "1m", "bars": results[0], "bars_15m": results[1]}
        bar_ts = int(results[0][-1][0]) if results[0] else 0
        key = (symbol, "1m")
        if bar_ts and self._last_bar.get(key) == bar_ts:
            return
        self._last_bar[key] = bar_ts
        event_engine.put(Event("NEW_BAR", payload))

    async def fetch_ohlcv_loop(self) -> None:
        self.active = True
        while self.active:
            for symbol in self.symbols:
                try:
                    await self.fetch_once(symbol)
                except Exception as exc:
                    event_engine.put(Event("FEED_ERROR", {"symbol": symbol, "error": str(exc)}))
            await asyncio.sleep(self.poll_seconds)

    def start(self) -> asyncio.Task:
        return asyncio.create_task(self.fetch_ohlcv_loop())

    async def stop(self) -> None:
        self.active = False
        if self.exchange is not None:
            await self.exchange.close()
            self.exchange = None


market_feed = MarketFeed()
