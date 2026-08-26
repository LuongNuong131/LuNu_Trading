"""Centralized, validated runtime configuration for LuNu Trading."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"between {minimum} and {maximum}" if maximum is not None else f">= {minimum}"
        raise ValueError(f"{name} must be {bound}, got {value}")
    return value


def _resolve_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Settings:
    api_host: str
    api_port: int
    log_level: str
    market_exchange: str
    market_symbols: tuple[str, ...]
    market_poll_seconds: int
    market_feed_enabled: bool
    db_path: Path
    cors_origins: tuple[str, ...]
    snapshot_interval_seconds: float
    live_trading_enabled: bool

    @classmethod
    def from_env(cls) -> "Settings":
        symbols = tuple(item.strip() for item in os.getenv("MARKET_SYMBOLS", "BTC/USDT").split(",") if item.strip())
        if not symbols:
            raise ValueError("MARKET_SYMBOLS must contain at least one symbol")
        origins = tuple(item.strip() for item in os.getenv("OMNI_QUANT_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if item.strip())
        interval_raw = os.getenv("SNAPSHOT_INTERVAL_SECONDS", "2").strip()
        try:
            interval = float(interval_raw)
        except ValueError as exc:
            raise ValueError(f"SNAPSHOT_INTERVAL_SECONDS must be numeric, got {interval_raw!r}") from exc
        if interval < 0.5:
            raise ValueError("SNAPSHOT_INTERVAL_SECONDS must be >= 0.5")
        live_trading_enabled = _env_bool("LIVE_TRADING_ENABLED", False)
        if live_trading_enabled:
            raise ValueError("LIVE_TRADING_ENABLED=true is blocked: live execution is not implemented")
        return cls(
            api_host=os.getenv("API_HOST", "127.0.0.1").strip() or "127.0.0.1",
            api_port=_env_int("API_PORT", 8000, 1, 65535),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            market_exchange=os.getenv("MARKET_EXCHANGE", "binance").strip().lower() or "binance",
            market_symbols=symbols,
            market_poll_seconds=max(15, _env_int("MARKET_POLL_SECONDS", 60, 1)),
            market_feed_enabled=_env_bool("MARKET_FEED_ENABLED", False),
            db_path=_resolve_path(os.getenv("OMNI_QUANT_DB_PATH", "data/omni_quant.sqlite3")),
            cors_origins=origins,
            snapshot_interval_seconds=interval,
            live_trading_enabled=False,
        )


settings = Settings.from_env()
