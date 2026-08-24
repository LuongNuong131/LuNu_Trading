from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import random

# Khởi tạo ứng dụng FastAPI
app = FastAPI(
    title="LuNu Omni Quant System API", 
    description="Backend Gateway cho hệ thống giao dịch tự động",
    version="1.0.0"
)

# ---------------------------------------------------------
# CẤU HÌNH CẤP GIẤY THÔNG HÀNH CORS (Sửa lỗi ngắt kết nối)
# ---------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",     # Cổng mặc định của Vite/Vue 3 chạy dev
        "http://127.0.0.1:1420",     # Alternative localhost IP
        "http://localhost:5173",     # Cổng dự phòng của Vite
        "tauri://localhost"          # Cổng nội bộ khi build ra app Tauri Desktop
    ],
    allow_credentials=True,
    allow_methods=["*"],             # Cho phép mọi phương thức (GET, POST, PUT, DELETE,...)
    allow_headers=["*"],             # Cho phép mọi Headers
)

# ---------------------------------------------------------
# CÁC API ENDPOINT MẶC ĐỊNH & MOCK DATA CHO UI
# ---------------------------------------------------------

@app.get("/")
def read_root():
    """API kiểm tra trạng thái sức khỏe của Backend"""
    return {
        "status": "online", 
        "message": "Trung Tình Backend đang bốc lửa chờ lệnh!",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/logs")
def get_system_logs(limit: int = 30):
    """
    API lấy dữ liệu Log hệ thống.
    Hiện tại trả về dữ liệu giả lập (Mock) để Frontend hiển thị trước.
    Sau này sẽ nối với DB/DuckDB để lấy log thật.
    """
    mock_logs = []
    log_levels = ["INFO", "INFO", "INFO", "WARNING", "ERROR"]
    
    for i in range(limit):
        mock_logs.append({
            "id": i + 1,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": random.choice(log_levels),
            "message": f"Hệ thống đang quét tín hiệu thị trường... [Luồng {random.randint(1, 100)}]"
        })
        
    return {
        "success": True,
        "count": len(mock_logs),
        "data": mock_logs
    }

@app.get("/api/stats")
def get_system_stats():
    """
    API cung cấp dữ liệu thống kê tổng quan và danh sách AI Agents.
    Dữ liệu giả lập (Mock) để UI hiển thị giao diện.
    """
    return {
        "success": True,
        "data": {
            "total_pnl": 1250.50,
            "win_rate": 68.5,
            "total_trades": 142,
            "active_agents": [
                {
                    "id": "agent_1",
                    "name": "Alpha-101", 
                    "role": "analyzer", # Từ khóa 'analyzer' để App.vue chọn Icon
                    "status": "active"
                },
                {
                    "id": "agent_2",
                    "name": "Meme-Sniper", 
                    "role": "trader", # Từ khóa 'trader'
                    "status": "active"
                },
                {
                    "id": "agent_3",
                    "name": "Risk-Guard", 
                    "role": "risk", # Từ khóa 'risk'
                    "status": "active"
                }
            ]
        }
    }

# Các route thực tế sẽ được thêm vào sau (Ví dụ)
# @app.post("/api/trade/start")
# @app.get("/api/portfolio/status")