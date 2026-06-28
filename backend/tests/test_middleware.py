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

from backend.src.config import ProxyConfig
from backend.src.middleware.auth import APIKeyMiddleware
from backend.src.models import ProxyRequest
from backend.src.pipeline import process_request, run_pipeline


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


def _make_pipeline_app() -> FastAPI:
    """Create a FastAPI app that uses run_pipeline for Content-Length testing."""
    from backend.src.error_handler import register_error_handlers

    app = FastAPI()
    register_error_handlers(app)

    @app.post("/v1/messages")
    async def messages(request: Request):
        return await run_pipeline(request, ProxyConfig())

    return app


# ============================================================================
# TC-C06-BLD-001: Compile check
# ============================================================================


def test_compile_check_auth_py():
    """TC-C06-BLD-001: auth.py compiles without syntax errors."""
    src_dir = Path(__file__).resolve().parent.parent / "src"
    auth_path = src_dir / "middleware" / "auth.py"
    py_compile.compile(str(auth_path), doraise=True)


def test_compile_check_pipeline_py():
    """pipeline.py compiles without syntax errors."""
    src_dir = Path(__file__).resolve().parent.parent / "src"
    pipeline_path = src_dir / "pipeline.py"
    py_compile.compile(str(pipeline_path), doraise=True)


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
# TC-C06-API-004: Content-Length > 10MB → 413
# ============================================================================


def test_content_length_exceeds_10mb_returns_413():
    """TC-C06-API-004 / TC-C06-SYS-004: Content-Length > 10MB → 413."""
    app = _make_pipeline_app()
    client = TestClient(app)

    # 12 MB as Content-Length, but send a tiny body.
    resp = client.post(
        "/v1/messages",
        content=b'{"messages":[{"role":"user","content":"hi"}]}',
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(12 * 1024 * 1024),  # 12 582 912
        },
    )
    assert resp.status_code == 413
    data = resp.json()
    assert data["error"] == "payload_too_large"


def test_content_length_within_limit_passes():
    """Content-Length within 10 MB passes through to body parsing."""
    app = _make_pipeline_app()
    client = TestClient(app)

    resp = client.post(
        "/v1/messages",
        content=b'{"messages":[{"role":"user","content":"hi"}]}',
        headers={
            "Content-Type": "application/json",
            "Content-Length": "100",
        },
    )
    # 200 because pipeline forwards (placeholder mode, no auth configured)
    assert resp.status_code == 200


# ============================================================================
# TC-C06-API-005: Empty messages → 400
# ============================================================================


def test_empty_messages_array_returns_400():
    """TC-C06-API-005 / TC-C06-SYS-005: empty messages → 400."""
    app = _make_pipeline_app()
    client = TestClient(app)

    resp = client.post(
        "/v1/messages",
        json={"messages": []},
        headers={"x-api-key": "secret"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "invalid_request"
    assert "messages" in data["message"].lower()


def test_missing_messages_field_returns_400():
    """Missing 'messages' key also returns 400."""
    app = _make_pipeline_app()
    client = TestClient(app)

    resp = client.post(
        "/v1/messages",
        json={"model": "claude"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "invalid_request"
    assert "messages" in data["message"].lower()


# ============================================================================
# TC-C06-API-006: Pure-text passthrough (Vision not called)
# ============================================================================


@pytest.mark.asyncio
async def test_pure_text_fast_path_vision_not_called():
    """TC-C06-API-006 / TC-C06-SYS-006: pure-text → Vision never called,
    forward receives original_body."""
    vision_mock = AsyncMock()
    rewriter_mock = AsyncMock()
    forward_mock = AsyncMock(return_value={"status": "ok"})

    original_body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hello world"}],
    }

    result = await process_request(
        body=original_body,
        path="/v1/messages",
        config=ProxyConfig(),
        vision_recognize=vision_mock,
        rewrite_request=rewriter_mock,
        forward_to_target=forward_mock,
    )

    # Vision Client MUST NOT have been called.
    vision_mock.assert_not_called()

    # Request Rewriter MUST NOT have been called.
    rewriter_mock.assert_not_called()

    # Forward MUST have received original_body as first positional arg.
    forward_mock.assert_called_once()
    forwarded_body = forward_mock.call_args[0][0]
    assert forwarded_body == original_body

    # Result is whatever forward returned.
    assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_pure_text_openai_also_skips_vision():
    """OpenAI pure-text requests also skip Vision and Rewriter."""
    vision_mock = AsyncMock()
    forward_mock = AsyncMock(return_value={"status": "ok"})

    original_body = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "hi"}],
    }

    await process_request(
        body=original_body,
        path="/v1/chat/completions",
        config=ProxyConfig(),
        vision_recognize=vision_mock,
        forward_to_target=forward_mock,
    )

    vision_mock.assert_not_called()
    forward_mock.assert_called_once()
    assert forward_mock.call_args[0][0] == original_body


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


@pytest.mark.asyncio
async def test_pipeline_placeholder_response_no_forward():
    """When no forward_to_target is wired, pipeline returns placeholder."""
    body = {
        "model": "claude",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hello"}],
    }

    result = await process_request(
        body=body,
        path="/v1/messages",
        config=ProxyConfig(),
    )

    assert result["status"] == "forwarded"
    assert result["source_format"] == "anthropic"
    assert result["target_model"] == "claude"
    assert result["message_count"] == 1
    assert result["image_count"] == 0


@pytest.mark.asyncio
async def test_pipeline_placeholder_response_with_image():
    """Placeholder response correctly counts image blocks."""
    body = {
        "model": "claude",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(b"fake-image-data").decode(),
                        },
                    },
                ],
            }
        ],
    }

    result = await process_request(
        body=body,
        path="/v1/messages",
        config=ProxyConfig(),
    )

    assert result["status"] == "forwarded"
    assert result["image_count"] == 1
    assert result["source_format"] == "anthropic"


def test_content_length_malformed_header_passes():
    """Malformed Content-Length header is ignored (body parsing handles it)."""
    app = _make_pipeline_app()
    client = TestClient(app)

    resp = client.post(
        "/v1/messages",
        content=b'{"messages":[{"role":"user","content":"hi"}]}',
        headers={
            "Content-Type": "application/json",
            "Content-Length": "not-a-number",
        },
    )
    # Should pass through (200 = placeholder forward response)
    assert resp.status_code == 200


def test_invalid_json_returns_400():
    """Invalid JSON body returns 400 even through pipeline."""
    app = _make_pipeline_app()
    client = TestClient(app)

    resp = client.post(
        "/v1/messages",
        content=b"this is not json {{{",
        headers={"Content-Type": "application/json", "x-api-key": "secret"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "invalid_request"


# ============================================================================
# System behaviour equivalents (TC-C06-SYS-001 through SYS-006)
# The API-level tests above already cover each SYS case.  These are aliases
# to make the traceability explicit.
# ============================================================================


def test_sys_001_missing_api_key():
    """TC-C06-SYS-001: §6 — 缺失 x-api-key → 401."""
    test_missing_api_key_returns_401()


def test_sys_002_invalid_api_key():
    """TC-C06-SYS-002: §6 — 无效 x-api-key → 401."""
    test_invalid_api_key_returns_401()


def test_sys_003_valid_api_key():
    """TC-C06-SYS-003: §6 — 有效 x-api-key → 继续."""
    test_valid_api_key_continues()


def test_sys_004_content_length():
    """TC-C06-SYS-004: §6 — Content-Length > 10MB → 413."""
    test_content_length_exceeds_10mb_returns_413()


def test_sys_005_empty_messages():
    """TC-C06-SYS-005: §7.2 — 空 messages → 400."""
    test_empty_messages_array_returns_400()


@pytest.mark.asyncio
async def test_sys_006_pure_text():
    """TC-C06-SYS-006: F06 — 纯文本直通零开销."""
    await test_pure_text_fast_path_vision_not_called()
