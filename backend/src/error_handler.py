"""
Error handling and HTTP exception mapping for the multimodal proxy gateway.

Defines the custom exception hierarchy and FastAPI exception handler
registration for unified error responses. All proxy errors extend
``AppError`` so a single ``@app.exception_handler(AppError)`` catches
every error type and returns a consistent JSON envelope:

.. code-block:: json

    {"error": "<error_type>", "message": "<human-readable message>"}

Error-code mapping (from target client errors to HTTP responses):

| Scenario                          | Exception                      | HTTP Status | error_type                |
|-----------------------------------|--------------------------------|-------------|---------------------------|
| Invalid JSON body                 | InvalidRequestError            | 400         | "invalid_request"         |
| Target model 5xx                  | TargetModelUnavailableError    | 503         | "target_model_unavailable"|
| Target model timeout              | TargetModelTimeoutError        | 504         | "target_model_timeout"    |
| Vision failure (degraded)         | (no exception — fallback text) | 200         | N/A                       |
| Content-Length > 10 MB            | PayloadTooLargeError           | 413         | "payload_too_large"       |
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.src.config import ProxyConfig

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class AppError(Exception):
    """Base class for all proxy gateway errors.

    Every proxy error extends this class so that a single
    ``@app.exception_handler(AppError)`` registration catches all of them.

    Parameters:
        message:     Human-readable error description.
        status_code: HTTP status code to return to the client.
        error_type:  Machine-readable error code (e.g. ``"invalid_request"``).
    """

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        error_type: str = "internal_error",
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        super().__init__(message)


class InvalidRequestError(AppError):
    """Client sent an invalid request (bad JSON, missing fields, etc.).

    Mapped to HTTP 400 — ``"invalid_request"``.
    """

    def __init__(self, message: str = "Invalid request") -> None:
        super().__init__(message, status_code=400, error_type="invalid_request")


class TargetModelUnavailableError(AppError):
    """Target model API returned 5xx or is otherwise unreachable.

    Mapped to HTTP 503 — ``"target_model_unavailable"``.
    """

    def __init__(self, message: str = "Target model unavailable") -> None:
        super().__init__(
            message, status_code=503, error_type="target_model_unavailable"
        )


class TargetModelTimeoutError(AppError):
    """Request to the target model timed out.

    Mapped to HTTP 504 — ``"target_model_timeout"``.
    """

    def __init__(self, message: str = "Target model request timed out") -> None:
        super().__init__(
            message, status_code=504, error_type="target_model_timeout"
        )


class VisionDegradationError(AppError):
    """Vision API failed but the request continues with degradation text.

    This exception exists for type classification and test assertions.
    It should NOT be raised during normal pipeline execution — vision
    failures always degrade gracefully via ``"[图片无法识别]"`` fallback text.
    """

    def __init__(self, message: str = "Vision recognition degraded") -> None:
        super().__init__(message, status_code=200, error_type="vision_degraded")


class PayloadTooLargeError(AppError):
    """Request payload exceeds the 10 MB maximum size limit.

    Mapped to HTTP 413 — ``"payload_too_large"``.
    """

    def __init__(self, message: str = "Payload too large") -> None:
        super().__init__(
            message, status_code=413, error_type="payload_too_large"
        )


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------


def register_error_handlers(app: FastAPI) -> None:
    """Register global exception handlers on *app*.

    Catches every ``AppError`` subclass and returns a consistent JSON
    response envelope::

        {"error": "<error_type>", "message": "<message>"}

    Usage::

        from backend.src.error_handler import register_error_handlers
        register_error_handlers(app)
    """

    @app.exception_handler(AppError)
    async def _app_error_handler(
        request: Request, exc: AppError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error_type, "message": exc.message},
        )


# ---------------------------------------------------------------------------
# Configuration validation (§7.1 — 配置缺失启动检查)
# ---------------------------------------------------------------------------


def check_config(config: ProxyConfig) -> None:
    """Validate required configuration fields at startup.

    Calls ``config.validate_required()`` internally and re-raises any
    ``ValueError`` as a ``RuntimeError`` so that the process exits with a
    clear, non-zero exit code.

    Raises:
        RuntimeError: If ``VISION_API_KEY`` or ``TARGET_DEFAULT_API_KEY``
                      is empty / unset.
    """
    try:
        config.validate_required()
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
