"""
Target model client for forwarding requests to Volcengine Coding Plan.

Implements the Anthropic-compatible Messages API forwarding to
Volcengine's Coding Plan endpoint. Supports both non-streaming and
streaming (SSE) response paths.

Error handling at this layer is intentionally minimal: httpx.HTTPStatusError
and httpx.TimeoutException are re-raised for the ResponseHandler (C07) to
convert into appropriate client-facing error codes (503/504).
"""

from __future__ import annotations

from typing import AsyncGenerator

import httpx

from backend.src.core.models import TargetModelConfig


class TargetModelClient:
    """Forwards proxy requests to a target LLM model API endpoint.

    Communicates with the target model using the Anthropic-compatible
    Messages API (POST /v1/messages).  Each request is authenticated
    via ``x-api-key`` and declares ``anthropic-version: 2023-06-01``.

    Parameters:
        router: The model router used by the pipeline to resolve
                requested model names to TargetModelConfig instances.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Internal client lifecycle
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        """Return (or lazily create) the shared httpx AsyncClient."""
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    async def close(self) -> None:
        """Gracefully close the underlying httpx client.

        Safe to call multiple times; no-op when already closed.
        """
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Header construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_headers(config: TargetModelConfig) -> dict[str, str]:
        """Build request headers for the target model API.

        Returns a dict with ``x-api-key``, ``anthropic-version``, and
        ``Content-Type`` populated from *config*.
        """
        return {
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def forward(
        self, request_body: dict, config: TargetModelConfig
    ) -> httpx.Response:
        """Forward a non-streaming POST request to the target model.

        Uses ``config.api_base`` as the full target URL (no suffix appended).
        """
        url = config.api_base
        headers = self._build_headers(config)
        client = self._get_client()

        response = await client.post(
            url,
            json=request_body,
            headers=headers,
            timeout=httpx.Timeout(60.0),
        )
        return response

    async def forward_raw(
        self, method: str, url: str, headers: dict[str, str], api_key: str,
    ) -> httpx.Response:
        """Forward a raw request (GET/HEAD/etc.) to the target without body processing.

        Strips hop-by-hop headers and injects auth.
        """
        # Remove hop-by-hop / host headers
        fwd_headers = {
            k: v for k, v in headers.items()
            if k.lower() not in (
                "host", "content-length", "transfer-encoding",
                "connection", "x-api-key", "x-target-base-url",
            )
        }
        if api_key:
            fwd_headers["x-api-key"] = api_key

        client = self._get_client()
        return await client.request(
            method, url, headers=fwd_headers, timeout=httpx.Timeout(30.0),
        )

    async def forward_passthrough(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        api_key: str,
    ) -> httpx.Response:
        """Forward a request verbatim (headers + body) to the target URL.

        Used for non-conversational endpoints such as Anthropic's
        ``/v1/messages/count_tokens`` — their request/response shape differs
        from ``/v1/messages`` and must bypass the vision/rewrite pipeline.
        The target URL keeps its full suffix (e.g. ``/count_tokens``).
        """
        fwd_headers = {
            k: v for k, v in headers.items()
            if k.lower() not in (
                "host", "content-length", "transfer-encoding",
                "connection", "x-api-key", "x-target-base-url",
            )
        }
        if api_key:
            fwd_headers["x-api-key"] = api_key

        client = self._get_client()
        return await client.request(
            method, url, headers=fwd_headers, content=body,
            timeout=httpx.Timeout(60.0),
        )

    async def forward_stream(
        self, request_body: dict, config: TargetModelConfig
    ) -> AsyncGenerator[str, None]:
        """Forward a streaming POST request to the target model.

        Uses ``config.api_base`` as the full target URL.

        Read timeout is disabled (``read=None``) because SSE streams can have
        arbitrarily long pauses between events — thinking/推理 models routinely
        idle for 30-120 s between tokens, and a read timeout would kill the
        stream mid-response, producing an unhandled ``httpx.ReadTimeout`` that
        propagates through Starlette as a raw 500.
        """
        url = config.api_base
        headers = self._build_headers(config)

        body: dict = {**request_body, "stream": True}
        client = self._get_client()

        try:
            async with client.stream(
                "POST",
                url,
                json=body,
                headers=headers,
                timeout=httpx.Timeout(connect=30.0, read=None, write=60.0, pool=10.0),
            ) as response:
                async for line in response.aiter_lines():
                    yield line + "\n"
        except httpx.TimeoutException:
            # connect / write timeout (read is disabled).  Emit a graceful
            # SSE error so the client sees a structured failure instead of
            # a mid-stream hang or a raw 500 from Starlette.
            yield 'data: {"type":"error","error":"target_stream_timeout"}\n\n'
