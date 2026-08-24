@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo [LuNu] Checking Python launcher...
py -3 -c "import sys; print(sys.version)" >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python 3 is not available through the py launcher.
  echo Install Python from https://www.python.org/downloads/windows/ and enable the py launcher.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [LuNu] Creating .venv...
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
)

echo [LuNu] Installing core dependencies...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r backend_trading_core\requirements.txt
if errorlevel 1 exit /b 1

.venv\Scripts\python.exe -c "import pandas, numpy, fastapi, uvicorn; print('Dependencies OK:', pandas.__version__, numpy.__version__)"
if errorlevel 1 exit /b 1

echo.
echo Setup complete. Start the backend with:
echo   .venv\Scripts\python.exe backend_trading_core\run_backend.py
exit /b 0
