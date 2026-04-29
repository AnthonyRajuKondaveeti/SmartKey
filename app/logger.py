"""
logger.py
---------
Centralised logging for Smart Desktop Keyboard.

Writes to:
  - Console  (stdout, level INFO+)
  - logs/smart_keyboard.log  (rotating file, level DEBUG+)

Usage anywhere in the app:
    from logger import log
    log.info("...")
    log.debug("...")
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from cache import appdata_dir

# ── Log directory — use appdata so path is correct in both dev and .exe bundle ─
_LOG_DIR  = os.path.join(appdata_dir(), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOG_DIR, "smart_keyboard.log")

# ── Formatter ─────────────────────────────────────────────────────────────────
_FMT = "%(asctime)s.%(msecs)03d  %(levelname)-7s  %(name)-22s  %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# ── Root logger ───────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.WARNING)   # silence noisy third-party libs

log = logging.getLogger("SmartKeyboard")
log.setLevel(logging.DEBUG)
log.propagate = False

# Console handler — INFO and above
_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter(_FMT, _DATE_FMT))
log.addHandler(_ch)

# File handler — DEBUG and above, 2 MB × 3 backups
_fh = RotatingFileHandler(_LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter(_FMT, _DATE_FMT))
log.addHandler(_fh)

log.info(f"Logging initialised — file: {_LOG_FILE}")
