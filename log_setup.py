"""
AgentGeeX logging system

Provides unified dual-output logging for the daemon, watchdog, WS client and MCP:
  - StreamHandler -> configurable stream (stdout by default; MCP passes stderr)
  - FileHandler   -> logs/{component}.log (one file per component)

Usage:
    from log_setup import setup_logging
    log = setup_logging("DAEMON", log_dir="/path/to/logs")
    log = setup_logging("MCP", log_dir="/path/to/logs", stream=sys.stderr)

    log("normal message")              # INFO
    log("warning message", "WARN")     # WARNING
    log("error message", "ERROR")      # ERROR
"""

import contextlib
import logging
import os
import sys
from datetime import datetime

# component name -> log file name mapping
_COMPONENT_FILE = {
    "DAEMON": "daemon.log",
    "WATCHDOG": "watchdog.log",
    "WS": "ws_client.log",
    "MCP": "agx_mcp.log",
}

# level string -> logging constant
_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class _AgentGeeXFormatter(logging.Formatter):
    """Custom Formatter; timestamps use the YYYY-MM-DD HH:MM:SS format."""

    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")


class _Logger:
    """Backward-compatible logger wrapper supporting both log(msg) and log(msg, "WARN")."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def __call__(self, msg: str, level: str = "INFO"):
        # self._logger.log(_LEVEL_MAP.get(level, logging.INFO), msg)

        # Modified 2026-06-14 12:34:53
        # Optimization 1: guard against an extremely large JSON blowing up the terminal — safe long-text truncation (optional, does not affect the core path)
        # if len(msg) > 10000: msg = msg[:10000] + "... [Log Truncated]"

        self._logger.log(_LEVEL_MAP.get(level, logging.INFO), msg)

        # Optimization 2: [core fix] force-flush the buffers of every handler under this logger
        for handler in self._logger.handlers:
            with contextlib.suppress(Exception):
                handler.flush()


def setup_logging(component: str, log_dir: str, stream=sys.stdout):
    """
    Initialize a component logger.

    Args:
        component: component name — "DAEMON" / "WATCHDOG" / "WS" / "MCP"
        log_dir: absolute path of the log directory (e.g. /home/user/.agx/logs)
        stream: output stream, sys.stdout by default (MCP should pass sys.stderr)

    Returns:
        logger object compatible with the old interface, supporting log("msg") and log("msg", "WARN")
    """
    logger = logging.getLogger(component)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()  # prevent duplicate handlers

    fmt = _AgentGeeXFormatter(f"[%(asctime)s] [%(levelname)s] [{component}] %(message)s")

    # ── StreamHandler -> configurable stream (stdout by default; MCP uses stderr) ──
    stream_handler = logging.StreamHandler(stream)
    stream_handler.setFormatter(fmt)
    stream_handler.setLevel(logging.INFO)
    logger.addHandler(stream_handler)

    # ── FileHandler -> one file per component ──
    filename = _COMPONENT_FILE.get(component, f"{component.lower()}.log")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.FileHandler(os.path.join(log_dir, filename), encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    return _Logger(logger)


def update_file_log_level(component: str, level: str):
    """Dynamically update the log level of a component's FileHandler.
    level: "DEBUG" | "INFO" | "WARN" | "ERROR"
    """
    logger = logging.getLogger(component)
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            handler.setLevel(_LEVEL_MAP.get(level, logging.DEBUG))
            break
