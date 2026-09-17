# logger.py
# -*- coding: utf-8 -*-
"""
Shared rotating file logger for all services.
Keeps log files small and safe for SD card usage.

Supports enabling/disabling logging via config.json:
  { "logger": "enable" }  -> logging enabled  (default if key is missing)
  { "logger": "disable" } -> all log output suppressed (NullHandler)

Hot-reload without restart:
  - Via MQTT  : send payload {"logger": "enable"} or {"logger": "disable"}
  - Via REST  : POST /api/config {"logger": "enable"}
  - Manual    : call reload_all_loggers() from anywhere
"""
__version__ = "1.0.0(9)"

import logging
import logging.handlers
import os
import sys
import json

# =====================================================================================================================================
#      LOGGER
#      Name                       : LOGGER
#      Version                    : 0.0.0.1
#      Date Created               : 05-05-2026
#      Updated                    : 15-05-2026
#      Changes                    : - Fix: NullHandler is no longer treated as "already active"
#                                          so reload can enable logging without restart
#                                   - Fix: _managed_loggers dict to track all loggers
#                                          created via get_logger()
#                                   - Fix: propagate = False to prevent duplication via root logger
#                                   - Fix: _is_logging_enabled() always reads from file (not cached)
# ======================================================================================================================================

#LOG_DIR = "/home/alice/bridge/logs"
LOG_DIR = "/home/alice/broker/logs"
os.makedirs(LOG_DIR, exist_ok=True)

MAX_BYTES    = 2 * 1024 * 1024   # 2 MB per file
BACKUP_COUNT = 3                  # keep 3 rotated files = max 8 MB per service

# logger.py is located in /bridge/library/
# config.json is located in /bridge/ (one level above)
_BASE_DIR   = os.path.dirname(os.path.abspath(__file__))   # /bridge/library/
_PARENT_DIR = os.path.dirname(_BASE_DIR)                   # /bridge/

# Search for config.json:
# first in parent directory (/bridge/),
# fallback to same directory (/bridge/library/)
_CONFIG_FILE = os.path.join(_PARENT_DIR, "config.json")
if not os.path.exists(_CONFIG_FILE):
    _CONFIG_FILE = os.path.join(_BASE_DIR, "config.json")

# -----------------------------------------------------------------------
# Registry of all loggers created via get_logger().
# Used by reload_all_loggers() so none are missed,
# even if the logger hasn't been added to logging.Logger.manager.loggerDict yet.
# -----------------------------------------------------------------------
_managed_loggers: dict = {}   # { name: logging.Logger }


# ======================================================================
#  INTERNAL HELPERS
# ======================================================================

def _is_logging_enabled() -> bool:
    """
    Read config.json every time it is called -- NOT cached.

    This is important so that reload_all_loggers() always gets the
    latest value without needing to restart the process.

    Supported values (case-insensitive):
      "enable" / "disable"

    Default: True (logging active) if the file is missing or cannot be read.
    """
    try:
        if os.path.exists(_CONFIG_FILE):
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            value = str(cfg.get("logger", "enable")).strip().lower()
            return value != "disable"
    except Exception:
        pass
    return True   # safe default: keep logging if config cannot be read


def _make_formatter() -> logging.Formatter:
    """Create the standard formatter used across all handlers."""
    return logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )


def _clear_handlers(logger: logging.Logger) -> None:
    """
    Flush, close, and remove all handlers from the logger.
    Safe to call multiple times.
    """
    for h in list(logger.handlers):
        try:
            h.flush()
            h.close()
        except Exception:
            pass
        logger.removeHandler(h)


def _attach_active_handlers(logger: logging.Logger) -> None:
    """
    Attach two handlers to the logger:
      1. RotatingFileHandler  -> /home/alice/broker/logs/<name>.log
      2. StreamHandler        -> stdout (so journalctl can still read it)
    """
    formatter = _make_formatter()

    # --- Rotating file handler ---
    fh = logging.handlers.RotatingFileHandler(
        filename=os.path.join(LOG_DIR, f"{logger.name}.log"),
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8"
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # --- stdout handler ---
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)


# ======================================================================
#  PUBLIC API
# ======================================================================

def get_logger(name: str) -> logging.Logger:
    """
    Return a logger for the given name.

    - Always tracked in _managed_loggers so reload_all_loggers()
      can find it without relying on loggerDict.
    - propagate is set to False to prevent duplication via the root logger.
    - If the logger already has active handlers (not NullHandler),
      it is returned immediately without changing the existing configuration.

    Logs are written to:
      /home/alice/broker/logs/<name>.log

    Rotation:
      - 2 MB per file
      - 3 backup files

    If config.json contains { "logger": "disable" }:
      all handlers are replaced with a NullHandler (silent mode).

    Note:
      The enable/disable state is evaluated WHEN the logger is first created.
      To change the state without restarting, call reload_all_loggers().
    """
    logger = logging.getLogger(name)

    # Important: disable propagation to the root logger.
    # Without this, logs may appear duplicated or be lost because
    # the root logger also handles them.
    logger.propagate = False

    # Save reference for hot-reload
    _managed_loggers[name] = logger

    # Check whether active handlers already exist (not NullHandler).
    # This is a fix from the previous version which incorrectly checked
    # logger.handlers alone -- NullHandler was treated as "already set up"
    # even though it is not active.
    real_handlers = [
        h for h in logger.handlers
        if not isinstance(h, logging.NullHandler)
    ]
    if real_handlers:
        return logger   # already active, no need to set up again

    # No active handler found -> clear any existing handlers (e.g. old NullHandler)
    # then set up according to the current config state.
    _clear_handlers(logger)
    logger.setLevel(logging.DEBUG)

    if _is_logging_enabled():
        _attach_active_handlers(logger)
    else:
        logger.addHandler(logging.NullHandler())

    return logger


def reload_all_loggers() -> None:
    """
    Re-read config.json and update ALL managed loggers.

    Called by:
      - apps.py  when receiving {"logger": "enable/disable"} via MQTT
      - apps.py  when receiving {"logger": "enable/disable"} via REST /api/config

    How it works:
      1. Read the latest "logger" value from config.json
      2. Merge _managed_loggers + logging.Logger.manager.loggerDict
      3. For each logger:
         - Remove all existing handlers (including NullHandler)
         - Attach new handlers according to the enable/disable state
      4. Handlers are changed IN-PLACE -> logger references in other modules
         (e.g. mqtts.py: log = get_logger("mqtts")) remain valid
         without needing a restart or re-import

    No application or service restart is required.
    """
    enabled = _is_logging_enabled()

    # Merge all known loggers:
    # - _managed_loggers : loggers created via get_logger() in our code
    # - loggerDict       : other loggers possibly created by third-party libraries
    all_loggers: dict = dict(_managed_loggers)

    manager = logging.Logger.manager
    for name, obj in list(manager.loggerDict.items()):
        if isinstance(obj, logging.Logger) and name not in all_loggers:
            all_loggers[name] = obj

    for name, logger in all_loggers.items():
        logger.propagate = False        # ensure propagation is always off
        _clear_handlers(logger)
        logger.setLevel(logging.DEBUG)

        if enabled:
            _attach_active_handlers(logger)
        else:
            logger.addHandler(logging.NullHandler())

    status_str = "ENABLED" if enabled else "DISABLED"
    print(f"[LOGGER] reload_all_loggers() complete -> {status_str}")
    print(f"[LOGGER] config read from : {_CONFIG_FILE}")
    print(f"[LOGGER] loggers updated  : {list(all_loggers.keys())}")
