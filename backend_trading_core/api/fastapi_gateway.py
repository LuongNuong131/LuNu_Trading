from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from db.duckdb_client import db_client
from core.order_executor import executor

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/logs")
def get_logs(limit: int = 30):
    try:
        logs = db_client.get_recent_ai_logs(limit)
        return {"success": True, "data": logs}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/stats")
def get_stats():
    """Lấy dữ liệu Két sắt và lệnh đang mở để UI hiển thị"""
    return {
        "success": True,
        "data": {
            "capital": executor.capital,
            "open_positions": len(executor.positions)
        }
    }