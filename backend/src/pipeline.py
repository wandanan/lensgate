"""
Pipeline orchestrator for the multimodal proxy gateway.

Connects all 7 processing stages (per architecture.md §2):

| Stage | Module           | Pure-text path |
|-------|------------------|----------------|
| 1     | Format Detector  | ✓              |
| 2     | Image Extractor  | ✓              |
| 3     | Vision Client    | ✗ (skipped)    |
| 4     | Request Rewriter | ✗ (skipped)    |
| 5     | Model Router     | ✓              |
| 6     | Target Client    | ✓              |
| 7     | Response Handler | ✓              |

Pure-text requests skip stages 3–4 entirely and forward ``original_body``
directly to the target model without any Vision / Rewriter overhead.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

import httpx
from fastapi import Request

from backend.src.config import ProxyConfig
from backend.src.error_handler import (
    InvalidRequestError,
    PayloadTooLargeError,
    TargetModelUnavailableError,
    TargetModelTimeoutError,
)
from backend.src.format_detector import (
    detect_format,
    parse_anthropic_request,
    parse_openai_request,
)
from backend.src.image_extractor import extract_images, has_images
from backend.src.models import ProxyRequest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum allowed request body size (10 MB).
_MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 485 760 bytes


# ---------------------------------------------------------------------------
# Public API — HTTP entry point
# ---------------------------------------------------------------------------


async def run_pipeline(
    request: Request,
    config: ProxyConfig,
    *,
    vision_recognize: Callable[..., Awaitable] | None = None,
    rewrite_request: Callable[..., Awaitable] | None = None,
    forward_to_target: Callable[..., Awaitable] | None = None,
) -> Any:
    """Run the full proxy pipeline from an incoming HTTP request.

    This is the HTTP-facing entry point.  It performs pre-body checks
    (Content-Length validation) and JSON parsing before delegating to
    :func:`process_request` for the core pipeline logic.

    Injectable callables (``vision_recognize``, ``rewrite_request``,
    ``forward_to_target``) allow tests to mock individual pipeline stages.
    When omitted the pipeline uses placeholder/default behaviour for
    stages that have not been wired yet (see individual task handoffs).
    """

    # --- Pre-body check: Content-Length must be < 10 MB -------------------
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_BODY_SIZE:
                raise PayloadTooLargeError(
                    f"Request body exceeds {_MAX_BODY_SIZE // (1024 * 1024)} MB limit"
                )
        except ValueError:
            # Malformed Content-Length header — let body parsing handle it.
            pass

    # --- Parse JSON body --------------------------------------------------
    try:
        body = await request.json()
    except Exception:
        raise InvalidRequestError("Failed to parse JSON request body")

    # --- Delegate to core pipeline ----------------------------------------
    return await process_request(
        body=body,
        path=str(request.url.path),
        config=config,
        vision_recognize=vision_recognize,
        rewrite_request=rewrite_request,
        forward_to_target=forward_to_target,
    )


# ---------------------------------------------------------------------------
# Core pipeline (testable without HTTP Request)
# ---------------------------------------------------------------------------


async def process_request(
    body: dict,
    path: str,
    config: ProxyConfig,
    *,
    vision_recognize: Callable[..., Awaitable] | None = None,
    rewrite_request: Callable[..., Awaitable] | None = None,
    forward_to_target: Callable[..., Awaitable] | None = None,
) -> Any:
    """Execute the core proxy pipeline from a parsed request body.

    Args:
        body: The raw JSON request body.
        path: The HTTP request path (used for format detection).
        config: Proxy gateway configuration.
        vision_recognize: Injectable Vision Client callable.
        rewrite_request: Injectable Request Rewriter callable.
        forward_to_target: Injectable Target Client callable.

    Returns:
        The target model response, or a placeholder forwarded response
        when no ``forward_to_target`` is wired.

    Raises:
        InvalidRequestError: If ``messages`` is missing or empty.
    """

    # --- Business rule: messages must be non-empty ------------------------
    messages = body.get("messages")
    if not messages or (isinstance(messages, list) and len(messages) == 0):
        raise InvalidRequestError("messages field is required and must be non-empty")

    # --- Stage 1: Format Detection ----------------------------------------
    fmt = detect_format(path, body)
    if fmt == "anthropic":
        proxy_request = parse_anthropic_request(body)
    else:
        proxy_request = parse_openai_request(body)

    # --- Stage 2: Image Check + Branch -----------------------------------
    if has_images(proxy_request):
        logger.info("Images detected — entering full pipeline (stages 3-7)")

        # Extract all image blocks (resolve binary data).
        images = await extract_images(proxy_request)

        # Stage 3: Vision Client (wired by task C02).
        descriptions: list[str] = []
        if vision_recognize is not None:
            descriptions = await vision_recognize(images)

        # Stage 4: Request Rewriter (wired by task C03).
        body_to_forward: dict = proxy_request.original_body
        if rewrite_request is not None and descriptions:
            body_to_forward = await rewrite_request(proxy_request, descriptions)

        # Stage 5–7: Model Router + Target Client + Response Handler.
        if forward_to_target is not None:
            return await _safe_forward(forward_to_target, body_to_forward, proxy_request, config)
        return _placeholder_forward_response(proxy_request)

    else:
        logger.info("Pure-text request — fast path (skip Vision + Rewriter)")

        # Pure-text fast path: forward original_body directly.
        body_to_forward: dict = proxy_request.original_body

        if forward_to_target is not None:
            return await _safe_forward(forward_to_target, body_to_forward, proxy_request, config)
        return _placeholder_forward_response(proxy_request)


# ---------------------------------------------------------------------------
# Placeholder (until Target Client is wired in C05)
# ---------------------------------------------------------------------------


async def _safe_forward(
    forward_to_target: Callable[..., Awaitable],
    body_to_forward: dict,
    proxy_request: ProxyRequest,
    config: ProxyConfig,
) -> Any:
    """Call *forward_to_target* with error mapping to AppError subclasses.

    Catches:
    - ``httpx.TimeoutException`` → ``TargetModelTimeoutError`` (504)
    - ``httpx.HTTPStatusError`` with 5xx → ``TargetModelUnavailableError`` (503)
    - ``httpx.Response`` with status_code >= 500 → ``TargetModelUnavailableError``

    All other exceptions propagate unchanged so FastAPI's default 500 handler
    can log the traceback.
    """
    try:
        result = await forward_to_target(body_to_forward, proxy_request, config)
    except httpx.TimeoutException as exc:
        raise TargetModelTimeoutError(
            f"Target model request timed out: {exc}"
        ) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500:
            raise TargetModelUnavailableError(
                f"Target model returned {exc.response.status_code}: {exc}"
            ) from exc
        raise

    # If the forwarder returns a raw httpx.Response, check its status code.
    if isinstance(result, httpx.Response) and result.status_code >= 500:
        raise TargetModelUnavailableError(
            f"Target model returned status {result.status_code}"
        )

    return result


def _placeholder_forward_response(proxy_request: ProxyRequest) -> dict:
    """Return a structured placeholder response.

    This is used when no ``forward_to_target`` callable has been injected
    (i.e. the Target Client has not been wired yet).  It returns a
    machine-readable summary so that upstream tests and early integrations
    can verify the pipeline executed correctly.
    """
    image_count = sum(
        1
        for msg in proxy_request.messages
        for blk in msg.content
        if blk.__class__.__name__ == "ImageBlock"
    )
    return {
        "status": "forwarded",
        "source_format": proxy_request.source_format,
        "target_model": proxy_request.target_model,
        "message_count": len(proxy_request.messages),
        "image_count": image_count,
        "stream": proxy_request.stream,
    }
