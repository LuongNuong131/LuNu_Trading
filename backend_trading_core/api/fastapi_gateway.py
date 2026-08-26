from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from core.order_executor import executor
from data_pipeline.market_feed import market_feed
from db.duckdb_client import db_client

app = FastAPI(
    title="LuNu Trading",
    version="0.4.0",
    description="Read-only monitoring API for the fail-closed paper-trading engine.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Content-Type"],
)


def _runtime_status() -> dict[str, Any]:
    return {
        "mode": "paper",
        "live_execution": settings.live_trading_enabled,
        "market_feed_enabled": settings.market_feed_enabled,
        "market_feed_active": market_feed.active,
        "exchange": market_feed.exchange_id,
        "symbols": list(market_feed.symbols),
        "database": str(db_client.path),
    }


@app.get("/")
def root() -> dict[str, Any]:
    return {"status": "online", **_runtime_status()}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", **_runtime_status()}


@app.get("/api/logs")
def get_logs(limit: int = Query(30, ge=1, le=500)) -> dict[str, Any]:
    return {"success": True, "data": db_client.fetch_recent_logs(limit)}


@app.get("/api/trades")
def get_trades(limit: int = Query(30, ge=1, le=500)) -> dict[str, Any]:
    return {"success": True, "data": db_client.fetch_recent_trades(limit)}


@app.get("/api/audit")
def get_audit(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    return {"success": True, "data": db_client.fetch_recent_audit_events(limit)}


@app.get("/api/stats")
def get_stats() -> dict[str, Any]:
    return {"success": True, "data": executor.snapshot()}


@app.get("/api/positions")
def get_positions() -> dict[str, Any]:
    return {"success": True, "data": executor.snapshot()["open_positions"]}


@app.websocket("/ws/snapshot")
async def snapshot_stream(websocket: WebSocket) -> None:
    """Read-only state stream for the desktop UI; incoming messages are ignored."""
    await websocket.accept()
    try:
        while True:
            await websocket.send_json({"type": "snapshot", "data": executor.snapshot()})
            await asyncio.sleep(settings.snapshot_interval_seconds)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
