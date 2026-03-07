"""
fastapi_a2a.logging — Structured logger factory.

Every domain module should obtain its logger via::

    from fastapi_a2a.logging import get_logger
    logger = get_logger(__name__)

Loggers are standard Python ``logging.Logger`` instances so they work with
any log sink the consumer configures (structlog, loguru, stdout JSON, etc.).
The library itself never configures handlers or log levels — that is the
consumer application's responsibility, following Python logging best practice.
"""
from __future__ import annotations

import logging


def get_logger(name: str) -> logging.Logger:
    """
    Return a standard library Logger scoped to *name*.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A ``logging.Logger`` instance. No handlers are attached by this
        function — the host application controls the log configuration.
    """
    return logging.getLogger(name)
