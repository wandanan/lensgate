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

    def handle_stream(
        self,
        generator: AsyncGenerator[str, None],
        source_format: str,
    ) -> StreamingResponse:
        """Handle a streaming (SSE) response.

        The generator yields SSE lines (each ending with ``\\n``,
        consecutive lines form ``\\n\\n`` separators).  Status events
        from the pipeline are mixed in by the caller.
        """
        return StreamingResponse(
            content=generator,
            media_type="text/event-stream",
        )

    @staticmethod
    def status_event(message: str) -> str:
        """Build a progress status SSE event for the pipeline."""
        return (
            f"event: status\ndata: {json.dumps({'type': 'processing', 'message': message}, ensure_ascii=False)}\n\n"
        )
