"""Local public paper session. Account ledgers are keyed by quote currency."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from threading import RLock

from core.event_engine import Event
from core.models import utc_now
from core.order_executor import PaperExecutor
from core.pipeline import WARMUP_BARS, process_bar
from data_pipeline.bars import utc_ms
from db.duckdb_client import DuckDBClient

log = logging.getLogger(__name__)


class PaperRuntime:
    def __init__(self, db: DuckDBClient) -> None:
        self.db = db
        self.accounts: dict[str, PaperExecutor] = {}
        self.watchlist: dict[str, dict] = {}
        self.source = "CCXT public OHLCV"
        self.max_age = int(os.getenv("MARKET_MAX_AGE_SECONDS", "180"))
        if self.max_age < 60:
            raise ValueError("MARKET_MAX_AGE_SECONDS must be at least 60 for 1m bars")
        self.kill_override = os.getenv("PAPER_KILL_SWITCH")
        self.mode = "Paper dashboard · feed tắt"
        self._lock = RLock()

    def account(self, currency: str) -> PaperExecutor:
        with self._lock:
            if currency not in self.accounts:
                account = PaperExecutor(currency=currency, path=self.db.path)
                if self.kill_override is not None:
                    account.set_kill_switch(self.kill_override.strip().lower() in {"1", "true", "yes", "on"})
                account.reconcile()
                self.accounts[currency] = account
            return self.accounts[currency]

    def configure(self, exchange: str, symbols: list[str], enabled: bool) -> None:
        if enabled:
            self.mode = "Paper · feed công khai"
        for symbol in symbols:
            self.watchlist[f"{exchange}:{symbol}"] = {"symbol": f"{exchange}:{symbol}", "source": self.source,
                                                       "status": "waiting" if enabled else "disabled"}
        for currency in self.db.paper_currencies():
            self.account(currency)

    async def feed_error(self, event: Event) -> None:
        with self._lock:
            key = event.data["symbol"]
            watch = self.watchlist.setdefault(key, {"symbol": key, "source": self.source})
            watch.update(status="error", error=event.data["error"])
        log.warning("%s feed unavailable: %s", key, event.data["error"])

    async def handle_new_bar(self, event: Event) -> None:
        instrument, bars, now = (event.data[field] for field in ("instrument", "bars", "observed_at"))
        with self._lock:
            key = instrument.key
            watch = self.watchlist.setdefault(key, {"symbol": key, "source": self.source})
            watch.update(observed_at=now.isoformat(), error=None)
            if len(bars) <= WARMUP_BARS:
                watch.update(status="warm-up", error=f"{len(bars)} finalized bars; need at least {WARMUP_BARS + 1}")
                return
            last = watch.get("event") or self.db.latest_signal(key)
            quote_close = utc_ms(bars["closed_ms"].iloc[-1])
            if last and quote_close.isoformat() <= last["quote_closed_at"]:
                if quote_close.isoformat() < last["quote_closed_at"]:
                    watch.update(status="stale", event=last, error="feed timestamp went backwards")
                else:
                    watch.update(status=last["data_status"], event=last)
                return
            account = self.account(instrument.currency)
            other_quotes_fresh = all(
                self._status(self.watchlist.get(held, {}), now) == "ready"
                for held in account.positions if held != key
            )
            signal = process_bar(bars, instrument, account, self.source, paper_now=now,
                                 max_age_seconds=self.max_age, other_quotes_fresh=other_quotes_fresh)
            watch.update(status=signal["data_status"], event=signal)
            signal_id = signal["signal_id"]
            self.db.insert_audit_event(signal_id, "SIGNAL_GENERATED", key, signal)
            self.db.insert_ai_log("deterministic_signal", key, signal["signal"], signal["reason"])
            if risk := signal.get("risk"):
                client_id = risk["client_order_id"]
                self.db.insert_audit_event(client_id, "RISK_DECISION", key, {"signal_id": signal_id, **risk})
                order = account.orders[client_id]
                self.db.insert_trade(client_id, key, signal["signal"], order["price"], order["amount"],
                                     order["status"], {"signal_id": signal_id, **risk})

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
                accounts = [self.account("USDT")]
            result = {"mode": self.mode, "source_note": "Spot long-only · SQLite local · mỗi đồng tiền một sổ",
                      "accounts": [account.snapshot() for account in accounts], "watchlist": watches}
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
                "paper_mode": result["mode"]}
