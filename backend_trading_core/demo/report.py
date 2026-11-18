"""Network-free HTML view of runner snapshots and research results."""
from __future__ import annotations

from html import escape


def text(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.6f}".rstrip("0").rstrip(".")
    return escape(str(value), quote=True)


def table(headers, rows) -> str:
    th = "".join(f"<th scope='col'>{text(value)}</th>" for value in headers)
    tr = "".join("<tr>" + "".join(f"<td>{text(value)}</td>" for value in row) + "</tr>" for row in rows)
    return f"<div class='scroll'><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>"


def render_report(snapshot: dict) -> str:
    watchlist = []
    signals = {}
    for event in snapshot.get("signals", []):
        signals[event["signal_id"]] = event
    for item in snapshot["watchlist"]:
        event = item.get("event") or {}
        indicators = event.get("indicators", {})
        watchlist.append((item["symbol"], item["source"], item["status"], item.get("observed_at"),
                          event.get("closed_at"), event.get("timeframe"), event.get("signal"),
                          indicators.get("rsi"), indicators.get("macd_hist"), indicators.get("atr"),
                          event.get("reason"), item.get("error")))
    overview = table(("Mã · thị trường", "Nguồn", "Feed", "Quan sát UTC", "Nến tín hiệu UTC", "Khung",
                      "Tín hiệu", "RSI", "MACD hist.", "ATR", "Lý do", "Lỗi"), watchlist)
    accounts = []
    audit = []
    for account in snapshot["accounts"]:
        currency = account["currency"]
        stats = table(("Tiền mặt", "Vốn chủ", "P&L đã chốt", "P&L chưa chốt", "Phí", "Drawdown (%)"),
                      [(account[key] for key in ("capital", "equity", "realized_pnl", "unrealized_pnl",
                                                 "fees", "drawdown_pct"))])
        positions = table(("Vị thế long", "Số lượng", "Giá vốn", "Cắt lỗ", "Chốt lời"),
                          [(p["symbol"], p["amount"], p["entry_price"], p["stop_loss"], p["take_profit"])
                           for p in account["open_positions"]]) if account["open_positions"] else "<p>Không có vị thế mở.</p>"
        kill = "Bật" if account["kill_switch"] else "Tắt"
        accounts.append(f"<article><h3>{text(currency)}</h3><p>Kill switch · {kill}</p>{stats}{positions}</article>")
        for order in reversed(account["orders"]):
            event = order.get("evidence") or signals.get(order["signal_id"], {})
            indicators = event.get("indicators", {})
            details = table(("Tín hiệu", "Phiên bản", "Nến đóng UTC", "Nến dùng làm giá UTC", "Lý do", "Điều kiện", "Hủy tín hiệu"),
                            [(order["signal_id"], event.get("strategy"), event.get("closed_at"),
                              event.get("quote_closed_at"), order["reason"], event.get("conditions"),
                              event.get("invalidation"))])
            if indicators:
                details += table(("RSI", "MACD histogram", "ATR", "EMA26", "Đỉnh 10 nến",
                                  "Đáy 10 nến", "Quét đáy", "Quét đỉnh"),
                                 [(indicators.get(key) for key in ("rsi", "macd_hist", "atr", "ema26",
                                  "prior_10_high", "prior_10_low", "liquidity_sweep_bullish", "liquidity_sweep_bearish"))])
            decision = order["decision"]
            risk = table(("Thời điểm UTC", "Risk", "Trạng thái lệnh", "Giá dự kiến", "Khối lượng", "Tiền trước lệnh", "Sàn mô phỏng"),
                         [(order["created_at"], decision["reason"], order["status"], order["price"],
                           order["amount"], order["account_before"]["capital"], order.get("broker_reason") or "—")])
            fills = table(("Fill ID", "Giá", "SL", "Phí", "Δ tiền", "Δ vị thế", "P&L đã chốt", "Tiền sau fill", "Vốn sau fill"),
                          [(f["fill_id"], f["price"], f["amount"], f["fee"], f["cash_delta"], f["position_delta"],
                            f["realized_pnl"], f["account_after"]["capital"], f["account_after"]["equity"])
                           for f in order["fills"].values()]) if order["fills"] else "<p>Chưa có fill.</p>"
            status = text(order["status"])
            audit.append(f"<details><summary><b>{status}</b> · {text(order['side'])} {text(order['symbol'])}"
                         f" · {text(order['client_order_id'])} · {text(decision['reason'])}</summary>{details}{risk}{fills}</details>")
    research = ""
    if snapshot.get("research"):
        result = snapshot["research"]
        metric_columns = ("Mô hình", "Chi phí ×", "Từ UTC", "Đến UTC", "Lệnh đóng", "Ròng (%)",
                          "Drawdown (%)", "Turnover (%)", "Exposure TB (%)", "Phí")
        def metric(row, cost):
            return (row["strategy"], cost, row["from"], row["to"], row["trades"], row["return_pct"],
                    row["max_drawdown_pct"], row["turnover_pct"], row["mean_exposure_pct"], row["fees"])
        research = (f"<section><h2>Đánh giá theo cửa sổ</h2><p>{text(result['assumptions'])}</p>"
                    + table(metric_columns, (metric(row, trial["cost_multiplier"])
                                             for trial in result["trials"] for row in trial["validation"]["windows"]))
                    + "</section>")
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LuNu Trading · Paper</title>
<style>
:root {{color-scheme:dark; font: 16px system-ui,sans-serif; background:#101620; color:#eef3f9}}
body {{max-width:1440px; margin:0 auto; padding:clamp(12px,3vw,40px)}}
h1 {{font-size:clamp(2rem,5vw,3.6rem); margin:0 0 .4rem; letter-spacing:-.05em}}
h2 {{font-size:1.3rem; margin:0 0 .8rem}} h3 {{margin:0}}
p {{color:#b8c6d7; margin:.35rem 0 .8rem; line-height:1.5}}
section {{margin:1.25rem 0; padding:clamp(12px,2vw,24px); background:#182231; border:1px solid #344761; border-radius:14px}}
article+article {{margin-top:1.25rem}} .scroll {{overflow-x:auto; margin:.6rem 0}}
table {{border-collapse:collapse; width:100%; font-size:.86rem; font-variant-numeric:tabular-nums}}
th,td {{text-align:left; padding:.6rem; border-bottom:1px solid #344761}} th,b {{color:#8edbce}}
summary {{cursor:pointer; padding:.65rem 0; overflow-wrap:anywhere}} details+details {{border-top:1px solid #344761}}
</style></head><body>
<header><h1>LuNu Trading</h1><p>{text(snapshot['mode'])} · {text(snapshot['source_note'])}</p></header>
<section><h2>Watchlist</h2>{overview}</section>
<section><h2>Tài khoản paper</h2>{''.join(accounts)}</section>
<section><h2>Truy vết tín hiệu, rủi ro và fill</h2>{''.join(audit) or '<p>Chưa có lệnh.</p>'}</section>
{research}
</body></html>"""
