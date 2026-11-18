"""Offline demo and XNYS CSV replay entrypoint."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from backtest.runner import run_backtest
from backtest.validation import walk_forward_validate
from core.models import RiskLimits
from core.pipeline import BASELINE, BENCHMARK, TREND
from data_pipeline.bars import REPLAY_CRYPTO, REPLAY_EQUITY, read_equity_csv
from data_pipeline.fixtures import FIXTURE_SEED, crypto_fixture
from demo.report import render_report

SOURCE = "synthetic fixture"
SAMPLE = Path(__file__).with_name("equity_xnys_sample.csv")


def run_demo(output: Path, equity_csv: Path | None = None, equity_symbol: str = "DEMO") -> dict:
    if equity_csv is None and equity_symbol != "DEMO":
        raise ValueError("--equity-symbol requires --equity-csv")
    if not equity_symbol or not all(char.isascii() and (char.isalnum() or char in ".-") for char in equity_symbol):
        raise ValueError("expected XNYS ticker containing letters, digits, dot or hyphen")
    limits = RiskLimits()
    crypto = crypto_fixture()
    equity = read_equity_csv(equity_csv or SAMPLE)
    equity_instrument = replace(REPLAY_EQUITY, symbol=equity_symbol.upper())
    runs = [
        run_backtest(crypto, instrument=REPLAY_CRYPTO, source=SOURCE, keep_trace=True, liquidate=False),
        run_backtest(equity, instrument=equity_instrument, source=SOURCE if equity_csv is None else "user CSV · raw",
                     keep_trace=True, liquidate=False),
    ]
    if any(not run["events"] for run in runs):
        raise ValueError("offline replay needs at least 36 complete bars per instrument")
    trials = []
    for cost in (1, 2):
        costs = replace(limits, fee_rate=limits.fee_rate * cost, slippage_bps=limits.slippage_bps * cost)
        for strategy in (BASELINE, TREND, BENCHMARK):
            validation = walk_forward_validate(crypto, train_size=60, test_size=45,
                                               instrument=REPLAY_CRYPTO, strategy=strategy, limits=costs, source=SOURCE)
            trials.append({"cost_multiplier": cost, "validation": validation})
    snapshot = {
        "mode": "Offline demo · paper replay",
        "source_note": f"Crypto tổng hợp, seed {FIXTURE_SEED}. "
                       + ("Cổ phiếu DEMO tổng hợp." if equity_csv is None else "Cổ phiếu từ CSV do người dùng cung cấp.")
                       + " XNYS dùng giá raw, không hạch toán cổ tức/split. Các tài khoản tách theo đồng tiền.",
        "accounts": [run["account"] for run in runs],
        "signals": [event for run in runs for event in run["events"]],
        "watchlist": [{
            "symbol": last["symbol"], "source": last["source"], "status": "replay · nến đã đóng",
            "observed_at": last["quote_closed_at"], "event": last,
        } for last in (run["events"][-1] for run in runs)],
        "research": {
            "assumptions": ("Dữ liệu crypto tổng hợp. 10.000 USDT mỗi cửa sổ. Khớp ở giá mở nến kế tiếp. "
                            "Đóng số dư vị thế tại giá cuối cửa sổ. Spread 0 bps, slippage 5 bps và phí 6 bps mỗi chiều "
                            "ở mức 1×, làm tròn theo bước giá/lô. Benchmark mua một lần với cùng giới hạn vốn. "
                            "60 nến warm-up, 45 nến ngoài mẫu, quy tắc cố định. Chỉ kiểm tra chức năng."),
            "trials": trials,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.html").write_text(render_report(snapshot), encoding="utf-8")
    (output / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False),
                                         encoding="utf-8")
    return snapshot
