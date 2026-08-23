from __future__ import annotations

import os

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from core.order_executor import executor
from db.duckdb_client import db_client


app = FastAPI(title="LuNu Trading", version="0.2.0")
_origins = [item.strip() for item in os.getenv("OMNI_QUANT_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if item.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_credentials=False, allow_methods=["GET"], allow_headers=["Content-Type"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mode": "paper", "live_execution": False}


@app.get("/api/logs")
def get_logs(limit: int = Query(30, ge=1, le=500)) -> dict:
    return {"success": True, "data": db_client.fetch_recent_logs(limit)}


@app.get("/api/trades")
def get_trades(limit: int = Query(30, ge=1, le=500)) -> dict:
    return {"success": True, "data": db_client.fetch_recent_trades(limit)}


@app.get("/api/stats")
def get_stats() -> dict:
    snapshot = executor.snapshot()
    return {"success": True, "data": snapshot}


@app.get("/api/positions")
def get_positions() -> dict:
    return {"success": True, "data": executor.snapshot()["open_positions"]}
