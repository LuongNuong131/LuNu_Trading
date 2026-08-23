import duckdb
import os
from datetime import datetime

# File cơ sở dữ liệu sẽ được tạo ngay trong thư mục db/
DB_PATH = os.path.join(os.path.dirname(__file__), "omni_quant.duckdb")

class DuckDBClient:
    def __init__(self):
        # Kết nối tới file DuckDB, tự động tạo nếu chưa có
        self.conn = duckdb.connect(DB_PATH)
        self._init_tables()

    def _init_tables(self):
        """Khởi tạo cấu trúc bảng cho Lịch sử lệnh và Log của AI"""
        self.conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS log_seq;
            
            CREATE TABLE IF NOT EXISTS ai_debate_logs (
                id INTEGER DEFAULT nextval('log_seq'),
                timestamp TIMESTAMP,
                agent_name VARCHAR,
                symbol VARCHAR,
                decision VARCHAR,
                reasoning TEXT
            );
            
            CREATE TABLE IF NOT EXISTS trade_history (
                order_id VARCHAR,
                timestamp TIMESTAMP,
                symbol VARCHAR,
                side VARCHAR,
                price DOUBLE,
                amount DOUBLE,
                status VARCHAR
            );
        """)

    def insert_ai_log(self, agent_name: str, symbol: str, decision: str, reasoning: str):
        now = datetime.now()
        self.conn.execute(
            "INSERT INTO ai_debate_logs (timestamp, agent_name, symbol, decision, reasoning) VALUES (?, ?, ?, ?, ?)",
            [now, agent_name, symbol, decision, reasoning]
        )
        print(f"[DuckDB] Đã lưu log của {agent_name} cho mã {symbol}")

    def insert_trade(self, order_id: str, symbol: str, side: str, price: float, amount: float, status: str):
        now = datetime.now()
        self.conn.execute(
            "INSERT INTO trade_history (order_id, timestamp, symbol, side, price, amount, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [order_id, now, symbol, side, price, amount, status]
        )

    def fetch_recent_logs(self, limit: int = 50):
        # Xuất thẳng ra Pandas/Polars DataFrame cực nhanh
        return self.conn.execute("SELECT * FROM ai_debate_logs ORDER BY timestamp DESC LIMIT ?", [limit]).df()

# Khởi tạo Singleton pattern để dùng chung toàn hệ thống
db_client = DuckDBClient()