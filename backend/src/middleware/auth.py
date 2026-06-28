"""
Authentication middleware for the multimodal proxy gateway.

If PROXY_API_KEY is configured, ``x-api-key`` must match it.
If PROXY_API_KEY is empty (default), all requests pass through.
The ``x-api-key`` header value is preserved in request state so the
pipeline can forward it as the target API key.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_PUBLIC_PATHS: set[str] = {"/", "/health"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validate optional proxy API key, then stash client key for target forwarding."""

    def __init__(self, app, api_key: str = "") -> None:
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"

        if path in _PUBLIC_PATHS:
            return await call_next(request)

        # Read auth from x-api-key or Authorization: Bearer header.
        client_key = request.headers.get("x-api-key", "")
        if not client_key:
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                client_key = auth[7:]

        # Log incoming headers for debugging.
        import logging
        _log = logging.getLogger(__name__)
        _log.debug("Incoming headers: x-api-key=%s auth=%s",
                   request.headers.get("x-api-key", "<none>"),
                   request.headers.get("authorization", "<none>"))

        # If proxy auth is configured, enforce it.
        if self._api_key and client_key != self._api_key:
            return JSONResponse(status_code=401, content={"error": "invalid_api_key"})

        # Stash the client key — it's the target API key for forwarding.
        request.state.target_api_key = client_key
        return await call_next(request)
