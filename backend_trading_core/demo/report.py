"""Self-contained HTML dashboard for the paper runner and offline reports."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

ASSETS = Path(__file__).parent
LOCAL_ZONE = timezone(timedelta(hours=7))
STATUS = {
    "ready": ("Sẵn sàng", "good"), "waiting": ("Đang chờ dữ liệu", "muted"),
    "disabled": ("Feed đã tắt", "muted"), "error": ("Feed gặp lỗi", "bad"),
    "stale": ("Dữ liệu cũ", "warn"), "degraded": ("Feed bị gián đoạn", "warn"),
    "ok": ("Sẵn sàng", "good"), "filled": ("Đã khớp", "good"),
    "partially_filled": ("Khớp một phần", "good"), "risk_rejected": ("Risk chặn", "bad"),
    "rejected": ("Bị từ chối", "bad"), "buy": ("MUA", "good"), "sell": ("BÁN", "bad"),
    "hold": ("CHỜ", "muted"),
}


def text(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.8f}".rstrip("0").rstrip(".")
    return escape(str(value), quote=True)


def number(value, digits=2, *, signed=False) -> str:
    sign = "+" if signed and value > 0 else ""
    return (sign + f"{value:,.{digits}f}").translate(str.maketrans(".,-", ",.−"))


def local_time(value: str | None) -> str:
    if not value:
        return "—"
    try:
        instant = datetime.fromisoformat(value)
        if instant.tzinfo is not None:
            return instant.astimezone(LOCAL_ZONE).strftime("%d/%m/%Y · %H:%M:%S")
    except ValueError:
        pass
    return text(value)


def badge(status, label=None) -> str:
    default, tone = STATUS.get(str(status).lower(), (status, "muted"))
    return f'<span class="badge {tone}"><i></i>{text(label or default)}</span>'


def table(headers, rows) -> str:
    th = "".join(f"<th scope='col'>{text(value)}</th>" for value in headers)
    tr = "".join("<tr>" + "".join(f"<td>{text(value)}</td>" for value in row) + "</tr>" for row in rows)
    return f"<div class='scroll'><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>"


def section_title(title, note="") -> str:
    return f'<div class="section-heading"><h2>{text(title)}</h2>{f"<p>{text(note)}</p>" if note else ""}</div>'


def metric(label, value, suffix, icon, tone="", signed=False) -> str:
    return (f'<div class="metric card"><span class="metric-icon {tone}" aria-hidden="true">{icon}</span>'
            f'<span class="metric-label">{text(label)}</span><div class="metric-value {tone}">'
            f'{number(value, signed=signed)} <small>{text(suffix)}</small></div></div>')


def empty_state(message) -> str:
    return f'<p class="empty">{text(message)}</p>'


def equity_points(account: dict) -> list[dict]:
    points = []
    orders = sorted(account["orders"], key=lambda order: order["created_at"])
    for order in orders:
        if order["fills"] and not points:
            points.append({"value": order["account_before"]["equity"], "label": "Trước lần khớp đầu"})
        for fill in order["fills"].values():
            points.append({"value": fill["account_after"]["equity"], "label": "Lệnh lúc " + local_time(order["created_at"])})
    return [*points[-59:], {"value": account["equity"], "label": "Ảnh chụp hiện tại"}]


def account_view(account: dict, scanner: dict | None, *, active: bool) -> str:
    currency = account["currency"]
    realized = account["realized_pnl"]
    tone = "positive" if realized > 0 else "negative" if realized < 0 else ""
    tiles = "".join((
        metric("Vốn chủ", account["equity"], currency, "◈", "accent"),
        metric("Tiền mặt", account["capital"], currency, "▤"),
        metric("P&L đã chốt", realized, currency, "+", tone, True),
        metric("Drawdown hiện tại", account["drawdown_pct"], "%", "↓"),
    ))
    points = equity_points(account)
    graph = text(json.dumps(points, ensure_ascii=False, allow_nan=False))
    position_count = len(account["open_positions"])
    chart = f"""
    <section class="card chart-card" aria-label="Biểu đồ vốn chủ">
      <div class="panel-top">{section_title("Diễn biến vốn chủ", "Theo lần khớp · tối đa 60 điểm gần nhất")}
        <span class="chart-key"><i></i>{text(currency)}</span></div>
      <div class="chart-wrap">
        <canvas data-points="{graph}" tabindex="0" role="img"
          aria-label="Vốn chủ {number(account['equity'])} {text(currency)} tại ảnh chụp hiện tại"></canvas>
        <div class="chart-tooltip" hidden></div>
        {('<div class="chart-empty"><strong>Chưa có lệnh khớp</strong>'
          '<span>Biểu đồ hiển thị số vốn hiện tại.</span></div>') if len(points) == 1 else ''}
      </div>
      <div class="chart-footer">
        <div><span>P&L chưa chốt</span><strong>{number(account["unrealized_pnl"], signed=True)} <small>{text(currency)}</small></strong></div>
        <div><span>Tổng phí</span><strong>{number(account["fees"], 4)} <small>{text(currency)}</small></strong></div>
        <div><span>Vị thế mở</span><strong>{position_count}</strong></div>
      </div>
    </section>"""
    limits = account.get("risk_limits", {})
    limits_view = ""
    if limits.get("include_costs_in_risk"):
        limits_view = table(("Vốn ban đầu", "Vị thế tối đa", "Giá trị mỗi vị thế (%)", "Rủi ro ước tính mỗi lệnh (%)",
                             "Ngưỡng lỗ ngày (%)", "Ngưỡng drawdown (%)", "Phí mỗi chiều (bps)"),
                            [(limits["starting_capital"], limits["max_open_positions"],
                              100 * limits["max_position_notional_pct"], 100 * limits["risk_per_trade"],
                              100 * limits["max_daily_loss_pct"], 100 * limits["max_drawdown_pct"],
                              10_000 * limits["fee_rate"])])
    positions = table(("Vị thế long", "Số lượng", "Giá vốn", "Cắt lỗ", "Chốt lời"),
                      [(p["symbol"], p["amount"], p["entry_price"], p["stop_loss"], p["take_profit"])
                       for p in account["open_positions"]]) if position_count else empty_state("Không có vị thế mở.")
    kill = badge("error", "Kill switch đang bật") if account["kill_switch"] else badge("disabled", "Kill switch đang tắt")
    return (f'<div data-wallet="{text(currency)}"{"" if active else " hidden"}>'
            f'<div class="metric-grid">{tiles}</div><div class="overview-grid">{chart}'
            f'{scanner_view(scanner, currency) if scanner else wallet_view(account)}</div>'
            f'<details class="card positions" data-disclosure="positions-{text(currency)}"><summary>'
            f'<strong>Vị thế và hạn mức</strong><span>{position_count} vị thế mở</span></summary>'
            f'<div class="detail-body">{kill}{positions}{limits_view}</div></details></div>')


def wallet_view(account) -> str:
    limits = account.get("risk_limits", {})
    filled = sum(bool(order.get("fills")) for order in account["orders"])
    return f"""<section class="card scanner-card"><div class="panel-top">{section_title("Hoạt động tài khoản")}</div>
    <div class="wallet-activity">
      <div><span>Vốn ban đầu</span><strong>{number(limits['starting_capital']) if 'starting_capital' in limits else '—'} {text(account['currency'])}</strong></div>
      <div><span>Lệnh đã khớp</span><strong>{filled}</strong></div>
      <div><span>Lệnh bị risk chặn</span><strong>{sum(order['status'] == 'RISK_REJECTED' for order in account['orders'])}</strong></div>
      <div><span>Drawdown lớn nhất</span><strong>{number(account.get('max_drawdown_pct', account['drawdown_pct']))}%</strong></div>
    </div></section>"""


def scanner_view(scanner: dict, currency: str) -> str:
    universe = scanner.get("universe") or 0
    bars = []
    for key, label in (
        ("universe", "Cặp spot"), ("quoted", "Có báo giá"), ("eligible", "Qua lọc thanh khoản"),
        ("processed", "Nến mới đã xét"),
    ):
        value = scanner.get(key)
        ratio = min(100, value / universe * 100) if value is not None and universe > 0 else 0
        bars.append(f'<div class="scanner-row"><div><span>{label}</span><strong>{text(value)}</strong></div>'
                    f'<div class="bar-track"><i style="width:{ratio:.2f}%"></i></div></div>')
    data = table(("Đã hỏi nến", "Có nến hợp lệ", "BUY", "BUY qua lọc chi phí"),
                 [[scanner.get(k) for k in ("attempted", "completed", "buy_signals", "eligible_buys")]])
    excluded = ", ".join(f"{reason} ({count})" for reason, count in scanner.get("excluded", {}).items())
    return f"""<section class="card scanner-card" aria-label="Quét spot">
      <div class="panel-top">{section_title("Quét thị trường", f"Spot · {scanner['quote']}")}{badge(scanner['status'])}</div>
      <div class="scanner-bars">{''.join(bars)}</div>
      <div class="scanner-foot"><span>Lần quan sát gần nhất</span>
        <strong>{local_time(scanner.get('observed_at'))}</strong></div>
      {f'<p class="feed-error">{text(scanner["error"])}</p>' if scanner.get('error') else ''}
      <details class="scanner-detail" data-disclosure="scanner-{text(currency)}"><summary>Chi tiết bộ quét</summary>
        {data}{f'<p>{text(excluded)}</p>' if excluded else ''}</details>
    </section>"""


def filters(section, placeholder, choices="") -> str:
    return (f'<div class="filter-tools" data-tools hidden><label class="search"><span aria-hidden="true">⌕</span>'
            f'<input type="search" data-search="{section}" placeholder="{placeholder}" aria-label="{placeholder}"></label>'
            f'{choices}</div>')


def filter_choices(section, label, values) -> str:
    return (f'<select data-filter="{section}" aria-label="{label}">'
            + "".join(f'<option value="{key}">{name}</option>' for key, name in values) + "</select>")


def watchlist_view(items: list[dict], scanner: dict | None) -> str:
    content = []
    for item in items:
        event = item.get("event") or {}
        indicators = event.get("indicators", {})
        scan = event.get("scanner", {})
        symbol = item["symbol"]
        signal = event.get("signal")
        details = table(("Nguồn", "Khung", "Nến đóng UTC", "MACD hist.", "ATR", "Lý do"),
                        [(item["source"], event.get("timeframe"), event.get("closed_at"),
                          indicators.get("macd_hist"), indicators.get("atr"), event.get("reason"))])
        if scanner:
            details += table(("Khối lượng 24h", "Lệnh tối thiểu", "Chi phí hai chiều (bps)", "Bộ lọc BUY"),
                             [[scan.get(k) for k in ("quote_volume", "venue_minimum", "round_trip_cost_bps", "entry_block_reason")]])
        content.append(f"""<details class="watch-row" data-row="watch" data-kind="{text(signal or 'NONE')}"
                      data-disclosure="watch-{text(symbol)}">
          <summary><span class="pair"><b>{text(symbol)}</b><small>{text(event.get('timeframe') or item['source'])}</small></span>
            <span>{badge(item['status'])}</span><span>{badge(signal) if signal else '—'}</span>
            <span>{text(indicators.get('rsi'))}</span>
            {f"<span>{text(scan.get('spread_bps'))}</span>" if scanner else ""}
            <span><time>{local_time(item.get('observed_at'))}</time></span></summary>
          <div class="detail-body">{details}
            {f'<p class="feed-error">{text(item["error"])}</p>' if item.get("error") else ''}</div>
          </details>""")
    choices = filter_choices("watch", "Lọc theo tín hiệu", (("", "Tất cả tín hiệu"), ("BUY", "Mua"), ("SELL", "Bán"), ("HOLD", "Chờ")))
    tools = filters("watch", "Tìm cặp giao dịch", choices) if content else ""
    spread_header = "<span>Spread (bps)</span>" if scanner else ""
    table_class = "watch-table has-scanner" if scanner else "watch-table"
    rows = (f'<div class="watch-scroll scroll"><div class="{table_class}"><div class="watch-head" aria-hidden="true">'
            f'<span>Cặp giao dịch</span><span>Feed</span><span>Tín hiệu</span><span>RSI</span>{spread_header}'
            f'<span>Quan sát · UTC+7</span></div>{"".join(content)}</div></div>') if content else empty_state("Chưa có cặp giao dịch.")
    return (f'<section id="watch" class="card data-panel"><div class="panel-top">{section_title("Watchlist", f"{len(items)} cặp theo dõi")}'
            f'{tools}</div>{rows}<p class="empty" data-empty="watch" hidden>Không tìm thấy cặp phù hợp.</p></section>')


def order_details(order, event) -> str:
    indicators = event.get("indicators", {})
    details = table(("Tín hiệu", "Phiên bản", "Nến đóng UTC", "Nến dùng làm giá UTC", "Lý do", "Điều kiện", "Hủy tín hiệu"),
                    [(order["signal_id"], event.get("strategy"), event.get("closed_at"), event.get("quote_closed_at"),
                      order["reason"], event.get("conditions"), event.get("invalidation"))])
    if indicators:
        details += table(("RSI", "MACD histogram", "ATR", "EMA26", "Đỉnh 10 nến", "Đáy 10 nến", "Quét đáy", "Quét đỉnh"),
                         [[indicators.get(k) for k in ("rsi", "macd_hist", "atr", "ema26", "prior_10_high", "prior_10_low",
                                                       "liquidity_sweep_bullish", "liquidity_sweep_bearish")]])
    if event.get("strategy") == "ict-fvg-v1":
        keys = ("ict_prior_low", "ict_prior_high", "ict_displacement_body", "ict_fvg_up", "ict_fvg_down")
        details += table(("Đáy tham chiếu", "Đỉnh tham chiếu", "Thân displacement", "FVG tăng", "FVG giảm", "Stop cấu trúc"),
                         [[indicators.get(k) for k in keys] + [event.get("structural_stop")]])
    decision = order["decision"]
    risk = table(("Risk", "Trạng thái lệnh", "Giá dự kiến", "Khối lượng", "Lỗ ước tính tại stop", "Tiền trước lệnh", "Sàn mô phỏng"),
                 [(decision["reason"], order["status"], order["price"], order["amount"], decision.get("estimated_stop_loss"),
                   order["account_before"]["capital"], order.get("broker_reason"))])
    fills = table(("Fill ID", "Giá", "SL", "Phí", "Δ tiền", "Δ vị thế", "P&L đã chốt", "Tiền sau fill", "Vốn sau fill"),
                  [(f["fill_id"], f["price"], f["amount"], f["fee"], f["cash_delta"], f["position_delta"], f["realized_pnl"],
                    f["account_after"]["capital"], f["account_after"]["equity"]) for f in order["fills"].values()]
                  ) if order["fills"] else '<p>Chưa có fill.</p>'
    return f'<div class="detail-body"><h3>Tín hiệu</h3>{details}<h3>Quyết định rủi ro</h3>{risk}<h3>Khớp lệnh</h3>{fills}</div>'


def audit_view(accounts, signals) -> str:
    evidence = {item["signal_id"]: item for item in signals}
    orders = sorted((order for account in accounts for order in account["orders"]),
                    key=lambda order: order["created_at"], reverse=True)
    rows = []
    for order in orders:
        event = order.get("evidence") or evidence.get(order["signal_id"], {})
        order_id = order["client_order_id"]
        kind = "filled" if order["fills"] else "blocked" if order["status"] in {"RISK_REJECTED", "REJECTED"} else "other"
        rows.append(f"""<details class="audit-row" data-row="audit" data-kind="{kind}" data-disclosure="audit-{text(order_id)}">
         <summary><span class="pair"><b>{text(order['symbol'])}</b><small>{text(order['side'])} · {local_time(order['created_at'])}</small></span>
          {badge(order['status'])}<span class="decision">{text(order['decision']['reason'])}<small>{text(order_id)}</small></span></summary>
         {order_details(order, event)}</details>""")
    choices = filter_choices("audit", "Lọc trạng thái lệnh", (("", "Tất cả lệnh"), ("filled", "Đã khớp"), ("blocked", "Bị chặn")))
    tools = filters("audit", "Tìm cặp hoặc mã lệnh", choices) if rows else ""
    counts = f"{len(orders)} quyết định"
    return (f'<section id="audit" class="card data-panel"><div class="panel-top">'
            f'{section_title("Truy vết lệnh", counts)}{tools}</div>{''.join(rows) or empty_state("Chưa có quyết định đặt lệnh.")}'
            '<p class="empty" data-empty="audit" hidden>Không tìm thấy lệnh phù hợp.</p></section>')


def research_view(result) -> str:
    if not result:
        return ""
    rows = []
    detailed = []
    for trial in result["trials"]:
        validation = trial["validation"]
        summary = validation["summary"]
        windows = validation["windows"]
        cost = trial["cost_multiplier"]
        rows.append((trial["strategy"], cost, sum(w["trades"] for w in windows), sum(w["buy_rejections"] for w in windows),
                     summary["profitable_windows"], summary["median_return_pct"], summary["worst_drawdown_pct"]))
        detailed.extend((w["strategy"], cost, w["from"], w["to"], w["trades"], w["return_pct"], w["max_drawdown_pct"],
                         w["turnover_pct"], w["mean_exposure_pct"], w["fees"]) for w in windows)
    metrics = table(("Mô hình", "Chi phí ×", "Lệnh đóng", "BUY bị chặn", "Cửa sổ có lãi", "Lãi ròng trung vị (%)",
                     "Drawdown lớn nhất (%)"), rows)
    detail_table = table(("Mô hình", "Chi phí ×", "Từ UTC", "Đến UTC", "Lệnh đóng", "Ròng (%)", "Drawdown (%)",
                          "Turnover (%)", "Exposure TB (%)", "Phí"), detailed)
    return (f'<section id="research" class="card data-panel"><div class="panel-top">{section_title("Đánh giá chiến lược")}</div>'
            f'<p class="assumptions">{text(result["assumptions"])}</p>{metrics}'
            f'<details class="research-detail" data-disclosure="research"><summary>Kết quả từng cửa sổ</summary>{detail_table}</details></section>')


def feed_status(snapshot):
    scanner = snapshot.get("scanner")
    statuses = {item["status"] for item in snapshot["watchlist"]}
    if scanner and scanner["status"] != "ready":
        return scanner["status"]
    if "ready" in statuses:
        return "degraded" if "error" in statuses else "ready"
    return next((status for status in ("error", "stale", "waiting", "disabled") if status in statuses),
                "ready" if scanner else "waiting")


def render_report(snapshot: dict, *, live: bool = False) -> str:
    accounts = snapshot["accounts"]
    scanner = snapshot.get("scanner")
    research = snapshot.get("research")
    links = ([("top", "◈", "Tổng quan"), ("watch", "▤", "Watchlist"), ("audit", "≋", "Truy vết lệnh")] if accounts else [])
    if research:
        links.append(("research", "▥", "Đánh giá"))
    nav = "".join(f'<a href="#{target}"><span aria-hidden="true">{icon}</span>{label}</a>' for target, icon, label in links)
    tabs = ""
    if len(accounts) > 1:
        tabs = '<div class="wallet-tabs" aria-label="Chọn ví paper">' + "".join(
            f'<button type="button" data-select-wallet="{text(a["currency"])}" aria-pressed="{str(i == 0).lower()}">{text(a["currency"])}</button>'
            for i, a in enumerate(accounts)) + "</div>"
    wallets = "".join(account_view(account, scanner, active=i == 0) for i, account in enumerate(accounts))
    trading = watchlist_view(snapshot["watchlist"], scanner) + audit_view(accounts, snapshot.get("signals", [])) if accounts else ""
    status = badge(feed_status(snapshot)) if live else badge("disabled", "Báo cáo offline")
    source = str(snapshot["source_note"])
    label = "Giao dịch mô phỏng" if accounts else "Nghiên cứu giao dịch"
    refresh = '<span id="refresh-status" role="status"></span><button type="button" id="refresh" hidden>↻ <span>Làm mới</span></button>' if live else ""
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LuNu Trading · {label}</title><style>{(ASSETS / 'dashboard.css').read_text(encoding='utf-8')}</style></head>
<body{" data-live='true'" if live else ""}>
<a class="skip" href="#top">Đến nội dung</a>
<aside class="sidebar">
  <a href="#top" class="brand"><span class="brand-mark" aria-hidden="true">L</span><span>LuNu Trading</span></a>
  <nav aria-label="Điều hướng">{nav}</nav>
  <div class="sidebar-foot"><i></i><div><strong>Paper trading</strong><small>Ví mô phỏng</small></div></div>
</aside>
<div class="workspace">
  <header class="topbar"><span>{label}</span><div class="topbar-actions">{refresh}</div></header>
  <main id="top" class="dashboard-view">
    <div class="intro">
      <div><h1>{'Tổng quan giao dịch' if accounts else 'So sánh chiến lược'}</h1>
       <p>{text(snapshot['mode'])} <span class="intro-separator">·</span> {text(source)}</p></div>
      <div class="intro-actions">{status}{tabs}</div>
    </div>
    {f'<div class="strategy">Chiến lược <strong>{text(snapshot["strategy"])}</strong></div>' if snapshot.get("strategy") else ''}
    {wallets}{trading}{research_view(research)}
  </main>
</div>
<script>{(ASSETS / 'dashboard.js').read_text(encoding='utf-8')}</script>
</body></html>"""
