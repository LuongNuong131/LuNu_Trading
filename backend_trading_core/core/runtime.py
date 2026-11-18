"""Local public paper session. Account ledgers are keyed by quote currency."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from threading import RLock

from core.event_engine import Event
from core.micro import limits_from_env
from core.models import Signal, utc_now
from core.order_executor import OPEN, PaperExecutor
from core.pipeline import WARMUP_BARS, prepare_bar, process_bar
from core.risk_manager import quantize
from data_pipeline.bars import utc_ms
from db.duckdb_client import DuckDBClient

log = logging.getLogger(__name__)


class PaperRuntime:
    def __init__(self, db: DuckDBClient) -> None:
        self.db = db
        self.accounts: dict[str, PaperExecutor] = {}
        self.watchlist: dict[str, dict] = {}
        self.scan: dict | None = None
        self.micro_quote, self.micro_limits = limits_from_env()
        self.source = "CCXT public OHLCV"
        self.max_age = int(os.getenv("MARKET_MAX_AGE_SECONDS", "180"))
        if self.max_age < 60:
            raise ValueError("MARKET_MAX_AGE_SECONDS must be at least 60 for 1m bars")
        self.kill_override = os.getenv("PAPER_KILL_SWITCH")
        self.mode = "Paper dashboard · feed tắt"
        self._lock = RLock()

    def account(self, currency: str) -> PaperExecutor:
        with self._lock:
            if self.micro_quote and currency != self.micro_quote:
                raise ValueError("micro paper uses a single quote currency; select a separate paper database")
            if currency not in self.accounts:
                account = PaperExecutor(self.micro_limits, currency=currency, path=self.db.path)
                if self.kill_override is not None:
                    account.set_kill_switch(self.kill_override.strip().lower() in {"1", "true", "yes", "on"})
                account.reconcile()
                self.accounts[currency] = account
            return self.accounts[currency]

    def configure(self, exchange: str, symbols: list[str], enabled: bool, scan_quote: str | None = None) -> None:
        if self.micro_quote and scan_quote != self.micro_quote:
            raise ValueError("micro paper requires a scanner with its account quote")
        if enabled:
            self.mode = "Paper · máy quét spot" if scan_quote else "Paper · feed công khai"
        if scan_quote:
            self.scan = {"quote": scan_quote, "status": "waiting" if enabled else "disabled"}
        else:
            for symbol in symbols:
                key = f"{exchange}:{symbol}"
                self.watchlist[key] = {"symbol": key, "source": self.source,
                                       "status": "waiting" if enabled else "disabled"}
        currencies = self.db.paper_currencies()
        if scan_quote and any(currency != scan_quote for currency in currencies):
            raise ValueError("scanner requires a separate database for its quote currency")
        for currency in currencies:
            account = self.account(currency)
            if scan_quote and any(not key.startswith(f"{exchange}:") for key in account.positions):
                raise ValueError("scanner database contains open positions on another exchange")

    def priority_symbols(self, exchange: str) -> set[str]:
        prefix = f"{exchange}:"
        with self._lock:
            active = {key for account in self.accounts.values() for key in account.positions}
            active.update(order["symbol"] for account in self.accounts.values() for order in account.orders.values()
                          if order["status"] in OPEN)
            return {key.removeprefix(prefix) for key in active if key.startswith(prefix)}

    async def feed_error(self, event: Event) -> None:
        with self._lock:
            key = event.data["symbol"]
            watch = self.watchlist.setdefault(key, {"symbol": key, "source": self.source})
            watch.update(status="error", error=event.data["error"])
        log.warning("%s feed unavailable: %s", key, event.data["error"])

    async def scan_error(self, event: Event) -> None:
        with self._lock:
            self.scan = {**(self.scan or {}), **event.data, "status": "error"}
        log.warning("public spot scan failed: %s", event.data["error"])

    def _prepare(self, instrument, bars, now) -> dict | None:
        key = instrument.key
        watch = self.watchlist.setdefault(key, {"symbol": key, "source": self.source})
        watch.update(observed_at=now.isoformat(), error=None)
        if len(bars) <= WARMUP_BARS:
            watch.update(status="warm-up", error=f"{len(bars)} finalized bars; need at least {WARMUP_BARS + 1}")
            return None
        last = watch.get("event") or self.db.latest_signal(key)
        quote_close = utc_ms(bars["closed_ms"].iloc[-1]).isoformat()
        if last and quote_close <= last["quote_closed_at"]:
            if quote_close < last["quote_closed_at"]:
                watch.update(status="stale", event=last, error="feed timestamp went backwards")
            else:
                watch.update(status=last["data_status"], event=last)
            return None
        return prepare_bar(bars, instrument, self.source, paper_now=now, max_age_seconds=self.max_age)

    def _process(self, instrument, bars, now, account, signal, **scan_options) -> None:
        key = instrument.key
        other_quotes_fresh = all(self._status(self.watchlist.get(held, {}), now) == "ready"
                                 for held in account.positions if held != key)
        signal = process_bar(bars, instrument, account, self.source, paper_now=now,
                             max_age_seconds=self.max_age, other_quotes_fresh=other_quotes_fresh,
                             prepared=signal, **scan_options)
        self.watchlist[key].update(status=signal["data_status"], event=signal)
        signal_id = signal["signal_id"]
        self.db.insert_audit_event(signal_id, "SIGNAL_GENERATED", key, signal)
        if not scan_options or signal["signal"] != Signal.HOLD.value:
            self.db.insert_ai_log("deterministic_signal", key, signal["signal"], signal["reason"])
        if risk := signal.get("risk"):
            client_id = risk["client_order_id"]
            self.db.insert_audit_event(client_id, "RISK_DECISION", key, {"signal_id": signal_id, **risk})
            order = account.orders[client_id]
            self.db.insert_trade(client_id, key, signal["signal"], order["price"], order["amount"],
                                 order["status"], {"signal_id": signal_id, **risk})

    async def handle_new_bar(self, event: Event) -> None:
        with self._lock:
            instrument, bars, now = (event.data[field] for field in ("instrument", "bars", "observed_at"))
            if signal := self._prepare(instrument, bars, now):
                self._process(instrument, bars, now, self.account(instrument.currency), signal)

    @staticmethod
    def _screen(instrument, signal, quote, settings, account, now) -> dict:
        fresh = quote is not None and quote.fresh(now, settings.max_quote_age_seconds)
        spread = quote.spread_bps if fresh else settings.max_spread_bps
        price = quote.mid if fresh else signal["quote"]
        executable = quantize(price * (1 + account.risk.one_way_adjustment(spread)), instrument.tick, up=True)
        venue_min = max(instrument.minimum_notional, instrument.minimum_amount * executable)
        stop = account.risk.stop_distance(executable, signal["indicators"].get("atr", 0.0))
        cost_bps = 2 * account.limits.fee_rate * 10_000 + spread + 2 * account.limits.slippage_bps
        cost_bps += 20_000 * instrument.tick / price
        ratio = 20_000 * stop / price / cost_bps if cost_bps else 0.0
        blocked = None
        if not fresh:
            blocked = "fresh bid/ask required for scanner entries"
        elif quote.volume < settings.min_volume:
            blocked = "24h quote volume below scanner floor"
        elif spread > settings.max_spread_bps:
            blocked = "spread above scanner ceiling"
        elif venue_min <= 0:
            blocked = "venue minimum order metadata unavailable"
        elif ratio < settings.min_target_cost_ratio:
            blocked = "modeled target too small versus round-trip costs"
        return {"reference": "ticker midpoint" if fresh else "finalized close with fallback spread",
                "price": price, "spread_bps": spread, "quote_volume": quote.volume if fresh else None,
                "quote_observed_at": quote.observed_at.isoformat() if fresh else None,
                "venue_minimum": venue_min, "round_trip_cost_bps": cost_bps,
                "target_cost_ratio": ratio, "entry_block_reason": blocked}

    async def handle_scan(self, event: Event) -> None:
        with self._lock:
            data = event.data
            now, settings = data["observed_at"], data["settings"]
            self.scan = data["summary"].copy()
            account = self.account(settings.quote)
            for symbol, error in data["errors"].items():
                key = f"{data['exchange']}:{symbol}"
                watch = self.watchlist.setdefault(key, {"symbol": key, "source": self.source})
                watch.update(status="error", error=error)
            candidates = []
            for reading in data["readings"]:
                instrument, bars = reading["instrument"], reading["bars"]
                if instrument.currency != settings.quote:
                    continue
                signal = self._prepare(instrument, bars, now)
                if signal is None:
                    continue
                screen = self._screen(instrument, signal, reading["quote"], settings, account, now)
                signal["scanner"] = screen
                holding = instrument.key in account.positions
                eligible_buy = (signal["signal"] == Signal.BUY.value and signal["data_status"] == "ready"
                                and screen["entry_block_reason"] is None)
                rank = (not holding, not eligible_buy, -screen["target_cost_ratio"],
                        -(screen["quote_volume"] or 0), instrument.key)
                candidates.append((rank, instrument, bars, signal))
            candidates.sort(key=lambda row: row[0])
            self.scan["processed"] = len(candidates)
            self.scan["buy_signals"] = sum(signal["signal"] == Signal.BUY.value for _, _, _, signal in candidates)
            self.scan["eligible_buys"] = 0
            for _, instrument, bars, signal in candidates:
                screen = signal["scanner"]
                # Held positions are processed first and can change the entry budget.
                position_cap = account.equity * account.limits.max_position_notional_pct
                if screen["entry_block_reason"] is None and screen["venue_minimum"] > position_cap:
                    screen["entry_block_reason"] = "venue minimum order exceeds position cap"
                if (signal["signal"] == Signal.BUY.value and signal["data_status"] == "ready"
                        and screen["entry_block_reason"] is None):
                    self.scan["eligible_buys"] += 1
                self._process(instrument, bars, now, account, signal, scanning=True,
                              scan_quote=screen["price"], spread_bps=screen["spread_bps"],
                              entry_block_reason=screen["entry_block_reason"])

    def _status(self, item: dict, now: datetime) -> str:
        status = item.get("status", "waiting")
        if status == "ready":
            close_at = datetime.fromisoformat(item["event"]["closed_at"])
            if (now - close_at).total_seconds() > self.max_age:
                return "stale"
        return status

    def snapshot(self, *, include_signals: bool = False) -> dict:
        with self._lock:
            now = utc_now()
            watches = [{**item, "status": self._status(item, now)} for item in self.watchlist.values()]
            accounts = sorted(self.accounts.values(), key=lambda account: account.currency)
            if not accounts:
                accounts = [self.account(self.micro_quote or "USDT")]
            result = {"mode": self.mode, "source_note": "Spot long-only · SQLite local · mỗi đồng tiền một sổ",
                      "accounts": [account.snapshot() for account in accounts], "watchlist": watches}
            if self.scan is not None:
                result["scanner"] = self.scan.copy()
            if include_signals:
                result["signals"] = [json.loads(row["payload"]) for row in self.db.fetch_recent_audit_events(500)
                                     if row["event_type"] == "SIGNAL_GENERATED"]
                result["signals"] = [event for event in result["signals"] if "signal_id" in event]
            return result

    def legacy_snapshot(self) -> dict:
        result = self.snapshot()
        accounts = result["accounts"]
        primary = next((account for account in accounts if account["currency"] == "USDT"), accounts[0])
        return {**primary, "accounts": accounts, "watchlist": result["watchlist"],
                "paper_mode": result["mode"], **({"scanner": result["scanner"]} if "scanner" in result else {})}
