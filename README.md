# LuNu Trading

LuNu Trading is a **paper-trading and research workspace** assembled from the user's six LuNu repositories. The current default runner is intentionally fail-closed: it reads public OHLCV data, computes deterministic features, applies explicit risk limits, and records simulated orders. It does **not** submit orders to an exchange.

> This project is research software, not a promise of profit. Crypto markets are volatile, fees and slippage matter, and historical or simulated performance does not guarantee future results.

## What was combined

The repository keeps the six upstream projects as integration references rather than copying their incompatible source trees into the core. The target core uses clean interfaces for a staged combination of event-driven execution, exchange data, technical research, LLM-assisted analysis, portfolio tooling, and agent-based simulation. License boundaries are documented in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

| Capability | Current implementation | Planned integration boundary |
|---|---|---|
| Market data | Read-only CCXT OHLCV adapter | Optional exchange-specific adapters |
| Signals | Deterministic RSI, MACD histogram, ATR and sweep baseline | Strategy plugins and reproducible backtests |
| Risk | Per-trade risk, notional cap, total portfolio exposure, max positions, daily loss and drawdown gates | Correlation-aware exposure and regime-specific limits |
| Execution | Paper-only executor with fees and slippage | Separate, manually enabled live connector after paper validation |
| LLM | Advisory module retained but not on the order path | Research summaries, never unvalidated order authority |
| UI/API | Read-only local FastAPI endpoints plus `/ws/snapshot` state stream and `/api/audit` | Authenticated monitoring and configuration UI |

## Safe local run

Create a virtual environment, install `backend_trading_core/requirements.txt`, and copy `.env.example` to a local `.env` outside version control. The optional advisory LLM package set can be installed separately with `backend_trading_core/requirements-llm.txt`; it is not used by the default order path. The default mode remains paper-only. The local API exposes `/health`, `/api/stats`, `/api/positions`, `/api/logs`, `/api/trades`, `/api/audit`, and the read-only WebSocket `/ws/snapshot`. The market feed now retries with bounded exponential backoff and recreates the exchange client after failures.

### Windows CMD / PowerShell

From the repository root, activate the virtual environment and run the Windows-friendly entrypoint:

```bat
.venv\\Scripts\\activate
cd backend_trading_core
python run_backend.py
```

If you need the optional advisory LLM module, install it separately after the core succeeds:

```bat
python -m pip install -r requirements-llm.txt
```

Do not delete `.venv` while it is the active environment. If `.venv` was deleted while the prompt still shows `(.venv)`, close that terminal, open a new CMD, and run `setup_windows.cmd` from the repository root.

In PowerShell, the activation command is `..\\.venv\\Scripts\\Activate.ps1` after entering `backend_trading_core`, or simply run `..\\.venv\\Scripts\\python.exe run_backend.py`. Do not use Linux commands such as `source .venv/bin/activate` or `PYTHONPATH=... command` in Windows CMD. Before starting, update the checkout with `git pull origin master`; the old implementation created a CCXT client during import and could emit a resource warning, while the current implementation creates and closes it asynchronously. For an automated Windows setup from the repository root, run `setup_windows.cmd`; it creates `.venv`, installs dependencies with that exact interpreter, and verifies Pandas/NumPy/FastAPI/Uvicorn before startup.

For research, use `backtest.runner.run_backtest` for a single historical run and `backtest.validation.walk_forward_validate` for forward-only windows. Treat all output as research diagnostics, not a forecast.

Run tests with `PYTHONPATH=backend_trading_core python -m unittest discover -s tests -v`.

## Before any future live-trading work

Do not add live order placement to the current process. A live connector must be a separately reviewed component with exchange testnet support, explicit key permissions, a kill switch, idempotent client order IDs, reconciliation, stale-data protection, maximum loss shutdown, audit logs, and an independent manual approval step. API keys must be rotated if they were ever committed to a public repository.

## Source attribution

The six source repositories and their licenses are recorded in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). No GPL, LGPL or AGPL source tree is copied into the new core in this revision.
