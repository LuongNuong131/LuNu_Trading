"""Project-generated synthetic OHLCV, with no external data or services."""
from __future__ import annotations

from datetime import datetime, timezone
from math import cos, sin

import pandas as pd

from data_pipeline.bars import COLUMNS, xnys

FIXTURE_SEED = 19


def synthetic_prices(index: int, level: float, seed: int = FIXTURE_SEED) -> tuple[float, ...]:
    close = level * (1 + 0.016 * sin(index / 7 + seed) + 0.008 * cos(index / 19)
                     + 0.004 * sin(index / 2.5) + index * 0.000015)
    opening = close * (1 - 0.001 * cos(index / 3))
    low = min(close, opening) - level * (0.035 if index % 17 == 2 else 0.003)
    high = max(close, opening) + level * (0.035 if index % 19 == 6 else 0.003)
    return tuple(round(value, 2) for value in (opening, high, low, close, 1000 + index * 13))


def crypto_fixture(seed: int = FIXTURE_SEED) -> pd.DataFrame:
    start = int(datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [(start + i * 60_000, *synthetic_prices(i, 60_000, seed)) for i in range(240)]
    frame = pd.DataFrame(rows, columns=COLUMNS)
    frame["closed_ms"] = frame["timestamp"] + 60_000
    return frame


def equity_fixture_csv() -> pd.DataFrame:
    calendar = xnys()
    sessions = calendar.sessions_in_range("2024-10-01", "2025-02-28")
    return pd.DataFrame(
        [(day.strftime("%Y-%m-%d"), calendar.session_close(day).strftime("%Y-%m-%dT%H:%M:%SZ"),
          *synthetic_prices(i, 4_000)) for i, day in enumerate(sessions)],
        columns=["session", "closed_at_utc", *COLUMNS[1:]],
    )
