"""Logging setup: make the ``app.*`` loggers visible in the container logs.

Uvicorn configures only its own loggers (``uvicorn``, ``uvicorn.access``,
``uvicorn.error``) and leaves the root logger without handlers at WARNING, so an
``app.*`` INFO record such as "Billing ready: indexes ensured" was dropped.
This gives the ``app`` logger a stderr handler and its own level
(``LOG_LEVEL``, INFO by default) and touches nothing else, so the uvicorn
access log is exactly as quiet or as noisy as before.

Idempotent: calling it twice does not add a second handler. When some other
code already installed a handler on the root logger, the ``app`` records
propagate to it and no extra handler is added (no duplicate lines).
"""

from __future__ import annotations

import logging

FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_HANDLER_NAME = "saludred-app"


def configure_logging(level: str = "INFO") -> None:
    resolved = logging.getLevelName(level.upper())
    if not isinstance(resolved, int):
        resolved = logging.INFO

    app_logger = logging.getLogger("app")
    app_logger.setLevel(resolved)

    if logging.getLogger().handlers:
        return
    if any(handler.get_name() == _HANDLER_NAME for handler in app_logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(logging.Formatter(FORMAT))
    app_logger.addHandler(handler)
