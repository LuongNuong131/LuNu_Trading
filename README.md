# LuNu Trading

LuNu Trading chạy nghiên cứu và paper spot/cash từ dữ liệu tổng hợp, CSV cổ phiếu hoặc OHLCV crypto công khai. Tín hiệu, kiểm tra rủi ro, fill và sổ cái dùng chung một đường xử lý. Mỗi đồng tiền dùng một sổ riêng.

## Chạy demo trên Windows

Từ thư mục checkout, dùng PowerShell với Python 3.12–3.14 đã cài:

```powershell
git pull --ff-only origin master
.\setup_windows.cmd
.\.venv\Scripts\python.exe backend_trading_core\run_backend.py --demo
Invoke-Item .\demo_output\report.html
```

Cài đặt cần kết nối để tải dependency. Demo chạy offline, không dùng API key, AI hay frontend build. Báo cáo HTML và `snapshot.json` nằm trong `demo_output` (đổi bằng `--output`). Mở một hàng trong mục **Truy vết tín hiệu, rủi ro và fill** để xem chỉ báo, ID tín hiệu, quyết định, lệnh, fill, phí và biến động tài khoản. Demo dùng fixture BTC/USDT sinh từ seed 19 và cổ phiếu giả định DEMO trên lịch XNYS. Cùng input cho cùng ID và số dư.

### Replay CSV cổ phiếu

```powershell
.\.venv\Scripts\python.exe backend_trading_core\run_backend.py --demo --equity-csv .\my-xnys.csv --equity-symbol MYTICKER
Invoke-Item .\demo_output\report.html
```

Dùng [file mẫu](backend_trading_core/demo/equity_xnys_sample.csv) làm schema `session,closed_at_utc,open,high,low,close,volume`. Mỗi file dùng một mã XNYS, USD, nến ngày **raw**, ít nhất 36 phiên đã kết thúc, từ cũ đến mới. `session` là ngày giao dịch New York (`YYYY-MM-DD`). `closed_at_utc` là giờ đóng cửa ISO 8601 UTC (`…Z`). Lịch XNYS kiểm tra phiên, giờ mùa hè, ngày nghỉ và đóng sớm. Replay khớp theo giá mở phiên kế. Số lượng mua làm tròn xuống theo lô một cổ phiếu, bước giá 0,01 USD. File raw không mang thông tin corporate action. Replay chưa hạch toán cổ tức/split, và khoảng có split có thể làm sai P&L. XNYS chỉ là thị trường được triển khai cho nhập liệu.

## Paper dùng giá công khai

Ví dụ với Kraken BTC/USD. Mặc định là Binance BTC/USDT và feed đang tắt. Có thể sao chép [.env.example](.env.example) sang `.env` ở gốc repo để lưu cấu hình.

```powershell
$env:MARKET_FEED_ENABLED = 'true'
$env:MARKET_EXCHANGE = 'kraken'
$env:MARKET_SYMBOLS = 'BTC/USD'
.\.venv\Scripts\python.exe backend_trading_core\run_backend.py
```

Mở dashboard từ cửa sổ PowerShell thứ hai:

```powershell
Start-Process 'http://127.0.0.1:8000/dashboard'
```

Dùng F5 để cập nhật báo cáo. Bấm Ctrl+C trong cửa sổ chạy backend để dừng. Paper gọi CCXT công khai, bỏ nến đang chạy, tính tín hiệu ở nến trước và dùng giá đóng nến đã hoàn tất kế tiếp quan sát được lúc poll làm giá tham chiếu fill mô phỏng. Dữ liệu mất nến, trùng, sai thứ tự, OHLCV lỗi, nguồn lỗi và dữ liệu quá cũ hiển thị trên watchlist. Giá cũ của vị thế hiện có giữ nguyên giá đánh dấu cuối cùng và chặn mở thêm long trong cùng tài khoản đến khi feed cập nhật. Mỗi đồng tiền theo dõi riêng và các symbol dùng chung giới hạn theo quote currency. Feed lỗi không tự chuyển sang fixture.

### Ví paper 10–50 USD và quét nhiều coin

Ví dụ PowerShell với vốn paper 25 USD. Dùng thư mục repo và chạy sau bước cài đặt ở trên.

```powershell
$env:PAPER_MICRO_CAPITAL = '25'
$env:MARKET_SCAN_QUOTE = 'USD'
$env:MARKET_EXCHANGE = 'kraken'
$env:MARKET_FEED_ENABLED = 'true'
$env:OMNI_QUANT_DB_PATH = '.\data\micro-kraken-usd-25.sqlite3'
.\.venv\Scripts\python.exe backend_trading_core\run_backend.py
```

Mở `http://127.0.0.1:8000/dashboard` và dùng F5 để cập nhật. Chọn một file SQLite riêng cho mỗi cấu hình vốn, đồng tiền, sàn và phí. Có thể đặt `MARKET_SCAN_QUOTE=USDT` hoặc `USDC` nếu sàn có thị trường spot phù hợp. Khi bật máy quét, số lượng cặp được xác định từ metadata công khai của sàn.

- Bộ quét đọc báo giá hàng loạt, dùng ngưỡng thanh khoản 24h mặc định 250.000 đơn vị quote và spread tối đa 30 bps. Nó xoay vòng tối đa 20 cặp mỗi đợt để lấy nến, ưu tiên vị thế và lệnh đang mở. `MARKET_SCAN_BATCH_SIZE` có thể tăng tới 60, với giới hạn thời gian và rate limit của sàn. Báo cáo tách số cặp trong danh sách, số vượt bộ lọc, số đã gọi nến và số thực tế xử lý được.
- Tín hiệu mua trong cùng đợt được xét theo tỷ lệ biên chốt lời mô hình trên chi phí dự kiến, sau đó theo thanh khoản. Giá paper tham chiếu điểm giữa bid/ask còn mới, cộng spread và slippage khi khớp. Tín hiệu có tỷ lệ dưới 3×, dữ liệu nến thiếu, báo giá cũ hoặc size sau làm tròn thấp hơn mức của sàn sẽ không mở vị thế. Trường hợp thiếu báo giá bid/ask, vị thế đang có vẫn được quản lý trên nến hợp lệ với spread giả định. Tỷ lệ 3× là bộ lọc chi phí, không đo xác suất lời.
- Hồ sơ vốn nhỏ mặc định tối đa một vị thế và 60% vốn phân bổ. Sizing giới hạn lỗ **ước tính tại giá stop** khoảng 1% vốn, đã tính phí và khớp giá giả định. Lỗ ngày 2% theo UTC hoặc drawdown 5% chặn lệnh mở mới. Mặc định mô phỏng taker 40 bps cộng slippage 10 bps mỗi chiều. Nếu chọn sàn khác, đặt `PAPER_TAKER_FEE_BPS` theo biểu phí phù hợp. Gap giá, mất thanh khoản và mất feed có thể làm lỗ thực tế cao hơn ngưỡng mô hình. Khi vốn hoặc thanh khoản thấp, bộ quét có thể chỉ quan sát và giữ tiền mặt.

Sổ paper lưu SQLite tại `OMNI_QUANT_DB_PATH`. Dùng một tiến trình runner cho mỗi file. File cũ giữ các bảng logs/trades/audit. Số dư của phiên bản trước nằm trong bộ nhớ, vì vậy sổ bền vững mới bắt đầu với vốn ban đầu. Đặt `PAPER_KILL_SWITCH=true` rồi khởi động lại để lưu trạng thái chặn mua. Đặt `false` để gỡ. Bỏ biến sẽ giữ trạng thái đã lưu. Hủy lệnh và đóng long có giá hợp lệ vẫn được phép khi kill switch hoặc giới hạn lỗ đang chặn lệnh mua. Với giá stale, lệnh đóng theo tín hiệu cũng bị chặn. Lệnh chờ đối soát tiếp tục giữ phần tiền/khối lượng liên quan. Nếu broker giả lập không tìm thấy lệnh theo client ID, sổ giữ trạng thái chờ và không gửi lại.

### API loopback

| Route | Nội dung |
|---|---|
| `GET /health`, `GET /` | Trạng thái process và chế độ paper |
| `GET /api/stats`, `GET /api/positions` | Tài khoản quote chính theo schema cũ. `stats` bổ sung các sổ theo đồng tiền và watchlist |
| `GET /api/logs`, `GET /api/trades`, `GET /api/audit` | Lịch sử và ID truy vết, có bộ lọc `limit` |
| `WS /ws/snapshot` | Snapshot tương thích mỗi khoảng hai giây |
| `GET /api/snapshot` | Các sổ tiền, watchlist và bằng chứng tín hiệu mới nhất |
| `GET /dashboard` | Báo cáo HTML từ snapshot thực tế |

Vue/Tauri cũ dùng các API tương thích. Dashboard mới dùng chung nguồn dữ liệu với backend. Core chạy độc lập với AI. Module LLM hiện có trong `requirements-llm.txt` nằm ngoài đường xử lý lệnh.

## Tín hiệu và phép đo

- Baseline `sweep-v1` cần 35 nến hoàn tất và dùng tối đa 99 nến gần nhất. RSI dùng EMA với alpha 1/14 trên phần tăng/giảm, RSI bằng 50 khi giá phẳng và 100 khi chỉ tăng. MACD histogram là EMA12 − EMA26 trừ EMA9 của MACD. ATR là trung bình 14 true range. Sweep BUY đòi hỏi xuyên đáy 10 nến trước rồi đóng trên đáy đó, histogram dương và RSI dưới 70. SELL dùng điều kiện đối xứng ở đỉnh, histogram âm và RSI trên 30. SELL chỉ giảm vị thế long. ATR không dương, thiếu warm-up hoặc điều kiện chưa đạt cho HOLD.
- Giả thuyết `sweep-ema26-v1` giữ nguyên luật thoát, thêm điều kiện đóng trên EMA26 cho BUY. Báo cáo chạy baseline, giả thuyết và benchmark mua một lần với cùng giới hạn vốn. Các cửa sổ dùng 60 nến lịch sử và 45 nến ngoài mẫu. Quy tắc cố định trước khi đánh giá. Giá mở nến kế tiếp dùng cho tín hiệu replay, stop/take-profit quan sát tại giá mở và đóng, vị thế còn lại đóng tại giá cuối cửa sổ đánh giá.
- Mỗi cửa sổ có 10.000 USDT, spread mô hình 0, slippage 5 bps và phí 6 bps mỗi chiều, rồi chạy lại với phí và slippage gấp đôi. Turnover là tổng giá trị fill chia cho vốn đầu kỳ. Exposure là trung bình tỷ lệ vị thế đánh dấu trên vốn mỗi nến. Báo cáo dùng dữ liệu tổng hợp và ít lệnh, phù hợp kiểm tra tính đúng chức năng. Baseline vẫn mặc định.

## Kiểm thử và trạng thái môi trường

```powershell
$env:PYTHONPATH = 'backend_trading_core'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

| Môi trường | Phạm vi |
|---|---|
| Offline demo | Hai thị trường synthetic, một báo cáo HTML và snapshot JSON |
| Crypto paper | OHLCV spot công khai qua CCXT, fill giả lập và sổ SQLite |
| Equity paper | Replay CSV phiên XNYS, tài khoản USD và lịch phiên |
| Testnet | Hợp đồng submit/lookup/cancel kiểm thử với fake broker. Binance Spot Testnet được đối chiếu tài liệu. Kết nối đặt lệnh vào venue chưa triển khai |
| Live | Chưa triển khai. Entry point lỗi nếu `LIVE_TRADING_ENABLED=true` |

Xem [nguồn, phiên bản và giấy phép](THIRD_PARTY_NOTICES.md).
