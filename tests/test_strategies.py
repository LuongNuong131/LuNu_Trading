import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from backtest.runner import run_backtest
from core.models import OrderIntent, Signal
from core.order_executor import PaperExecutor
from core.pipeline import signal_evidence
from data_pipeline.bars import COLUMNS, REPLAY_CRYPTO, read_crypto_history
from data_pipeline.fixtures import crypto_fixture
from factor_zoo.strategies import BENCHMARK, CASH, ICT, PAPER_STRATEGIES, RESEARCH_STRATEGIES

ROOT = Path(__file__).resolve().parents[1]


def ict_fixture():
    frame = crypto_fixture().iloc[:42].copy()
    frame.loc[:, ["open", "high", "low", "close"]] = [100.0, 100.5, 99.5, 100.0]
    frame.loc[35:38, ["open", "high", "low", "close"]] = [
        [100.0, 100.6, 98.9, 100.1], [100.3, 102.2, 100.1, 102.0],
        [101.3, 102.7, 100.9, 102.1], [102.2, 102.8, 101.6, 102.4],
    ]
    frame.loc[39:, ["open", "high", "low", "close"]] = [102.4, 102.8, 101.8, 102.3]
    return frame


class StrategyTests(unittest.TestCase):
    def test_ict_fvg_needs_confirmation_and_uses_sweep_stop(self):
        frame = ict_fixture()
        instrument = replace(REPLAY_CRYPTO, minimum_notional=5)
        self.assertEqual(signal_evidence(frame.iloc[:37], instrument, "fixture", ICT)["signal"], "HOLD")
        confirmed = signal_evidence(frame.iloc[:38], instrument, "fixture", ICT)
        self.assertEqual(confirmed["signal"], "BUY")
        self.assertAlmostEqual(confirmed["structural_stop"], 98.89)
        result = run_backtest(frame, instrument=instrument, strategy=ICT, keep_trace=True, liquidate=False)
        order = next(o for o in result["account"]["orders"] if o["decision"]["approved"])
        self.assertEqual(order["created_at"], confirmed["closed_at"])
        self.assertAlmostEqual(order["decision"]["stop_loss"], 98.89)
        future = frame.copy()
        future.loc[38:, ["open", "high", "low", "close"]] *= 1.1
        changed = run_backtest(future, instrument=instrument, strategy=ICT, keep_trace=True, liquidate=False)
        self.assertEqual(result["events"][3]["signal_id"], changed["events"][3]["signal_id"])
        self.assertEqual(result["events"][3]["indicators"], changed["events"][3]["indicators"])

    def test_risk_rejects_structural_stop_outside_cap_or_crossed(self):
        account = PaperExecutor()
        intent = OrderIntent("TEST:COIN", Signal.BUY, 100, 1, "ICT", structural_stop=92)
        self.assertIn("wider", account.execute(intent).reason)
        crossed = replace(intent, structural_stop=100)
        self.assertIn("crossed", account.execute(crossed).reason)
        self.assertFalse(account.positions)

    def test_same_bar_both_hits_choose_stop_on_ict_replay(self):
        frame = ict_fixture()
        frame.loc[38, ["high", "low"]] = [120.0, 95.0]
        result = run_backtest(frame, instrument=REPLAY_CRYPTO, strategy=ICT, keep_trace=True, liquidate=False)
        first_exit = next(o for o in result["account"]["orders"] if o["side"] == "SELL" and o["fills"])
        self.assertEqual(first_exit["reason"], "STOP_LOSS")

    def test_unknown_strategy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown strategy"):
            signal_evidence(ict_fixture(), REPLAY_CRYPTO, "fixture", "missing")
        self.assertNotIn(CASH, PAPER_STRATEGIES)
        self.assertNotIn(BENCHMARK, PAPER_STRATEGIES)

    def test_crypto_csv_command_compares_every_strategy_and_cost(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            frame = ict_fixture()
            # Treat the same candles as hourly to exercise timeframe validation and next-open timestamps.
            frame["timestamp"] = frame["timestamp"].iloc[0] + pd.Series(range(len(frame))) * 3_600_000
            path = directory / "sample.csv"
            frame[COLUMNS].to_csv(path, index=False)
            subprocess.run([sys.executable, str(ROOT / "backend_trading_core" / "run_backend.py"),
                            "--research-crypto", str(path), "--research-warmup", "35",
                            "--research-window", "7", "--output", str(directory)], check=True,
                           cwd=temp, capture_output=True, timeout=45)
            result = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual({(t["strategy"], t["cost_multiplier"]) for t in result["research"]["trials"]},
                             {(s, c) for s in RESEARCH_STRATEGIES for c in (1, 2)})
            ict = next(t for t in result["research"]["trials"] if t["strategy"] == ICT)
            self.assertEqual(ict["validation"]["windows"][0]["trades"], 1)
            self.assertIn("SHA256", result["source_note"])

    def test_binance_archive_reads_microsecond_timestamps_without_extraction(self):
        frame = crypto_fixture().iloc[:2]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "spot.zip"
            with ZipFile(path, "w") as archive:
                rows = [",".join(str(x) for x in (stamp * 1_000, *fields, 0, 0, 0, 0, 0, 0))
                        for stamp, *fields in frame[COLUMNS].itertuples(index=False, name=None)]
                archive.writestr("kline.csv", "\n".join(rows))
            parsed = read_crypto_history(path, "1m")
            self.assertEqual(parsed["timestamp"].tolist(), frame["timestamp"].tolist())
            self.assertEqual(parsed["closed_ms"].tolist(), frame["closed_ms"].tolist())
