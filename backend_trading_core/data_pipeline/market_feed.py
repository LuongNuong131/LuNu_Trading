"""Public spot OHLCV adapter with bounded retry and explicit per-symbol health."""
from __future__ import annotations

import asyncio
import os
import ssl
from collections.abc import Callable
from typing import Any

from core.event_engine import Event, event_engine
from core.models import Instrument, utc_now
from data_pipeline.bars import finalized_crypto
from data_pipeline.scanner import ScanSettings, SpotScanner


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
                 poll_seconds: int | None = None, scan_settings: ScanSettings | None = None) -> None:
        self.exchange_id = exchange_id or os.getenv("MARKET_EXCHANGE", "binance")
        self.symbols = symbols or [s.strip() for s in os.getenv("MARKET_SYMBOLS", "BTC/USDT").split(",") if s.strip()]
        self.poll_seconds = max(15, int(poll_seconds or os.getenv("MARKET_POLL_SECONDS", "60")))
        self.scanner = SpotScanner(scan_settings) if scan_settings else None
        self.exchange: Any = None
        self._markets_loaded_at = 0.0
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
        self._markets_loaded_at = asyncio.get_running_loop().time()

    async def _read_bar(self, symbol: str) -> dict:
        instrument = instrument_from_market(self.exchange, symbol)
        rows = await self.exchange.fetch_ohlcv(symbol, "1m", limit=100)
        observed_at = utc_now()
        bars = finalized_crypto(rows, observed_at)
        return {"instrument": instrument, "bars": bars, "observed_at": observed_at}

    async def fetch_once(self, symbol: str) -> None:
        await self._connect()
        if not event_engine.put(Event("NEW_BAR", await self._read_bar(symbol))):
            raise RuntimeError("paper event loop unavailable")

    async def _scan_once(self, held: set[str]) -> None:
        await self._connect()
        loop = asyncio.get_running_loop()
        if loop.time() - self._markets_loaded_at >= 1800:
            await self.exchange.load_markets(reload=True)
            self._markets_loaded_at = loop.time()
        if self.exchange.has.get("fetchTickers") is not True:
            raise ValueError("selected exchange has no native batched public tickers")
        try:
            tickers = await self.exchange.fetch_tickers()
        except Exception as exc:
            if not held:
                raise
            tickers = {}
            ticker_error = self._error_message(exc)
        else:
            ticker_error = None
        selected, quotes, summary = self.scanner.select(self.exchange.markets, tickers, utc_now(), held)
        deadline = loop.time() + min(35, self.poll_seconds)
        readings, attempted, errors = [], [], {}
        for symbol in selected:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            if symbol not in held and loop.time() < self._retry_after.get(symbol, 0):
                continue
            attempted.append(symbol)
            try:
                async with asyncio.timeout(remaining):
                    reading = await self._read_bar(symbol)
                reading["quote"] = quotes.get(symbol)
                readings.append(reading)
                self._failures.pop(symbol, None)
                self._retry_after.pop(symbol, None)
            except Exception as exc:
                errors[symbol] = self._error_message(exc)
                self._backoff(symbol, loop.time())
        self.scanner.record_attempts(attempted)
        summary.update(status="degraded" if ticker_error else "ready", error=ticker_error,
                       attempted=len(attempted), completed=len(readings), failed=len(errors),
                       held_priority=len(held))
        payload = {"readings": readings, "errors": errors, "exchange": self.exchange_id,
                   "summary": summary, "settings": self.scanner.settings, "observed_at": utc_now()}
        if not event_engine.put(Event("SCAN_BATCH", payload)):
            raise RuntimeError("paper event loop unavailable")

    @staticmethod
    def _error_message(exc: Exception) -> str:
        return str(exc) if type(exc) in (ValueError, RuntimeError) else type(exc).__name__

    def _backoff(self, symbol: str, now: float) -> None:
        failures = self._failures.get(symbol, 0) + 1
        self._failures[symbol] = failures
        self._retry_after[symbol] = now + min(300, 2 ** min(failures, 8))

    async def _scan_loop(self, held_symbols: Callable[[], set[str]]) -> None:
        loop = asyncio.get_running_loop()
        while self.active:
            started = loop.time()
            try:
                await self._scan_once(held_symbols())
                self._failures.pop("*", None)
                delay = self.poll_seconds
            except Exception as exc:
                message = self._error_message(exc)
                event_engine.put(Event("SCAN_ERROR", {"error": message, "quote": self.scanner.settings.quote}))
                self._backoff("*", loop.time())
                delay = max(self.poll_seconds, self._retry_after["*"] - loop.time())
                await self._close_exchange()
            await asyncio.sleep(max(1, started + delay - loop.time()))

    async def fetch_ohlcv_loop(self, held_symbols: Callable[[], set[str]] = set) -> None:
        self.active = True
        loop = asyncio.get_running_loop()
        try:
            if self.scanner:
                await self._scan_loop(held_symbols)
                return
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
                        self._backoff(symbol, loop.time())
                        message = self._error_message(exc)
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

    def start(self, held_symbols: Callable[[], set[str]] = set) -> asyncio.Task:
        return asyncio.create_task(self.fetch_ohlcv_loop(held_symbols))

    async def stop(self) -> None:
        self.active = False


market_feed = MarketFeed(scan_settings=ScanSettings.from_env())
