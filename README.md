# LuNu Trading

LuNu Trading là **workspace nghiên cứu và paper-trading**. Luồng mặc định đọc dữ liệu OHLCV công khai, tính tín hiệu kỹ thuật xác định, áp dụng các giới hạn rủi ro rõ ràng và ghi nhận lệnh mô phỏng. Hệ thống **không gửi lệnh thật lên sàn**.

> Đây là phần mềm nghiên cứu, không phải cam kết lợi nhuận. Thị trường crypto biến động mạnh; phí, trượt giá và chất lượng dữ liệu đều ảnh hưởng đến kết quả. Hiệu suất lịch sử hoặc mô phỏng không bảo đảm kết quả tương lai.

## Điểm đã nâng cấp

| Khu vực | Trước đây | Hiện tại |
|---|---|---|
| Cấu hình | Rải rác trong nhiều module, host/cổng hard-code | Tập trung trong `backend_trading_core/config.py`, có kiểm tra kiểu và giới hạn |
| Equity | Định giá vị thế theo giá vào lệnh | Mark-to-market theo giá mới nhất nhận được |
| Dashboard | Trạng thái runtime tối thiểu | Health API trả về chế độ, feed, sàn, symbols và đường dẫn database |
| Lưu vết | Chủ yếu ghi tín hiệu/lệnh vào | Ghi cả lệnh đóng vị thế, PnL, lý do đóng và lỗi feed |
| Market feed | Có thể khởi tạo lặp cùng lúc | Chặn start trùng, kiểm tra exchange không hợp lệ, vẫn retry có backoff |
| Kiểm thử | 6 test lõi | Thêm test hồi quy cho mark-to-market và unrealized PnL |

## Yêu cầu

Cần Python 3.11 trở lên. Python 3.14 được hỗ trợ thông qua nhánh NumPy tương ứng trong `requirements.txt`. Feed thị trường dùng CCXT; nếu chỉ chạy API/dashboard thì feed mặc định đang tắt và không cần gọi mạng đến sàn.

## Cài đặt và chạy trên Linux/macOS

Từ thư mục gốc repository:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend_trading_core/requirements.txt
cp .env.example .env
PYTHONPATH=backend_trading_core python backend_trading_core/run_backend.py
```

API sẽ lắng nghe tại `http://127.0.0.1:8000`. Nếu muốn chạy từ thư mục `backend_trading_core`, dùng:

```bash
cd backend_trading_core
python run_backend.py
```

`run_backend.py` tự thêm thư mục backend vào `sys.path`, vì vậy cách chạy thứ hai không cần tự đặt `PYTHONPATH`.

## Cài đặt và chạy trên Windows

Cách nhanh nhất là mở CMD tại thư mục gốc repository và chạy:

```bat
setup_windows.cmd
.venv\Scripts\python.exe backend_trading_core\run_backend.py
```

Hoặc chạy thủ công trong CMD:

```bat
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r backend_trading_core\requirements.txt
copy .env.example .env
.venv\Scripts\python.exe backend_trading_core\run_backend.py
```

Trong PowerShell, kích hoạt bằng:

```powershell
.\.venv\Scripts\Activate.ps1
python .\backend_trading_core\run_backend.py
```

Nếu PowerShell chặn script activation, có thể bỏ qua activation và gọi trực tiếp `.venv\Scripts\python.exe`. Không dùng `source`, `PYTHONPATH=... command` hoặc đường dẫn `.venv/bin` trong Windows.

## Chế độ chạy

Mặc định `MARKET_FEED_ENABLED=false`, nên backend khởi động ổn định ở chế độ API/dashboard paper và không gọi dữ liệu sàn. Để bật feed OHLCV read-only, chỉnh `.env`:

```dotenv
MARKET_FEED_ENABLED=true
MARKET_EXCHANGE=binance
MARKET_SYMBOLS=BTC/USDT,ETH/USDT
MARKET_POLL_SECONDS=60
```

Feed chỉ phát sinh dữ liệu khi có nến 1 phút mới. Khi lỗi mạng hoặc lỗi sàn, hệ thống ghi `FEED_ERROR`, đóng client cũ và retry với exponential backoff có giới hạn. Không đặt API key để chạy feed public OHLCV; các API key dùng cho giao dịch thật chưa được hỗ trợ trong core hiện tại.

Có thể đổi host, cổng, database và chu kỳ snapshot:

```dotenv
API_HOST=127.0.0.1
API_PORT=8000
OMNI_QUANT_DB_PATH=data/omni_quant.sqlite3
SNAPSHOT_INTERVAL_SECONDS=2
LOG_LEVEL=INFO
```

Đường dẫn tương đối của `OMNI_QUANT_DB_PATH` được tính từ thư mục gốc repository. Database runtime là **SQLite** dù class tương thích vẫn mang tên `DuckDBClient`.

## Kiểm tra hệ thống

Sau khi backend chạy, mở các endpoint sau:

| Endpoint | Mục đích |
|---|---|
| `GET /` | Kiểm tra backend đang online và xem runtime mode |
| `GET /health` | Health check cho monitoring |
| `GET /api/stats` | Vốn, equity, realized/unrealized PnL và vị thế |
| `GET /api/positions` | Danh sách vị thế đang mở |
| `GET /api/trades?limit=30` | Lệnh mở/đóng gần nhất |
| `GET /api/audit?limit=100` | Audit event của bar, signal, risk và feed |
| `GET /api/logs?limit=30` | Log tín hiệu xác định |
| `WS /ws/snapshot` | Snapshot read-only định kỳ cho UI |

Ví dụ:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/stats
```

Dừng backend bằng `Ctrl+C`. Database sẽ được tạo tự động ở `data/omni_quant.sqlite3` nếu không cấu hình đường dẫn khác.

## Chạy test

Từ thư mục gốc:

```bash
PYTHONPATH=backend_trading_core python -m unittest discover -s tests -v
python -m compileall -q backend_trading_core
```

Trên Windows CMD:

```bat
set PYTHONPATH=backend_trading_core
python -m unittest discover -s tests -v
python -m compileall -q backend_trading_core
```

## Nghiên cứu và backtest

Dùng `backtest.runner.run_backtest` cho một lần chạy lịch sử và `backtest.validation.walk_forward_validate` cho các cửa sổ kiểm định forward-only. Mọi kết quả chỉ là chẩn đoán nghiên cứu; cần kiểm soát look-ahead bias, phí, slippage và chất lượng dữ liệu trước khi rút ra kết luận.

## LLM tùy chọn

Module advisory trong `backend_trading_core/llm/hydra_matrix.py` không nằm trên order path mặc định. Nếu cần nghiên cứu LLM, cài thêm:

```bash
python -m pip install -r backend_trading_core/requirements-llm.txt
```

LLM chỉ được phép cung cấp phân tích tham khảo; nó không có quyền tự tạo lệnh trong core hiện tại.

## Ranh giới an toàn cho live trading

Không thêm lệnh thật vào process hiện tại. Một live connector riêng phải được review độc lập và có testnet, quyền API tối thiểu, kill switch, client order ID idempotent, reconciliation, bảo vệ stale data, giới hạn lỗ tối đa, audit log và bước phê duyệt thủ công. Nếu API key từng bị commit vào repository public, cần thu hồi và tạo key mới ngay.

## Nguồn và giấy phép

Các repository nguồn và ranh giới giấy phép được ghi trong [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). Core hiện tại không copy nguyên cây mã nguồn GPL, LGPL hoặc AGPL vào repository này.
