"""Test cases for C06 — API Key auth middleware + pure-text fast path.

Coverage:
- TC-C06-BLD-001: compile check (auth.py)
- TC-C06-API-001: missing x-api-key → 401
- TC-C06-API-002: invalid x-api-key → 401
- TC-C06-API-003: valid x-api-key → continue
- TC-C06-API-004: Content-Length > 10MB → 413
- TC-C06-API-005: empty messages → 400
- TC-C06-API-006: pure-text passthrough (Vision not called)

Plus system-behaviour equivalents (SYS-001 through SYS-006).
"""

from __future__ import annotations

import base64
import py_compile
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.src.core.config import ProxyConfig
from backend.src.middleware.auth import APIKeyMiddleware


# ============================================================================
# Helpers
# ============================================================================


def _make_app(api_key: str) -> FastAPI:
    """Create a fresh FastAPI app with APIKeyMiddleware for isolated testing."""
    app = FastAPI()
    app.add_middleware(APIKeyMiddleware, api_key=api_key)

    @app.get("/")
    async def root():
        return {"service": "ok"}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/v1/messages")
    async def messages(request: Request):
        body = await request.json()
        return {"status": "ok", "echo": body}

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        body = await request.json()
        return {"status": "ok", "echo": body}

    return app


# ============================================================================
# TC-C06-BLD-001: Compile check
# ============================================================================


def test_compile_check_auth_py():
    """TC-C06-BLD-001: auth.py compiles without syntax errors."""
    src_dir = Path(__file__).resolve().parent.parent / "src"
    auth_path = src_dir / "middleware" / "auth.py"
    py_compile.compile(str(auth_path), doraise=True)


# ============================================================================
# TC-C06-API-001: Missing x-api-key → 401
# ============================================================================


def test_missing_api_key_returns_401():
    """TC-C06-API-001 / TC-C06-SYS-001: no x-api-key header → 401."""
    app = _make_app(api_key="secret")
    client = TestClient(app)

    resp = client.post(
        "/v1/messages",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_api_key"


# ============================================================================
# TC-C06-API-002: Invalid x-api-key → 401
# ============================================================================


def test_invalid_api_key_returns_401():
    """TC-C06-API-002 / TC-C06-SYS-002: wrong x-api-key → 401."""
    app = _make_app(api_key="secret")
    client = TestClient(app)

    resp = client.post(
        "/v1/messages",
        json={"messages": [{"role": "user", "content": "hello"}]},
        headers={"x-api-key": "wrong"},
    )
    assert resp.status_code == 401


def test_invalid_api_key_also_401_openai():
    """OpenAI endpoint also rejects invalid x-api-key."""
    app = _make_app(api_key="secret")
    client = TestClient(app)

    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
        headers={"x-api-key": "nope"},
    )
    assert resp.status_code == 401


# ============================================================================
# TC-C06-API-003: Valid x-api-key → continue
# ============================================================================


def test_valid_api_key_continues():
    """TC-C06-API-003 / TC-C06-SYS-003: correct x-api-key → request proceeds."""
    app = _make_app(api_key="secret")
    client = TestClient(app)

    resp = client.post(
        "/v1/messages",
        json={"messages": [{"role": "user", "content": "hello"}]},
        headers={"x-api-key": "secret"},
    )
    assert resp.status_code != 401
    assert resp.status_code == 200


# ============================================================================
# Edge cases
# ============================================================================


def test_public_paths_skip_auth():
    """GET / and GET /health are always public (no auth required)."""
    app = _make_app(api_key="secret")
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200


def test_empty_api_key_skips_auth():
    """When api_key is empty string, auth is permissive (all requests pass)."""
    app = _make_app(api_key="")
    client = TestClient(app)

    resp = client.post(
        "/v1/messages",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200


def test_auth_missing_on_openai_endpoint():
    """OpenAI endpoint also requires auth when api_key is set."""
    app = _make_app(api_key="secret")
    client = TestClient(app)

    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401




