"""Read-only OHLCV adapter. It never creates or submits exchange orders."""
from __future__ import annotations

import asyncio
from typing import Any

from config import settings
from core.event_engine import Event, event_engine


class MarketFeed:
    def __init__(
        self,
        exchange_id: str | None = None,
        symbols: list[str] | None = None,
        poll_seconds: int | None = None,
    ) -> None:
        self.exchange_id = exchange_id or settings.market_exchange
        self.symbols = symbols or list(settings.market_symbols)
        self.poll_seconds = max(15, int(poll_seconds or settings.market_poll_seconds))
        self.exchange: Any = None
        self.active = False
        self._last_bar: dict[tuple[str, str], int] = {}
        self._failure_count = 0

    async def _connect(self) -> None:
        if self.exchange is not None:
            return
        try:
            import ccxt.async_support as ccxt
        except ImportError as exc:
            raise RuntimeError("ccxt is required only to run the market feed; install requirements.txt") from exc
        exchange_cls = getattr(ccxt, self.exchange_id, None)
        if exchange_cls is None:
            raise ValueError(f"Unsupported CCXT exchange: {self.exchange_id}")
        self.exchange = exchange_cls({"enableRateLimit": True, "options": {"defaultType": "spot"}})

    async def fetch_once(self, symbol: str) -> None:
        await self._connect()
        results = await asyncio.gather(
            self.exchange.fetch_ohlcv(symbol, "1m", limit=100),
            self.exchange.fetch_ohlcv(symbol, "15m", limit=40),
        )
        self._failure_count = 0
        bars_1m, bars_15m = results
        bar_ts = int(bars_1m[-1][0]) if bars_1m else 0
        key = (symbol, "1m")
        if not bar_ts or self._last_bar.get(key) == bar_ts:
            return
        self._last_bar[key] = bar_ts
        event_engine.put(Event("NEW_BAR", {"symbol": symbol, "timeframe": "1m", "bars": bars_1m, "bars_15m": bars_15m}))

    async def fetch_ohlcv_loop(self) -> None:
        self.active = True
        try:
            while self.active:
                for symbol in self.symbols:
                    try:
                        await self.fetch_once(symbol)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self._failure_count += 1
                        event_engine.put(Event("FEED_ERROR", {"symbol": symbol, "error": str(exc), "failure_count": self._failure_count}))
                        await self._close_exchange()
                        await asyncio.sleep(min(300, 2 ** min(self._failure_count, 8)))
                await asyncio.sleep(self.poll_seconds)
        finally:
            await self._close_exchange()
            self.active = False

    async def _close_exchange(self) -> None:
        exchange, self.exchange = self.exchange, None
        if exchange is not None:
            try:
                await exchange.close()
            except Exception:
                pass

    def start(self) -> asyncio.Task:
        if self.active:
            raise RuntimeError("market feed is already running")
        return asyncio.create_task(self.fetch_ohlcv_loop())

    async def stop(self) -> None:
        self.active = False
        await self._close_exchange()


market_feed = MarketFeed()
