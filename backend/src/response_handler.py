"""
Response handler for the multimodal proxy gateway.

Unified handling of streaming (SSE) and non-streaming (JSON) target model
responses.  Converts raw httpx.Response objects or async SSE generators
into FastAPI-compatible response objects.

SSE event types are forwarded unchanged, preserving the native Anthropic
format: message_start, content_block_start, content_block_delta,
content_block_stop, message_delta, message_stop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)


class ResponseHandler:
    """Convert target model responses into FastAPI response objects.

    Handles both non-streaming (JSON) and streaming (SSE) response paths
    with format-agnostic processing — the *source_format* parameter is
    accepted for future format-specific handling but currently both
    Anthropic and OpenAI formats are returned as-is.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle_non_stream(
        self, target_response, source_format: str
    ) -> JSONResponse:
        """Handle a non-streaming target model response.

        Reads the JSON body from *target_response* (an ``httpx.Response``)
        and wraps it in a ``fastapi.responses.JSONResponse`` with the
        same status code and ``Content-Type: application/json``.

        Parameters:
            target_response: The raw ``httpx.Response`` from
                             ``TargetClient.forward()``.
            source_format:   The original API format (``"anthropic"`` or
                             ``"openai"``).

        Returns:
            A ``JSONResponse`` populated with the target model's body and
            status code.
        """
        body = target_response.json()
        return JSONResponse(
            content=body,
            status_code=target_response.status_code,
            headers={"Content-Type": "application/json"},
        )

    async def handle_stream(
        self,
        target_stream_generator: AsyncGenerator[str, None],
        source_format: str,
    ) -> StreamingResponse:
        """Handle a streaming (SSE) target model response.

        Wraps the async generator from ``TargetClient.forward_stream()``
        in a ``fastapi.responses.StreamingResponse`` with
        ``media_type="text/event-stream"``.

        SSE events (message_start, content_block_delta, message_stop,
        etc.) are forwarded unchanged — the generator is yielded
        line-by-line.

        Parameters:
            target_stream_generator: Async generator yielding SSE lines
                                     (each line starts with ``"data: "``).
            source_format:           The original API format
                                     (``"anthropic"`` or ``"openai"``).

        Returns:
            A ``StreamingResponse`` that streams SSE events to the client.
        """
        return StreamingResponse(
            content=self._sse_generator(target_stream_generator),
            media_type="text/event-stream",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _sse_generator(
        self, source_generator: AsyncGenerator[str, None]
    ) -> AsyncGenerator[str, None]:
        """Yield each line from the source SSE generator unchanged.

        Each line is already in SSE format (``data: ...``) as produced
        by ``TargetClient.forward_stream()``.  Empty lines have already
        been filtered upstream.

        Error handling (C07 — §7.1):
        - On upstream stream interruption: sends an ``[ERROR]`` SSE event
          before closing the connection (SYS-004).
        - On client disconnect (``asyncio.CancelledError``): the upstream
          generator's ``aclose()`` is invoked automatically by the async
          framework, releasing the httpx stream (SYS-001).
        """
        try:
            async for line in source_generator:
                yield line
        except asyncio.CancelledError:
            # Client disconnected — the upstream httpx stream will be
            # released by the generator's own async context manager.
            logger.info("SSE stream cancelled (client disconnect)")
        except Exception:
            logger.exception("Upstream SSE stream interrupted")
            error_event = json.dumps(
                {"type": "error", "error": {"type": "stream_error", "message": "Upstream stream interrupted"}},
                ensure_ascii=False,
            )
            yield f"data: {error_event}\n\n"
