"""API runtime tests for route handlers.

Coverage:
- Error handling (invalid JSON, missing fields)
- Route existence and middleware
- Forwarding with mocked target client
"""

from __future__ import annotations

import base64
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure required env vars are set before importing the app module.
os.environ.setdefault("VISION_API_KEY", "test-vision-key")

from backend.src.app import app  # noqa: E402

client = TestClient(app)

# Pre-computed sample base64-encoded image data
_SAMPLE_B64 = base64.b64encode(b"fake-image-data").decode()

TEST_TARGET = "test-target"


# ---------------------------------------------------------------------------
# Non-API quick checks (smoke)
# ---------------------------------------------------------------------------


def test_health_200():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Error paths — invalid JSON
# ---------------------------------------------------------------------------


def test_anthropic_invalid_json_400():
    """Invalid JSON returns 400 with error detail."""
    resp = client.post(
        f"/{TEST_TARGET}/v1/messages",
        content=b"this is not json {{{",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "invalid_request"

    resp2 = client.post(
        f"/{TEST_TARGET}/v1/messages",
        content=b"",
        headers={"Content-Type": "application/json"},
    )
    assert resp2.status_code == 400
    data2 = resp2.json()
    assert data2["error"] == "invalid_request"


def test_anthropic_invalid_json_400_openai_endpoint():
    """OpenAI endpoint also returns 400 on bad JSON."""
    resp = client.post(
        f"/{TEST_TARGET}/v1/chat/completions",
        content=b"garbage",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "invalid_request"


# ---------------------------------------------------------------------------
# Valid requests — with mocked target client
# ---------------------------------------------------------------------------


def _make_mock_target_response(body: dict | None = None) -> MagicMock:
    if body is None:
        body = {
            "id": "msg_test_001",
            "type": "message",
            "role": "assistant",
            "model": "deepseek-v3.2",
            "content": [{"type": "text", "text": "Hello from mocked target!"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 10},
        }
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = body
    return resp


def test_anthropic_valid_text_request():
    """Anthropic pure-text request is forwarded and target response returned."""
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hello"}],
    }
    mock_resp = _make_mock_target_response()

    with patch(
        "backend.src.app._get_target_client"
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.forward.return_value = mock_resp
        mock_get_client.return_value = mock_client

        resp = client.post(f"/{TEST_TARGET}/v1/messages", json=body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "assistant"
    assert "Hello from mocked target!" in data["content"][0]["text"]


def test_openai_valid_text_request():
    """OpenAI pure-text request is forwarded and target response returned."""
    openai_body = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello from OpenAI!"},
                "finish_reason": "stop",
            }
        ],
    }
    mock_resp = _make_mock_target_response(openai_body)
    body = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "hello"}],
    }

    with patch(
        "backend.src.app._get_target_client"
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.forward.return_value = mock_resp
        mock_get_client.return_value = mock_client

        resp = client.post(f"/{TEST_TARGET}/v1/chat/completions", json=body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"


def test_anthropic_stream_flag():
    """Anthropic request with stream=true uses streaming forward path."""
    body = {
        "model": "claude",
        "max_tokens": 100,
        "stream": True,
        "messages": [{"role": "user", "content": "hello"}],
    }

    mock_stream_resp = MagicMock()
    mock_stream_resp.status_code = 200

    with patch(
        "backend.src.app._get_target_client"
    ) as mock_get_client:
        mock_client = MagicMock()
        mock_client.forward_stream.return_value = _make_async_gen(
            ['data: {"type":"message_start"}\n', 'data: {"type":"message_stop"}\n']
        )
        mock_get_client.return_value = mock_client

        resp = client.post(f"/{TEST_TARGET}/v1/messages", json=body)

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_async_gen(lines: list[str]):
    for line in lines:
        yield line
