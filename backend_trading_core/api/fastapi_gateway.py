from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from core.order_executor import executor
from db.duckdb_client import db_client


app = FastAPI(title="LuNu Trading", version="0.3.0")
_origins = [item.strip() for item in os.getenv("OMNI_QUANT_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://localhost:1420,http://127.0.0.1:1420,tauri://localhost").split(",") if item.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_credentials=False, allow_methods=["GET"], allow_headers=["Content-Type"])


@app.get("/")
def root() -> dict:
    return {"status": "online", "mode": "paper", "live_execution": False}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mode": "paper", "live_execution": False}


@app.get("/api/logs")
def get_logs(limit: int = Query(30, ge=1, le=500)) -> dict:
    return {"success": True, "data": db_client.fetch_recent_logs(limit)}


@app.get("/api/trades")
def get_trades(limit: int = Query(30, ge=1, le=500)) -> dict:
    return {"success": True, "data": db_client.fetch_recent_trades(limit)}


@app.get("/api/audit")
def get_audit(limit: int = Query(100, ge=1, le=500)) -> dict:
    return {"success": True, "data": db_client.fetch_recent_audit_events(limit)}


@app.get("/api/stats")
def get_stats() -> dict:
    return {"success": True, "data": executor.snapshot()}


@app.get("/api/positions")
def get_positions() -> dict:
    return {"success": True, "data": executor.snapshot()["open_positions"]}


@app.websocket("/ws/snapshot")
async def snapshot_stream(websocket: WebSocket) -> None:
    """Read-only state stream for the desktop UI; incoming messages are ignored."""
    await websocket.accept()
    try:
        while True:
            await websocket.send_json({"type": "snapshot", "data": executor.snapshot()})
            await asyncio.sleep(2)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
