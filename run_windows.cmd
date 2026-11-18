@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "LUNU_MODE=%~1"
if not defined LUNU_MODE set "LUNU_MODE=paper"
set "LUNU_BROWSER=--open-dashboard"
if /i "%~2"=="--no-browser" set "LUNU_BROWSER="
if not "%~2"=="" if /i not "%~2"=="--no-browser" goto usage
if not "%~3"=="" goto usage
if /i not "%LUNU_MODE%"=="paper" if /i not "%LUNU_MODE%"=="demo" if /i not "%LUNU_MODE%"=="binance-paper" goto usage

if not exist ".venv\Scripts\python.exe" (
  call setup_windows.cmd
  if errorlevel 1 exit /b 1
)

if /i "%LUNU_MODE%"=="demo" goto demo
if /i "%LUNU_MODE%"=="binance-paper" (
  set "MARKET_EXCHANGE=binance"
  set "MARKET_SCAN_QUOTE=USDT"
  set "MARKET_TIMEFRAME=15m"
  set "MARKET_MAX_AGE_SECONDS=1860"
  set "PAPER_STRATEGY=ict-fvg-v1"
  set "PAPER_MICRO_CAPITAL=25"
  set "PAPER_TAKER_FEE_BPS=10"
  set "MARKET_FEED_ENABLED=true"
  set "OMNI_QUANT_DB_PATH=data\micro-binance-ict-25.sqlite3"
)

echo [LuNu] Paper dashboard: http://127.0.0.1:8000/dashboard
echo [LuNu] Keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" backend_trading_core\run_backend.py %LUNU_BROWSER%
exit /b %errorlevel%

:demo
".venv\Scripts\python.exe" backend_trading_core\run_backend.py --demo
if errorlevel 1 exit /b %errorlevel%
if defined LUNU_BROWSER start "" "%CD%\demo_output\report.html"
exit /b 0

:usage
echo Usage: run_windows.cmd [paper ^| demo ^| binance-paper] [--no-browser]
exit /b 2
