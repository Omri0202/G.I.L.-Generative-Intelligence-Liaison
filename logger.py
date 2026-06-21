"""
logger.py — G.I.L. central logging setup.

Import and use in every module:
    from logger import log
    log.info("something happened")
    log.warning("degraded state")
    log.error("something broke", exc_info=True)   # exc_info dumps the full traceback

Levels:
    DEBUG   — verbose detail (disabled in production builds)
    INFO    — normal operation milestones
    WARNING — something degraded but GIL keeps running
    ERROR   — a feature broke; full traceback recorded
    CRITICAL — GIL cannot continue

Log file: data/gil.log  (rotates at 5 MB, keeps 3 files)
"""

import logging
import logging.handlers
import sys
from pathlib import Path

_LOG_PATH  = Path(__file__).parent / "data" / "gil.log"
_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

_FMT = logging.Formatter(
    fmt     = "%(asctime)s  %(levelname)-8s  %(name)-24s  %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
)

# ── File handler — always on, rotating ───────────────────────────────────────
_fh = logging.handlers.RotatingFileHandler(
    str(_LOG_PATH),
    maxBytes    = 5_000_000,   # 5 MB per file
    backupCount = 3,
    encoding    = "utf-8",
)
_fh.setFormatter(_FMT)
_fh.setLevel(logging.DEBUG)

# ── Console handler — only when running from a terminal (not pythonw) ────────
_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_FMT)
_ch.setLevel(logging.INFO)

# ── Root GIL logger ───────────────────────────────────────────────────────────
log = logging.getLogger("GIL")
log.setLevel(logging.DEBUG)
log.addHandler(_fh)

# Add console output only when a real terminal is attached
if sys.stdout and sys.stdout.isatty():
    log.addHandler(_ch)

# Silence noisy third-party loggers
for _noisy in ("urllib3", "requests", "httpx", "asyncio", "PIL"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def get(name: str) -> logging.Logger:
    """Get a named child logger: from logger import get; log = get(__name__)"""
    return log.getChild(name)
