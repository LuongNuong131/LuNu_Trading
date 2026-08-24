"""Small durable audit store with a standard-library SQLite fallback."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock


DB_PATH = Path(os.getenv("OMNI_QUANT_DB_PATH", Path(__file__).with_name("omni_quant.sqlite3")))


class DuckDBClient:
    """Compatibility name retained for the UI; storage is SQLite by default."""

    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        with self._lock, self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS ai_debate_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reasoning TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trade_history (
                    order_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    amount REAL NOT NULL,
                    status TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    symbol TEXT,
                    payload TEXT NOT NULL
                );
            """)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def insert_ai_log(self, agent_name: str, symbol: str, decision: str, reasoning: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("INSERT INTO ai_debate_logs(timestamp, agent_name, symbol, decision, reasoning) VALUES (?, ?, ?, ?, ?)", (self._now(), agent_name, symbol, decision, reasoning))

    def insert_trade(self, order_id: str, symbol: str, side: str, price: float, amount: float, status: str, metadata: dict | None = None) -> None:
        with self._lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO trade_history(order_id, timestamp, symbol, side, price, amount, status, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (order_id, self._now(), symbol, side, price, amount, status, json.dumps(metadata or {})))

    def insert_audit_event(self, event_id: str, event_type: str, symbol: str | None, payload: dict) -> None:
        with self._lock, self.conn:
            self.conn.execute("INSERT OR IGNORE INTO audit_events(event_id, timestamp, event_type, symbol, payload) VALUES (?, ?, ?, ?, ?)", (event_id, self._now(), event_type, symbol, json.dumps(payload, default=str)))

    def fetch_recent_audit_events(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self.conn.execute("SELECT * FROM audit_events ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def fetch_recent_logs(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self.conn.execute("SELECT * FROM ai_debate_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    get_recent_ai_logs = fetch_recent_logs

    def fetch_recent_trades(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self.conn.execute("SELECT * FROM trade_history ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]


_db_path = os.getenv("OMNI_QUANT_DB_PATH", str(DB_PATH))
db_client = DuckDBClient(_db_path)
