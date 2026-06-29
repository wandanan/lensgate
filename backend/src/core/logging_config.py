"""
Structured logging configuration for the multimodal proxy gateway.

Uses ``structlog`` with JSON renderer to produce one JSON object per line
on stdout.  Integrates with Python's standard ``logging`` so that libraries
using stdlib logging are captured by structlog as well.

Usage::

    from backend.src.core.logging_config import setup_logging, get_logger

    setup_logging(level="DEBUG")
    logger = get_logger(__name__)

    logger.info("request", method="POST", path="/v1/messages", status_code=200, duration_ms=42)
"""

import logging
import sys
from typing import Any

import structlog


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog to emit JSON to stdout.

    Configures a processor chain that:
      - Adds the stdlib log level name (``level`` key)
      - Formats exception info (``exception`` key with full traceback)
      - Adds an ISO-8601 timestamp (``timestamp`` key)
      - Renders the event dictionary as a single JSON line

    The standard-library root logger is also reconfigured so that libraries
    using ``logging.getLogger(...)`` are visible through structlog.

    Args:
        level: One of ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``, ``CRITICAL``.
            Defaults to ``"INFO"``.
    """
    log_level: int = getattr(logging, level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso")

    structlog.configure(
        processors=[
            # Prepend the stdlib log level name (e.g. "info" / "error").
            structlog.stdlib.add_log_level,
            # If the log record contains an exception, render its traceback
            # into the ``exception`` key before the JSON renderer runs.
            structlog.processors.format_exc_info,
            # Add an ISO-8601 timestamp.
            timestamper,
            # Render the event dict as a single JSON line on stdout.
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Reconfigure the stdlib root logger so that:
    #   - It writes to stdout (not stderr, for container/log-aggregator
    #     friendliness).
    #   - Its level matches what the caller requested.
    #   - Its format is the raw message only — structlog handles all the
    #     structuring, so we just want the rendered string.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )


def get_logger(name: str = __name__):
    """Return a structlog logger with the module name bound as context.

    Args:
        name: Typically ``__name__`` so the logger carries the caller's
            module path in the ``event`` key.

    Returns:
        A ``structlog.stdlib.BoundLogger`` ready for structured calls like
        ``logger.info("something_happened", key=val)``.
    """
    return structlog.get_logger(name)


# ---------------------------------------------------------------------------
# Convenience helpers for the proxy gateway's standard log shapes
# ---------------------------------------------------------------------------


def log_request(
    logger: Any,
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
) -> None:
    """Emit a structured request log line.

    Args:
        logger: A structlog logger (from :func:`get_logger`).
        method: HTTP method, e.g. ``"POST"``.
        path: Request path, e.g. ``"/v1/messages"``.
        status_code: HTTP status code returned to the client.
        duration_ms: Wall-clock processing duration in milliseconds.
    """
    logger.info(
        "request",
        method=method,
        path=path,
        status_code=status_code,
        duration_ms=duration_ms,
    )


def log_vision(
    logger: Any,
    image_count: int,
    vision_duration_ms: float,
) -> None:
    """Emit a structured log line for the Vision stage.

    Args:
        logger: A structlog logger (from :func:`get_logger`).
        image_count: Number of images that were processed.
        vision_duration_ms: Time spent in the Vision Client in milliseconds.
    """
    logger.info(
        "vision_complete",
        image_count=image_count,
        vision_duration_ms=vision_duration_ms,
    )
