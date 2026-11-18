"""Canonical OHLCV validation for finalized CCXT bars and XNYS daily CSV."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from core.models import Instrument

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
CSV_COLUMNS = ["session", "closed_at_utc", "open", "high", "low", "close", "volume"]
REPLAY_CRYPTO = Instrument("BTC/USDT", "DEMO-SPOT", "USDT", "crypto spot", "1m", 0.01, 0.000001, 0.000001, 10)
REPLAY_EQUITY = Instrument("DEMO", "XNYS", "USD", "equity cash", "1d", 0.01, 1, 1)


@lru_cache(maxsize=1)
def xnys():
    from exchange_calendars import get_calendar
    return get_calendar("XNYS", start="1990-01-01", end="2050-12-31")


def utc_ms(value: int) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def validate_ohlcv(frame: pd.DataFrame, *, interval_ms: int | None = None) -> pd.DataFrame:
    if frame.empty:
        raise ValueError("no finalized OHLCV bars")
    if not set(COLUMNS).issubset(frame.columns):
        raise ValueError("OHLCV columns missing")
    frame = frame.copy()
    frame[COLUMNS] = frame[COLUMNS].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(frame[COLUMNS].to_numpy(dtype=float)).all():
        raise ValueError("OHLCV values must be finite")
    stamp = frame["timestamp"]
    if (stamp <= 0).any() or (stamp % 1 != 0).any():
        raise ValueError("expected positive integer UTC millisecond timestamps")
    delta = stamp.diff().iloc[1:]
    if (delta <= 0).any():
        raise ValueError("duplicated or unordered OHLCV timestamp")
    if interval_ms and ((delta != interval_ms).any() or (stamp % interval_ms != 0).any()):
        raise ValueError("missing or misaligned OHLCV bars")
    if ((frame[["open", "high", "low", "close"]] <= 0).any(axis=None)
            or (frame["volume"] < 0).any()
            or (frame["low"] > frame[["open", "close"]].min(axis=1)).any()
            or (frame["high"] < frame[["open", "close"]].max(axis=1)).any()):
        raise ValueError("invalid OHLC prices or volume")
    return frame.reset_index(drop=True)


def finalized_crypto(rows: list, now: datetime) -> pd.DataFrame:
    frame = validate_ohlcv(pd.DataFrame(rows, columns=COLUMNS), interval_ms=60_000)
    now_ms = int(now.timestamp() * 1000)
    if frame["timestamp"].iloc[-1] > now_ms:
        raise ValueError("OHLCV includes a future bar")
    frame = frame.loc[frame["timestamp"] + 60_000 <= now_ms].copy()
    if frame.empty:
        raise ValueError("no finalized OHLCV bars")
    frame["closed_ms"] = frame["timestamp"] + 60_000
    return frame


def read_equity_csv(path: Path) -> pd.DataFrame:
    """Read ordered, raw daily OHLCV for one XNYS equity."""
    raw = pd.read_csv(path, dtype={"session": str, "closed_at_utc": str})
    if list(raw.columns) != CSV_COLUMNS or raw.empty:
        raise ValueError(f"expected CSV columns in order: {','.join(CSV_COLUMNS)}")
    if not raw["session"].str.fullmatch(r"\d{4}-\d{2}-\d{2}", na=False).all():
        raise ValueError("session must be YYYY-MM-DD")
    if not raw["closed_at_utc"].str.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", na=False).all():
        raise ValueError("closed_at_utc must be an ISO 8601 UTC timestamp ending in Z")
    sessions = pd.DatetimeIndex(pd.to_datetime(raw["session"]))
    if not sessions.is_monotonic_increasing or not sessions.is_unique:
        raise ValueError("duplicate or unordered equity sessions")
    calendar = xnys()
    expected = calendar.sessions_in_range(sessions[0], sessions[-1])
    if not sessions.equals(expected):
        raise ValueError("missing session or equity row on a non-trading day")
    closes = pd.DatetimeIndex(pd.to_datetime(raw["closed_at_utc"], utc=True))
    schedule = calendar.schedule.loc[expected]
    if not (closes == schedule["close"]).all():
        raise ValueError("UTC close does not match XNYS session, including early closes")
    if closes[-1] > pd.Timestamp.now(tz="UTC"):
        raise ValueError("CSV contains a session whose close is still in the future")
    frame = raw.drop(columns=["session", "closed_at_utc"])
    frame["timestamp"] = schedule["open"].dt.as_unit("ms").astype("int64").to_numpy()
    frame["closed_ms"] = closes.as_unit("ms").astype("int64")
    return validate_ohlcv(frame)
