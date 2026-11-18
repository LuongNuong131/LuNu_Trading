# LuNu Trading

LuNu Trading chạy nghiên cứu và paper spot/cash từ dữ liệu tổng hợp, CSV cổ phiếu hoặc OHLCV crypto công khai. Tín hiệu, kiểm tra rủi ro, fill và sổ cái dùng chung một đường xử lý. Mỗi đồng tiền dùng một sổ riêng.

## Chạy từ CMD trên Windows

Mở Command Prompt trong thư mục repo. Máy cần Python 3.12–3.14.

```bat
git pull --ff-only origin master
run_windows.cmd demo
```

Lệnh tạo `.venv` nếu chưa có và mở báo cáo demo khi chạy xong. Từ PowerShell, dùng tiền tố `.\` trước `run_windows.cmd`.

- `run_windows.cmd` khởi động dashboard paper theo cấu hình hiện tại. Feed tắt theo mặc định nếu chưa cấu hình.
- `run_windows.cmd binance-paper` mở dashboard paper với giá Binance công khai, chiến lược ICT 15m và ví mô phỏng 25 USDT. Preset dùng sổ riêng tại `data\micro-binance-ict-25.sqlite3`. Nếu feed báo lỗi, dashboard vẫn mở và hiển thị trạng thái.
- Với dashboard chạy liên tục, giữ cửa sổ CMD mở và bấm Ctrl+C để dừng. Nếu trình duyệt chưa mở, vào `http://127.0.0.1:8000/dashboard`.

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

Dashboard paper tự cập nhật mỗi 15 giây khi tab đang mở. Nút **Làm mới** kiểm tra ngay, giữ bộ lọc và dòng đang mở. Biểu đồ vốn chủ dùng vốn ghi nhận sau các lần khớp mô phỏng và ảnh chụp hiện tại. Bấm Ctrl+C trong cửa sổ chạy backend để dừng. Paper gọi CCXT công khai, bỏ nến đang chạy, tính tín hiệu ở nến trước và dùng giá đóng nến đã hoàn tất kế tiếp quan sát được lúc poll làm giá tham chiếu fill mô phỏng. Dữ liệu mất nến, trùng, sai thứ tự, OHLCV lỗi, nguồn lỗi và dữ liệu quá cũ hiển thị trên watchlist. Giá cũ của vị thế hiện có giữ nguyên giá đánh dấu cuối cùng và chặn mở thêm long trong cùng tài khoản đến khi feed cập nhật. Mỗi đồng tiền theo dõi riêng và các symbol dùng chung giới hạn theo quote currency. Feed lỗi không tự chuyển sang fixture.

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

## Chiến lược và quản trị rủi ro

Chọn một chiến lược cho mỗi phiên paper bằng `PAPER_STRATEGY`. Baseline `sweep-v1` vẫn là mặc định. Các chiến lược chung sổ cash long-only, engine risk, phí/slippage, giới hạn ví nhỏ và báo cáo quyết định.

| ID | Điều kiện chính |
|---|---|
| `sweep-v1` | Quét biên 10 nến, MACD histogram và RSI |
| `sweep-ema26-v1` | Sweep với bộ lọc xu hướng EMA26 cho BUY |
| `ict-fvg-v1` | Sweep và thu hồi, displacement đổi cấu trúc, fair value gap xác nhận |
| `donchian-20-10-v1` | Mua khi phá đỉnh 20 nến cùng xu hướng, thoát khi mất đáy 10 nến |
| `ema-pullback-v1` | EMA12/26 và RSI vượt 50 sau nhịp hồi |
| `bollinger-reentry-v1` | Mua khi giá trở lại dải dưới Bollinger, thoát tại đường trung bình |

ICT là bộ quy tắc định lượng lấy cảm hứng từ ICT. Nến đầu quét và đóng lại qua biên thanh khoản 10 nến trước. Nến thứ hai có thân tối thiểu 0,8 ATR trước displacement và đóng qua biên đối diện. Nến thứ ba xác nhận FVG ba nến rộng ít nhất 0,1 ATR. BUY đặt stop dự kiến dưới đáy quét một tick. Risk từ chối nếu giá vào đã vượt qua stop theo hướng bất lợi hoặc khoảng stop rộng quá giới hạn. Mẫu giảm chỉ thoát long. Dashboard ghi rõ FVG, displacement và stop để đối chiếu từng tín hiệu.

Ví dụ đổi chiến lược cho phiên paper trên Binance. Dùng SQLite riêng và xóa cấu hình `MARKET_MAX_AGE_SECONDS` cũ nếu nó được đặt cho khung 1m.

```powershell
$env:MARKET_EXCHANGE = 'binance'
$env:MARKET_SCAN_QUOTE = 'USDT'
$env:MARKET_TIMEFRAME = '15m'
$env:PAPER_STRATEGY = 'ict-fvg-v1'
$env:PAPER_MICRO_CAPITAL = '25'
$env:PAPER_TAKER_FEE_BPS = '10'
$env:MARKET_FEED_ENABLED = 'true'
$env:OMNI_QUANT_DB_PATH = '.\data\micro-binance-ict-25.sqlite3'
.\.venv\Scripts\python.exe backend_trading_core\run_backend.py
```

Backend đọc nến đã đóng, hỗ trợ `1m`, `5m`, `15m`, `1h`. Sàn có thể trả lỗi truy cập tùy khu vực. Chỉ theo dõi một sổ cho mỗi cấu hình sàn và chiến lược.

### So sánh trên nến lịch sử

Demo tổng hợp chỉ kiểm tra chức năng. Dùng CSV từ nguồn dữ liệu đã xác minh để đo lại với ví 10–50 USDT. CSV gồm `timestamp,open,high,low,close,volume`. Timestamp là thời điểm mở nến UTC tính bằng mili giây. Cần dữ liệu đã đóng, đúng thứ tự và liên tục. Có thể dùng trực tiếp ZIP kline từ Binance Data Vision.

```powershell
.\.venv\Scripts\python.exe backend_trading_core\run_backend.py --research-crypto .\BTCUSDT.csv --crypto-symbol BTC/USDT --timeframe 1h --research-capital 25 --output .\demo_output\research-btc
Invoke-Item .\demo_output\research-btc\report.html
```

Chạy tất cả chiến lược, so với giữ tiền mặt và mua giữ theo cùng hạn mức. Mặc định 60 nến warm-up, 168 nến mỗi cửa sổ ngoài mẫu, phí 10 bps, slippage 10 bps mỗi chiều và spread 6 bps, rồi kiểm tra lại ở 2× chi phí. Chỉnh `--research-tick`, `--research-lot` và `--research-min-notional` theo thông số từng cặp. Replay vào ở giá mở nến kế, xử lý gap tại giá mở và giả định stop trước nếu một nến chạm cả stop lẫn target. Dữ liệu OHLCV không tái hiện được sổ lệnh, luân phiên scanner hoặc thanh khoản intrabar. Báo cáo có SHA256 của CSV, số lệnh, BUY bị chặn, lợi nhuận ròng và drawdown.

Lần nghiệm thu dùng nến Binance spot 1h tháng 10/2025 của BTC, ETH và SOL, vốn 25 USDT và bốn cửa sổ mỗi cặp. EMA pullback có lợi nhuận ròng trung vị lần lượt +0,034%, +0,444%, −1,009% ở chi phí 1× và âm trên cả ba cặp ở 2×. ICT có một giao dịch trên SOL với cửa sổ khoảng −1,00%. BTC và ETH không có giao dịch ICT. Mẫu này quá ngắn để kết luận hiệu quả. [Nguồn ZIP công khai](THIRD_PARTY_NOTICES.md)

## Kết nối sàn

| Sàn | Chế độ có sẵn trong LuNu | Bước thử khớp sau paper |
|---|---|---|
| Binance | Đọc spot công khai qua CCXT. CLI Spot Testnet riêng | Dùng khóa HMAC tạo tại Spot Testnet. Preview, submit và đối soát với tiền mô phỏng |
| OKX | Đọc spot công khai qua CCXT | Demo Trading dùng API key mô phỏng, passphrase và cờ simulated trading. Adapter lệnh chưa có |
| Bybit | Đọc spot công khai qua CCXT khi endpoint khả dụng | Chọn Demo Trading hoặc Testnet, dùng khóa đúng môi trường. Adapter lệnh chưa có |
| Bitget | Đọc spot công khai qua CCXT | Demo Trading dùng khóa demo và passphrase. Adapter lệnh chưa có |

### Binance Spot Testnet

Khi `ping` in `"status": "reachable"` rồi trở về CMD, phép thử kết nối đã hoàn tất. Khởi động dashboard paper bằng `run_windows.cmd`. Để thử lệnh Testnet, tạo khóa HMAC riêng tại Spot Testnet và đưa vào môi trường tiến trình dưới tên `BINANCE_TESTNET_API_KEY` và `BINANCE_TESTNET_API_SECRET`. Các lệnh bên dưới dùng giá và số lượng minh họa.

```powershell
$py = '.\.venv\Scripts\python.exe'
$cli = 'backend_trading_core\binance_testnet.py'
& $py $cli ping
& $py $cli preview BTCUSDT BUY 0.00010 100000 LNU-T-probe01 --stop-price 98500
& $py $cli submit BTCUSDT BUY 0.00010 100000 LNU-T-probe01 --stop-price 98500 --confirm-testnet
& $py $cli reconcile LNU-T-probe01
& $py $cli status
```

Dùng giá và khối lượng theo bộ lọc và sổ lệnh Spot Testnet lúc thử. Lệnh LIMIT IOC có thể không khớp hoặc chỉ khớp một phần. Chương trình ghim host Testnet, kiểm tra bước giá/lô, min notional, số dư và giới hạn ví 10–50 USDT. BUY qua risk engine chung, giới hạn tối đa 60% ngân sách và khối lượng theo rủi ro ở mức stop khai báo. `--stop-price` chỉ dùng để tính khối lượng, CLI chưa gửi lệnh stop bảo vệ. SELL chỉ giảm lượng mua đã được đối soát trong journal. CLI lưu client ID trước khi gửi, đọc `myTrades` để xác nhận fill/phí, và giữ lệnh chờ khi phản hồi không rõ. Dùng `reconcile` theo ID cũ sau timeout. Để hủy lệnh còn mở, dùng `cancel <client_id> --confirm-testnet`. Journal thật của sàn demo nằm ở `data/binance-spot-testnet.sqlite3`, độc lập với tài khoản paper. Binance có thể reset Testnet. Nếu reset làm ID biến mất, đối chiếu trên Testnet rồi dùng journal mới cho đợt mới.

CLI là phép thử lệnh thủ công. Để vào lệnh tiền thật về sau, cần một adapter production riêng dùng nguồn số dư và fill từ sàn, kết nối chặt với risk engine, limit lỗ, stop trên sàn, kill switch và đối soát sau restart. Bật production cần quyết định riêng sau khi xem kết quả ngoài mẫu và testnet. Khóa production chỉ cần quyền trade spot, tắt quyền rút và giới hạn IP khi sàn hỗ trợ.

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
| Testnet | CLI Binance Spot Testnet với SQLite riêng. Chữ ký, gửi, timeout và đối soát đã kiểm thử bằng HTTP giả lập. Ping thật trả HTTP 451 trong môi trường kiểm thử |
| Live | Chưa triển khai. Entry point lỗi nếu `LIVE_TRADING_ENABLED=true` |

Xem [nguồn, phiên bản và giấy phép](THIRD_PARTY_NOTICES.md).
