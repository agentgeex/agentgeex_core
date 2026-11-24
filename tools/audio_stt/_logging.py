"""Logging adapter -- replaces the original project's `common.log.logservice.getLog()`

Why a separate file
-------------------
The original project used `logservice.getLog(name)`, which also dragged in `Syscfg`
(it needed APP_ROOT_DIR + config/config.json just to start up). The speech service
only needs one thing: "write a log line". Making it carry a whole configuration
system makes no sense -- that would make "can we transcribe" depend on "does that
config.json exist", i.e. welding unrelated dependencies together.

This file does exactly one thing: hand back a standard logging.Logger.
Output format and level come from environment variables, so it can be tuned in
pm2/systemd environments just like the other services.
"""

import logging
import os
import sys


def get_logger(name: str = "audio_stt") -> logging.Logger:
    """Return a configured logger (repeated calls do not stack handlers)."""
    logger = logging.getLogger(name)
    if getattr(logger, "_ao_configured", False):
        return logger

    level = os.environ.get("ASR_LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level, logging.INFO))
    logger.propagate = False

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
                          datefmt="%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)
    logger._ao_configured = True  # Idempotency flag: keep handlers from doubling after a reload
    return logger
