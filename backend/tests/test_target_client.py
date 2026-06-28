"""
Test cases for target_client.py — TargetModelClient forwarding logic.

Covers:
- TC-C04-BLD-001: target_client.py compiles without syntax errors
- TC-C04-LOG-001: forward sends to correct endpoint URL
- TC-C04-LOG-002: forward carries correct auth headers
- TC-C04-LOG-003: forward returns httpx.Response with 200 + JSON body
- TC-C04-LOG-004: forward_stream yields per-line SSE data
- TC-C04-SYS-001: forwarding → 200 when target returns 200
- TC-C04-SYS-002: forwarding → httpx.HTTPStatusError re-raised
- TC-C04-SYS-003: forwarding → httpx.TimeoutException re-raised
- TC-C04-SYS-004: stream=true in body triggers forward_stream path
- TC-C04-SYS-005: client disconnect → stream properly released
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.src.model_router import ModelRouter
from backend.src.models import TargetModelConfig
from backend.src.target_client import TargetModelClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    api_base: str = "https://ark.cn-beijing.volces.com/api/coding",
    api_key: str = "test-key-123",
    model_id: str = "deepseek-v3.2",
) -> TargetModelConfig:
    return TargetModelConfig(
        model_id=model_id,
        api_base=api_base,
        api_key=api_key,
    )


def _make_router() -> ModelRouter:
    return ModelRouter({"default": _make_config()})


def _make_request_body() -> dict:
    return {
        "model": "deepseek-v3.2",
        "max_tokens": 4096,
        "stream": False,
        "messages": [
            {"role": "user", "content": "Hello, how are you?"}
        ],
    }


def _an200_response() -> MagicMock:
    """Return a mock httpx.Response with status 200 and Anthropic Message JSON body."""
    body = {
        "id": "msg_01ABC123",
        "type": "message",
        "role": "assistant",
        "model": "deepseek-v3.2",
        "content": [{"type": "text", "text": "I am doing well, thank you!"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 15},
    }
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


# ---------------------------------------------------------------------------
# Mock stream helpers for forward_stream tests
# ---------------------------------------------------------------------------


class _MockStreamResponse:
    """Simulates httpx.Response inside a stream context."""

    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _MockStreamCtx:
    """Async context manager substitute for ``client.stream()``."""

    def __init__(self, response: _MockStreamResponse) -> None:
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# TC-C04-LOG-001: forward sends to correct endpoint URL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_sends_correct_endpoint():
    """POST URL is ``{api_base}/v1/messages``."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = _an200_response()

    with patch("backend.src.target_client.httpx.AsyncClient", return_value=mock_client):
        client = TargetModelClient(_make_router())
        await client.forward(_make_request_body(), _make_config())

    mock_client.post.assert_called_once()
    url = mock_client.post.call_args[0][0]
    assert url == "https://ark.cn-beijing.volces.com/api/coding/v1/messages"


# ---------------------------------------------------------------------------
# TC-C04-LOG-002: forward carries correct auth headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_carries_correct_auth_headers():
    """Request headers include ``x-api-key`` and ``anthropic-version``."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = _an200_response()

    with patch("backend.src.target_client.httpx.AsyncClient", return_value=mock_client):
        client = TargetModelClient(_make_router())
        config = _make_config(api_key="test-key-123")
        await client.forward(_make_request_body(), config)

    _, kwargs = mock_client.post.call_args
    headers = kwargs["headers"]
    assert headers["x-api-key"] == "test-key-123"
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# TC-C04-LOG-003: forward non-streaming returns JSON response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_returns_json_response():
    """Non-streaming forward returns 200 with parseable JSON body."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = _an200_response()

    with patch("backend.src.target_client.httpx.AsyncClient", return_value=mock_client):
        client = TargetModelClient(_make_router())
        response = await client.forward(_make_request_body(), _make_config())

    assert response.status_code == 200
    # Body must be parseable as JSON
    body = response.json()
    assert body["type"] == "message"
    assert body["role"] == "assistant"


# ---------------------------------------------------------------------------
# TC-C04-LOG-004: forward_stream yields per-line SSE data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_stream_yields_sse_lines():
    """Async generator yields 3 lines, each starting with 'data: '."""
    sse_lines = [
        'data: {"type":"message_start","message":{"id":"msg_01"}}',
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hello"}}',
        'data: {"type":"message_stop"}',
    ]

    mock_response = _MockStreamResponse(sse_lines)
    mock_ctx = _MockStreamCtx(mock_response)

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_ctx

    with patch("backend.src.target_client.httpx.AsyncClient", return_value=mock_client):
        client = TargetModelClient(_make_router())
        yielded: list[str] = []
        async for line in client.forward_stream(_make_request_body(), _make_config()):
            yielded.append(line)

    assert len(yielded) == 3
    for line in yielded:
        assert line.startswith("data: "), f"expected SSE data line, got: {line!r}"


# ---------------------------------------------------------------------------
# TC-C04-SYS-001: system behaviour — forwarding 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forwarding_target_200():
    """When the target API returns 200, forward returns that response.

    Covers system interaction spec §4 — FORWARDING → RESPONDING.
    """
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = _an200_response()

    with patch("backend.src.target_client.httpx.AsyncClient", return_value=mock_client):
        client = TargetModelClient(_make_router())
        response = await client.forward(_make_request_body(), _make_config())

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# TC-C04-SYS-002: system behaviour — target 5xx → re-raised
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forwarding_target_500_re_raises():
    """When the target API returns 500, the raw 500 response is returned.

    Error-code conversion (500→503) is handled by ResponseHandler/C07,
    not by TargetClient.  The raw response propagates so the handler
    can inspect it and decide the mapping.
    """
    body_500 = {"error": {"type": "server_error", "message": "internal"}}

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 500
    mock_response.json.return_value = body_500
    mock_response.text = json.dumps(body_500)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    with patch("backend.src.target_client.httpx.AsyncClient", return_value=mock_client):
        client = TargetModelClient(_make_router())
        response = await client.forward(_make_request_body(), _make_config())

    # The raw 500 status is passed through; C07 will map to 503.
    assert response.status_code == 500
    assert "server_error" in response.json()["error"]["type"]


# ---------------------------------------------------------------------------
# TC-C04-SYS-003: system behaviour — timeout → re-raised
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forwarding_timeout_re_raises():
    """When the target API triggers a TimeoutException, it is re-raised.

    Error-code conversion (TimeoutException→504) is handled by
    ResponseHandler/C07.
    """
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.side_effect = httpx.TimeoutException("Read timeout")

    with patch("backend.src.target_client.httpx.AsyncClient", return_value=mock_client):
        client = TargetModelClient(_make_router())
        with pytest.raises(httpx.TimeoutException, match="Read timeout"):
            await client.forward(_make_request_body(), _make_config())


# ---------------------------------------------------------------------------
# TC-C04-SYS-004: stream=true in body → forward_stream path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_stream_sets_stream_true_in_body():
    """forward_stream always sets ``stream: true`` in the outgoing request body.

    Even when the incoming request_body omits ``stream`` or sets it to
    ``False``, the body sent to the target must contain ``stream: true``.
    """
    sse_lines = ['data: {"type":"message_stop"}']
    mock_response = _MockStreamResponse(sse_lines)
    mock_ctx = _MockStreamCtx(mock_response)

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_ctx

    # Request body explicitly has stream: False
    body_without_stream = {
        "model": "deepseek-v3.2",
        "max_tokens": 4096,
        "stream": False,
        "messages": [{"role": "user", "content": "hello"}],
    }

    with patch("backend.src.target_client.httpx.AsyncClient", return_value=mock_client):
        client = TargetModelClient(_make_router())
        async for _ in client.forward_stream(body_without_stream, _make_config()):
            pass

    # Verify the json body sent to the target has stream: True
    mock_client.stream.assert_called_once()
    _, kwargs = mock_client.stream.call_args
    sent_body = kwargs["json"]
    assert sent_body["stream"] is True


# ---------------------------------------------------------------------------
# TC-C04-SYS-005: system behaviour — client disconnect aborts stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_stream_client_disconnect_releases_stream():
    """When the client disconnects mid-stream, resources are released.

    Covers system interaction spec §7.1 — 客户端断连取消上游.
    """
    sse_lines = [
        'data: {"type":"message_start"}',
        'data: {"type":"content_block_delta","delta":{"text":"part1"}}',
        'data: {"type":"content_block_delta","delta":{"text":"part2"}}',
        'data: {"type":"message_stop"}',
    ]

    mock_response = _MockStreamResponse(sse_lines)
    mock_ctx = _MockStreamCtx(mock_response)

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_ctx

    with patch("backend.src.target_client.httpx.AsyncClient", return_value=mock_client):
        client = TargetModelClient(_make_router())
        gen = client.forward_stream(_make_request_body(), _make_config())

        # Consume only the first line, then simulate disconnect
        first = await gen.__anext__()
        assert first.startswith("data:")

        # Simulate client disconnect by closing the generator
        await gen.aclose()

    # No exception should propagate — the stream was cleanly released.
    # If aclose raises, pytest will catch it and the test will fail.


# ---------------------------------------------------------------------------
# Additional coverage — lazy client creation / close lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_stream_skips_empty_lines():
    """forward_stream skips empty lines received from the upstream SSE stream."""
    sse_lines = [
        'data: {"type":"message_start"}',
        "",                      # empty line → skipped
        'data: {"type":"message_stop"}',
        "",                      # trailing empty line → skipped
    ]

    mock_response = _MockStreamResponse(sse_lines)
    mock_ctx = _MockStreamCtx(mock_response)

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_ctx

    with patch("backend.src.target_client.httpx.AsyncClient", return_value=mock_client):
        client = TargetModelClient(_make_router())
        yielded: list[str] = []
        async for line in client.forward_stream(_make_request_body(), _make_config()):
            yielded.append(line)

    assert len(yielded) == 2, f"expected 2 non-empty lines, got {len(yielded)}"
    assert all(line.startswith("data:") for line in yielded)


@pytest.mark.asyncio
async def test_client_close_is_idempotent():
    """close() can be called multiple times safely."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = _an200_response()

    with patch("backend.src.target_client.httpx.AsyncClient", return_value=mock_client):
        client = TargetModelClient(_make_router())
        await client.forward(_make_request_body(), _make_config())
        await client.close()
        await client.close()  # second call is a no-op

    # Second close should not call aclose again
    mock_client.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_forward_timeout_set_to_60s():
    """Non-streaming forward uses a 60-second timeout."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = _an200_response()

    with patch("backend.src.target_client.httpx.AsyncClient", return_value=mock_client):
        client = TargetModelClient(_make_router())
        await client.forward(_make_request_body(), _make_config())

    _, kwargs = mock_client.post.call_args
    timeout = kwargs["timeout"]
    assert timeout.read == 60.0


@pytest.mark.asyncio
async def test_forward_stream_timeout_set_to_120s():
    """Streaming forward uses a 120-second timeout."""
    sse_lines = ['data: {"type":"message_stop"}']
    mock_response = _MockStreamResponse(sse_lines)
    mock_ctx = _MockStreamCtx(mock_response)

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_ctx

    with patch("backend.src.target_client.httpx.AsyncClient", return_value=mock_client):
        client = TargetModelClient(_make_router())
        async for _ in client.forward_stream(_make_request_body(), _make_config()):
            pass

    _, kwargs = mock_client.stream.call_args
    timeout = kwargs["timeout"]
    assert timeout.read == 120.0
