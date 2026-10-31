"""Process-wide logging setup for the app entry points.

Importing a library module must not configure the root logger, or any script that
imports `src.data` loses control of its own logging. Only an entry point
(`app/api.py`, `app/app.py`) calls `configure_logging()`.
"""

from __future__ import annotations

import logging

from src.data import CONFIG


# ─────────────────────────────────────────────────────────────────────────────
# Third-party log noise suppression
# ─────────────────────────────────────────────────────────────────────────────
# Background: running this web app on Windows (uvicorn/FastAPI, or the legacy
# Streamlit server) produces two kinds of repetitive third-party log records
# that are not actionable for this project:
#
# 1. `asyncio` — Windows' Proactor event loop emits an ERROR with a traceback
#    ending in `ConnectionResetError: [WinError 10054]` whenever a long-lived
#    HTTP/websocket peer disconnects abruptly (browser tab refresh, mobile
#    screen sleep, Wi-Fi handoff, app restart). The underlying TCP reset is
#    benign — the client reconnects on its own, and nothing in user-space needs
#    to handle it. During a single audit session we typically see a dozen of
#    these, each as a 4-line traceback.
#
# 2. `httpx` — the Anthropic SDK uses httpx, which logs an INFO line for every
#    HTTP request (`HTTP Request: POST ...`). Our own loggers in
#    `inventory_extract` and `transcribe_client` already record the same calls
#    with more context, so the httpx echo is duplicate noise.
#
# What we suppress, narrowly:
#  - `asyncio`: a logging.Filter that drops ONLY error records whose exception
#    traceback contains the literal `WinError 10054`. Any other asyncio error
#    (and any non-error record) passes through untouched. On non-Windows
#    platforms this filter is effectively a no-op since the marker won't appear.
#  - `httpx`: level raised from INFO to WARNING. Real HTTP problems
#    (connection refused, 4xx/5xx) still surface.
#
# How to re-enable for debugging:
#  - Comment out the `_suppress_known_log_noise()` call in `configure_logging`, OR
#  - Lower a specific logger at runtime, e.g. in a Python REPL or
#    early in your entrypoint (`app/api.py` or `app/app.py`):
#        logging.getLogger("httpx").setLevel(logging.INFO)
#        logging.getLogger("asyncio").filters.clear()
#
# When NOT to suppress: if you ever see ANY asyncio error you don't recognise
# (i.e. one whose traceback does NOT mention WinError 10054), the filter is
# already letting it through — investigate that one. If you suspect the filter
# itself is hiding a real error, disable it and reproduce.
# ─────────────────────────────────────────────────────────────────────────────


class _AsyncioConnectionResetFilter(logging.Filter):
    """Drop asyncio ERROR records whose traceback ends in WinError 10054."""

    _MARKER = "WinError 10054"

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.ERROR or not record.exc_info:
            return True
        try:
            exc_text = logging.Formatter().formatException(record.exc_info)
        except Exception:  # pragma: no cover — defensive: never break logging
            return True
        return self._MARKER not in exc_text


def _suppress_known_log_noise() -> None:
    """Apply the targeted filters described above. Idempotent."""
    asyncio_logger = logging.getLogger("asyncio")
    if not any(isinstance(f, _AsyncioConnectionResetFilter) for f in asyncio_logger.filters):
        asyncio_logger.addFilter(_AsyncioConnectionResetFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)


def configure_logging() -> None:
    """Set the root logger from ``config.json`` and apply the noise filters. Idempotent."""
    logging.basicConfig(
        level=getattr(logging, CONFIG["logging"]["level"]),
        format=CONFIG["logging"]["format"],
    )
    _suppress_known_log_noise()
