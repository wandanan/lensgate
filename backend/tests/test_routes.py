"""API runtime tests for route handlers.

Coverage:
- TC-B01-SYS-001: valid Anthropic request → 200
- TC-B01-SYS-002: invalid JSON → 400
- TC-B01-SYS-003: valid OpenAI request → 200
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from backend.src.app import app

client = TestClient(app)

# Pre-computed sample base64-encoded image data
_SAMPLE_B64 = base64.b64encode(b"fake-image-data").decode()


# ---------------------------------------------------------------------------
# Non-API quick checks (smoke)
# ---------------------------------------------------------------------------


def test_root_200():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "多模态代理网关"


def test_health_200():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# TC-B01-SYS-001: valid Anthropic text request → 200
# ---------------------------------------------------------------------------


def test_anthropic_valid_text_request():
    """System behaviour — Anthropic pure-text request returns 200 and parsed info."""
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hello"}],
    }
    resp = client.post("/v1/messages", json=body)
    assert resp.status_code == 200

    data = resp.json()
    assert data["status"] == "forwarded"
    assert data["source_format"] == "anthropic"
    assert data["target_model"] == "claude-sonnet-4-6"
    assert data["message_count"] == 1
    assert data["image_count"] == 0


# ---------------------------------------------------------------------------
# TC-B01-SYS-002: invalid JSON → 400
# ---------------------------------------------------------------------------


def test_anthropic_invalid_json_400():
    """System behaviour — invalid JSON returns 400 with error detail."""
    resp = client.post(
        "/v1/messages",
        content=b"this is not json {{{",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "invalid_request"

    resp2 = client.post(
        "/v1/messages",
        content=b"",
        headers={"Content-Type": "application/json"},
    )
    assert resp2.status_code == 400
    data2 = resp2.json()
    assert data2["error"] == "invalid_request"


# ---------------------------------------------------------------------------
# TC-B01-SYS-003: valid OpenAI request → 200
# ---------------------------------------------------------------------------


def test_openai_valid_text_request():
    """System behaviour — OpenAI pure-text request returns 200 and parsed info."""
    body = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "hello"}],
    }
    resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200

    data = resp.json()
    assert data["status"] == "forwarded"
    assert data["source_format"] == "openai"
    assert data["target_model"] == "gpt-4"
    assert data["message_count"] == 1
    assert data["image_count"] == 0


# ---------------------------------------------------------------------------
# Additional route-level coverage
# ---------------------------------------------------------------------------


def test_anthropic_with_image_200():
    """Anthropic request containing an image block is parsed correctly."""
    body = {
        "model": "claude",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": _SAMPLE_B64,
                        },
                    },
                ],
            }
        ],
    }
    resp = client.post("/v1/messages", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["source_format"] == "anthropic"
    assert data["image_count"] == 1


def test_openai_with_image_url_200():
    """OpenAI request containing an image_url part is parsed correctly."""
    body = {
        "model": "gpt-4",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + _SAMPLE_B64
                        },
                    },
                ],
            }
        ],
    }
    resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["source_format"] == "openai"
    assert data["image_count"] == 1


def test_anthropic_stream_flag():
    """Anthropic request with stream=true is reflected in response."""
    body = {
        "model": "claude",
        "max_tokens": 100,
        "stream": True,
        "messages": [{"role": "user", "content": "hello"}],
    }
    resp = client.post("/v1/messages", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["stream"] is True


def test_anthropic_invalid_json_400_openai_endpoint():
    """OpenAI endpoint also returns 400 on bad JSON."""
    resp = client.post(
        "/v1/chat/completions",
        content=b"garbage",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "invalid_request"
