"""Windows-friendly backend entrypoint.

Run from this directory with: python run_backend.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Allow imports such as `from api...` when launched from any working directory.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import run  # noqa: E402


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("Backend stopped by user.", flush=True)
    except Exception:
        logging.exception("Backend failed to start")
        raise
