"""Screen public spot tickers before fetching rate-limited candle histories."""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from math import isfinite

STABLE_BASES = {"USD", "USDT", "USDC", "DAI", "USDS", "FDUSD", "PYUSD", "TUSD", "USDE", "USD1", "USDP", "RLUSD",
                "EUR", "EURC", "EURS", "EURT", "GBP", "AUD", "CAD", "CHF", "JPY", "BRL"}


@dataclass(frozen=True)
class ScanSettings:
    quote: str
    batch_size: int = 20
    min_volume: float = 250_000
    max_spread_bps: float = 30
    max_quote_age_seconds: float = 45
    min_target_cost_ratio: float = 3

    def __post_init__(self) -> None:
        if not self.quote.isascii() or not self.quote.isalnum():
            raise ValueError("invalid MARKET_SCAN_QUOTE")
        if not 1 <= self.batch_size <= 60:
            raise ValueError("MARKET_SCAN_BATCH_SIZE must be between 1 and 60")
        if not all(isfinite(value) and value > 0 for value in
                   (self.min_volume, self.max_spread_bps, self.max_quote_age_seconds, self.min_target_cost_ratio)):
            raise ValueError("invalid scanner limits")

    @classmethod
    def from_env(cls) -> ScanSettings | None:
        quote = os.getenv("MARKET_SCAN_QUOTE", "").strip().upper()
        if not quote:
            return None
        return cls(quote, int(os.getenv("MARKET_SCAN_BATCH_SIZE", "20")),
                   float(os.getenv("MARKET_SCAN_MIN_QUOTE_VOLUME", "250000")),
                   float(os.getenv("MARKET_SCAN_MAX_SPREAD_BPS", "30")))


@dataclass(frozen=True)
class Quote:
    mid: float
    spread_bps: float
    volume: float
    observed_at: datetime

    @classmethod
    def from_ticker(cls, ticker: dict, received_at: datetime) -> Quote | None:
        bid, ask = ticker.get("bid"), ticker.get("ask")
        if not all(isinstance(value, (int, float)) and isfinite(value) and value > 0 for value in (bid, ask)):
            return None
        if ask < bid:
            return None
        mid = bid + (ask - bid) / 2
        volume = ticker.get("quoteVolume")
        if volume is None:
            base_volume = ticker.get("baseVolume")
            if isinstance(base_volume, (int, float)) and isfinite(base_volume):
                vwap = ticker.get("vwap")
                reference = vwap if isinstance(vwap, (int, float)) and isfinite(vwap) and vwap > 0 else mid
                volume = base_volume * reference
        if not isinstance(volume, (int, float)) or not isfinite(volume) or volume < 0:
            volume = 0.0
        timestamp = ticker.get("timestamp")
        if timestamp is None:
            observed_at = received_at
        elif not isinstance(timestamp, (int, float)) or not isfinite(timestamp):
            return None
        else:
            try:
                observed_at = datetime.fromtimestamp(timestamp / 1000, received_at.tzinfo)
            except (OverflowError, OSError, ValueError):
                return None
        return cls(mid, (ask - bid) / mid * 10_000, volume, observed_at)

    def fresh(self, now: datetime, max_age: float) -> bool:
        return 0 <= (now - self.observed_at).total_seconds() <= max_age


class SpotScanner:
    def __init__(self, settings: ScanSettings) -> None:
        self.settings = settings
        self._last_scanned: dict[str, int] = {}
        self._round = 0

    def select(self, markets: dict, tickers: dict, received_at: datetime,
               held: set[str]) -> tuple[list[str], dict[str, Quote], dict]:
        settings = self.settings
        universe = {symbol: market for symbol, market in markets.items()
                    if market.get("spot") and market.get("active") is not False and market.get("quote") == settings.quote}
        bases = {market["base"] for market in universe.values()}
        quotes = {}
        eligible = []
        excluded = Counter()
        for symbol, market in universe.items():
            base = market["base"]
            leveraged = any(base.endswith(suffix) and base[:-len(suffix)] in bases
                            for suffix in ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S"))
            quote = Quote.from_ticker(tickers.get(symbol, {}), received_at)
            if quote is not None and quote.fresh(received_at, settings.max_quote_age_seconds):
                quotes[symbol] = quote
            if base in STABLE_BASES or market.get("leveraged") or leveraged:
                excluded["stable or leveraged base"] += 1
            elif symbol not in quotes:
                excluded["missing or stale bid/ask"] += 1
            elif quote.volume < settings.min_volume:
                excluded["low 24h quote volume"] += 1
            elif quote.spread_bps > settings.max_spread_bps:
                excluded["spread too wide"] += 1
            else:
                eligible.append(symbol)
        priority = sorted(held)
        eligible.sort(key=lambda symbol: (self._last_scanned.get(symbol, -1),
                                          -quotes[symbol].volume, symbol))
        selected = priority + [symbol for symbol in eligible if symbol not in held][:max(0, settings.batch_size - len(priority))]
        return selected, quotes, {
            "quote": settings.quote, "universe": len(universe), "quoted": len(quotes), "eligible": len(eligible),
            "excluded": dict(excluded), "selected": len(selected), "observed_at": received_at.isoformat(),
        }

    def record_attempts(self, symbols: list[str]) -> None:
        self._round += 1
        for symbol in symbols:
            self._last_scanned[symbol] = self._round
