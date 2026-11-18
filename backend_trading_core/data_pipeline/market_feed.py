"""Public spot OHLCV adapter with bounded retry and explicit per-symbol health."""
from __future__ import annotations

import asyncio
import os
import ssl
from typing import Any

from core.event_engine import Event, event_engine
from core.models import Instrument, utc_now
from data_pipeline.bars import finalized_crypto


def instrument_from_market(exchange, symbol: str) -> Instrument:
    import ccxt

    market = exchange.market(symbol)
    if not market.get("spot") or market.get("active") is False:
        raise ValueError("configured instrument is not an active spot market")
    if exchange.precisionMode not in (ccxt.TICK_SIZE, ccxt.DECIMAL_PLACES):
        raise ValueError("exchange uses unsupported spot precision mode")
    precision = market["precision"]

    def step(field: str) -> float:
        value = precision.get(field)
        if value is None:
            raise ValueError(f"exchange omitted {field} precision")
        return float(value) if exchange.precisionMode == ccxt.TICK_SIZE else 10 ** -int(value)

    limits = market["limits"]
    return Instrument(symbol, exchange.id, market["quote"], "crypto spot", "1m",
                      step("price"), step("amount"), limits.get("amount", {}).get("min") or 0.0,
                      limits.get("cost", {}).get("min") or 0.0)


class MarketFeed:
    def __init__(self, exchange_id: str | None = None, symbols: list[str] | None = None,
                 poll_seconds: int | None = None) -> None:
        self.exchange_id = exchange_id or os.getenv("MARKET_EXCHANGE", "binance")
        self.symbols = symbols or [s.strip() for s in os.getenv("MARKET_SYMBOLS", "BTC/USDT").split(",") if s.strip()]
        self.poll_seconds = max(15, int(poll_seconds or os.getenv("MARKET_POLL_SECONDS", "60")))
        self.exchange: Any = None
        self.active = False
        self._failures: dict[str, int] = {}
        self._retry_after: dict[str, float] = {}

    async def _connect(self) -> None:
        if self.exchange is not None:
            return
        import ccxt.async_support as ccxt

        if self.exchange_id not in ccxt.exchanges:
            raise ValueError("unsupported CCXT exchange")
        exchange_cls = getattr(ccxt, self.exchange_id)
        self.exchange = exchange_cls({"enableRateLimit": True, "timeout": 12_000,
                                      "aiohttp_trust_env": True, "options": {"defaultType": "spot"}})
        self.exchange.ssl_context = ssl.create_default_context()
        await self.exchange.load_markets()

    async def fetch_once(self, symbol: str) -> None:
        await self._connect()
        instrument = instrument_from_market(self.exchange, symbol)
        rows = await self.exchange.fetch_ohlcv(symbol, "1m", limit=100)
        observed_at = utc_now()
        bars = finalized_crypto(rows, observed_at)
        if not event_engine.put(Event("NEW_BAR", {"instrument": instrument, "bars": bars,
                                                 "observed_at": observed_at})):
            raise RuntimeError("paper event loop unavailable")

    async def fetch_ohlcv_loop(self) -> None:
        self.active = True
        loop = asyncio.get_running_loop()
        try:
            while self.active:
                for symbol in self.symbols:
                    if loop.time() < self._retry_after.get(symbol, 0):
                        continue
                    try:
                        await self.fetch_once(symbol)
                        self._failures[symbol] = 0
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        failures = self._failures.get(symbol, 0) + 1
                        self._failures[symbol] = failures
                        self._retry_after[symbol] = loop.time() + min(300, 2 ** min(failures, 8))
                        message = str(exc) if type(exc) in (ValueError, RuntimeError) else type(exc).__name__
                        event_engine.put(Event("FEED_ERROR", {"symbol": f"{self.exchange_id}:{symbol}",
                                                             "error": message}))
                        await self._close_exchange()
                await asyncio.sleep(self.poll_seconds)
        finally:
            await self._close_exchange()
            self.active = False

    async def _close_exchange(self) -> None:
        exchange, self.exchange = self.exchange, None
        if exchange is not None:
            await exchange.close()

    def start(self) -> asyncio.Task:
        return asyncio.create_task(self.fetch_ohlcv_loop())

    async def stop(self) -> None:
        self.active = False


market_feed = MarketFeed()
