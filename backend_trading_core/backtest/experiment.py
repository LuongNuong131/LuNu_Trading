"""Reproducible side-by-side research using pre-defined out-of-sample windows."""
from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from math import isfinite
from pathlib import Path

from backtest.validation import walk_forward_validate
from core.micro import micro_limits
from core.models import Instrument, RiskLimits
from data_pipeline.bars import read_crypto_history
from demo.report import render_report
from factor_zoo.strategies import RESEARCH_STRATEGIES


def compare(frame, instrument: Instrument, source: str, limits: RiskLimits,
            *, warmup: int, window: int) -> dict:
    if warmup < 35 or window < 1 or len(frame) < warmup + window:
        raise ValueError("need at least 35 warm-up candles and one complete evaluation window")
    trials = []
    for multiplier in (1, 2):
        costs = replace(limits, fee_rate=limits.fee_rate * multiplier,
                        spread_bps=limits.spread_bps * multiplier,
                        slippage_bps=limits.slippage_bps * multiplier)
        for strategy in RESEARCH_STRATEGIES:
            validation = walk_forward_validate(frame, train_size=warmup, test_size=window, instrument=instrument,
                                               strategy=strategy, limits=costs, source=source)
            trials.append({"strategy": strategy, "cost_multiplier": multiplier, "validation": validation})
    assumptions = (
        f"{instrument.key} · {instrument.timeframe} · {source}. Vốn {limits.starting_capital:g} {instrument.currency} "
        f"mỗi cửa sổ. Phí {limits.fee_rate * 10_000:g} bps, trượt {limits.slippage_bps:g} bps mỗi chiều, "
        f"spread {limits.spread_bps:g} bps. Lệnh tối thiểu {instrument.minimum_notional:g} {instrument.currency}, "
        f"tick {instrument.tick:g}, lot {instrument.lot:g}. {warmup} nến warm-up và {window} nến đánh giá. "
        "Tín hiệu khớp sớm nhất ở giá mở nến kế, stop được xét trước nếu nến chạm cả stop và target. "
        "Vị thế cuối cửa sổ đóng theo giá cuối nến. Dùng spread cố định; CSV thiếu báo giá bid/ask "
        "và độ sâu nên bộ lọc live scanner không được tái tạo đầy đủ."
    )
    return {"assumptions": assumptions, "trials": trials}


def run_crypto_research(output: Path, path: Path, *, symbol: str, timeframe: str,
                        capital: float, fee_bps: float = 10, spread_bps: float = 6,
                        tick: float = 0.01, lot: float = 0.00001, minimum_notional: float = 5,
                        warmup: int = 60, window: int = 168) -> dict:
    parts = symbol.upper().split("/")
    if len(parts) != 2 or not all(part.isascii() and part.isalnum() for part in parts):
        raise ValueError("--crypto-symbol must be BASE/QUOTE")
    if not all(isfinite(value) and value > 0 for value in (tick, lot, minimum_notional)):
        raise ValueError("positive tick, lot and minimum notional are required")
    if not isfinite(spread_bps) or not 0 <= spread_bps <= 500:
        raise ValueError("research spread must be between 0 and 500 bps")
    limits = replace(micro_limits(capital, fee_bps), spread_bps=spread_bps)
    instrument = Instrument("/".join(parts), "CSV", parts[1], "crypto spot", timeframe,
                            tick=tick, lot=lot, minimum_notional=minimum_notional)
    source = f"{path.name} · SHA256 {sha256(path.read_bytes()).hexdigest()}"
    frame = read_crypto_history(path, timeframe)
    research = compare(frame, instrument, source, limits, warmup=warmup, window=window)
    snapshot = {"mode": "Nghiên cứu crypto · paper replay", "source_note": source,
                "watchlist": [], "accounts": [], "research": research}
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.html").write_text(render_report(snapshot), encoding="utf-8")
    (output / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False),
                                         encoding="utf-8")
    return snapshot
