"""
Test cases for response_handler.py — ResponseHandler (SSE + JSON).

Covers:
- TC-C05-BLD-001: response_handler.py compiles without syntax errors
- TC-C05-LOG-001: handle_non_stream returns JSONResponse with 200 + body
- TC-C05-LOG-002: handle_stream returns StreamingResponse (text/event-stream)
- TC-C05-LOG-003: StreamingResponse correctly forwards SSE events
- TC-C05-LOG-004: Anthropic SSE event types preserved
- TC-C05-LOG-005: non-stream Content-Type: application/json
- TC-C05-LOG-006: stream Content-Type: text/event-stream
- TC-C05-SYS-001: RESPONDING non-stream 200 with full body
- TC-C05-SYS-002: RESPONDING stream each chunk (delta + stop)
- TC-C05-SYS-003: identify phase — no SSE events sent
- TC-C05-SYS-004: target model inference — token-by-token forwarding
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import AsyncGenerator
from unittest.mock import MagicMock

import httpx
import pytest

from backend.src.response_handler import ResponseHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_httpx_response(status_code: int = 200, body: dict | None = None) -> MagicMock:
    """Create a mock httpx.Response with the given status and JSON body."""
    if body is None:
        body = {
            "id": "msg_01ABC123",
            "type": "message",
            "role": "assistant",
            "model": "deepseek-v3.2",
            "content": [{"type": "text", "text": "Hello! How can I help?"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 8},
        }
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


async def _make_sse_generator(lines: list[str]) -> AsyncGenerator[str, None]:
    """Yield each line as if it came from TargetClient.forward_stream()."""
    for line in lines:
        yield line


async def _consume_stream(streaming_response) -> list[str]:
    """Read all chunks from a StreamingResponse body iterator."""
    chunks: list[str] = []
    async for chunk in streaming_response.body_iterator:
        # chunk may be str or bytes depending on FastAPI internals
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8"))
        else:
            chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# TC-C05-BLD-001: Compile check
# ---------------------------------------------------------------------------


def test_compile_check():
    """TC-C05-BLD-001: response_handler.py compiles without syntax errors."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", "backend/src/response_handler.py"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Compile failed: {result.stderr}"


# ---------------------------------------------------------------------------
# TC-C05-LOG-001: handle_non_stream returns JSONResponse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_non_stream_returns_json_response():
    """TC-C05-LOG-001: handle_non_stream returns JSONResponse with correct body.

    Given: target_response status=200, body is Anthropic Message JSON.
    When: handle_non_stream(target_response, "anthropic")
    Then: returns JSONResponse, status=200, body does not contain image block.
    """
    handler = ResponseHandler()
    body = {
        "id": "msg_test_001",
        "type": "message",
        "role": "assistant",
        "model": "deepseek-v3.2",
        "content": [{"type": "text", "text": "The answer is 42."}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    target_resp = _make_httpx_response(status_code=200, body=body)

    result = await handler.handle_non_stream(target_resp, "anthropic")

    # It is a JSONResponse.
    from fastapi.responses import JSONResponse

    assert isinstance(result, JSONResponse)
    assert result.status_code == 200

    # Body must be the JSON dict (FastAPI renders it).
    # JSONResponse stores content as the raw Python object.
    assert result.body is not None

    # Decode body to verify it matches.
    decoded = json.loads(result.body)
    assert decoded == body
    assert "image" not in str(decoded.get("content", ""))


# ---------------------------------------------------------------------------
# TC-C05-LOG-002: handle_stream returns StreamingResponse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_stream_returns_streaming_response():
    """TC-C05-LOG-002: handle_stream returns StreamingResponse with SSE media type.

    Given: target_stream yields 3 SSE chunks.
    When: handle_stream(target_stream, "anthropic")
    Then: returns StreamingResponse, media_type="text/event-stream".
    """
    handler = ResponseHandler()
    sse_lines = ["data: chunk1", "data: chunk2", "data: [DONE]"]
    gen = _make_sse_generator(sse_lines)

    result = handler.handle_stream(gen, "anthropic")

    from fastapi.responses import StreamingResponse

    assert isinstance(result, StreamingResponse)
    assert result.media_type == "text/event-stream"


# ---------------------------------------------------------------------------
# TC-C05-LOG-003: StreamingResponse correctly forwards SSE events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_response_forwards_sse_events():
    """TC-C05-LOG-003: StreamingResponse correctly forwards SSE events.

    Given: target_stream = ["data: chunk1", "data: chunk2", "data: [DONE]"]
    When: handle_stream
    Then: client-received content matches target_stream.
    """
    handler = ResponseHandler()
    sse_lines = ["data: chunk1", "data: chunk2", "data: [DONE]"]
    gen = _make_sse_generator(sse_lines)

    result = handler.handle_stream(gen, "anthropic")

    chunks = await _consume_stream(result)
    assert chunks == sse_lines


# ---------------------------------------------------------------------------
# TC-C05-LOG-004: Anthropic format SSE event types preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_sse_event_types_preserved():
    """TC-C05-LOG-004: Anthropic SSE event types remain unchanged.

    Given: target_stream contains content_block_delta, message_delta, etc.
    When: streaming forward
    Then: SSE event types preserve Anthropic format (message_start,
          content_block_start, content_block_delta, content_block_stop,
          message_delta, message_stop).
    """
    handler = ResponseHandler()
    # Realistic Anthropic SSE events (each line starts with "data: ").
    sse_lines = [
        "data: {\"type\": \"message_start\", \"message\": {\"id\": \"msg_1\"}}",
        "data: {\"type\": \"content_block_start\", \"index\": 0}",
        "data: {\"type\": \"content_block_delta\", \"delta\": {\"type\": \"text_delta\", \"text\": \"Hello\"}}",
        "data: {\"type\": \"content_block_delta\", \"delta\": {\"type\": \"text_delta\", \"text\": \" world\"}}",
        "data: {\"type\": \"content_block_stop\", \"index\": 0}",
        "data: {\"type\": \"message_delta\", \"delta\": {\"stop_reason\": \"end_turn\"}}",
        "data: {\"type\": \"message_stop\"}",
    ]
    gen = _make_sse_generator(sse_lines)

    result = handler.handle_stream(gen, "anthropic")
    chunks = await _consume_stream(result)

    expected_types = [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    actual_types = []
    for chunk in chunks:
        # Each chunk is "data: {json...}", extract the type field.
        json_str = chunk[len("data: "):]
        evt = json.loads(json_str)
        actual_types.append(evt["type"])

    assert actual_types == expected_types


# ---------------------------------------------------------------------------
# TC-C05-LOG-005: Non-stream Content-Type is application/json
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_stream_content_type_json():
    """TC-C05-LOG-005: Non-stream response has Content-Type: application/json.

    Given: non-streaming request.
    When: handle_non_stream
    Then: response header Content-Type: application/json.
    """
    handler = ResponseHandler()
    body = {"result": "ok"}
    target_resp = _make_httpx_response(status_code=200, body=body)

    result = await handler.handle_non_stream(target_resp, "anthropic")

    # FastAPI JSONResponse sets a media_type string attribute and the
    # Content-Type header when rendered.
    assert result.media_type == "application/json"
    # The headers dict also contains it (JSONResponse sets it).
    assert result.headers.get("Content-Type") == "application/json"


# ---------------------------------------------------------------------------
# TC-C05-LOG-006: Stream Content-Type is text/event-stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_content_type_event_stream():
    """TC-C05-LOG-006: Stream response has Content-Type: text/event-stream.

    Given: streaming request.
    When: handle_stream
    Then: response header Content-Type: text/event-stream.
    """
    handler = ResponseHandler()
    gen = _make_sse_generator(["data: hello"])

    result = handler.handle_stream(gen, "anthropic")

    assert result.media_type == "text/event-stream"


# ---------------------------------------------------------------------------
# TC-C05-SYS-001: RESPONDING non-stream 200 with full body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sys_non_stream_200_full_body():
    """TC-C05-SYS-001: Non-stream 200 returns complete body.

    Source: system-interaction-spec.md §4 — RESPONDING.

    Given: target response 200 + normal body.
    When: handle_non_stream
    Then: returns 200, body complete.
    PASS: status 200 + body parseable.
    FAIL: body empty or truncated.
    """
    handler = ResponseHandler()
    body = {
        "id": "msg_full_001",
        "type": "message",
        "role": "assistant",
        "model": "deepseek-v3.2",
        "content": [
            {"type": "text", "text": "A" * 100},  # Non-trivial body
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 4, "output_tokens": 100},
    }
    target_resp = _make_httpx_response(status_code=200, body=body)

    result = await handler.handle_non_stream(target_resp, "anthropic")

    assert result.status_code == 200

    # Verify body is complete (parseable and matches).
    decoded = json.loads(result.body)
    assert decoded == body
    assert len(decoded["content"][0]["text"]) == 100  # Not truncated


# ---------------------------------------------------------------------------
# TC-C05-SYS-002: RESPONDING stream each chunk (delta + stop)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sys_stream_each_chunk_delta_and_stop():
    """TC-C05-SYS-002: Streaming forwards each chunk (delta + stop).

    Source: system-interaction-spec.md §4 — RESPONDING.

    Given: target stream yields tokens one by one.
    When: handle_stream
    Then: client receives tokens gradually via SSE events.
    PASS: at least 1 content_block_delta + 1 message_stop.
    FAIL: only message_stop, no delta.
    """
    handler = ResponseHandler()
    sse_lines = [
        "data: {\"type\": \"message_start\", \"message\": {\"id\": \"msg_sys2\"}}",
        "data: {\"type\": \"content_block_start\", \"index\": 0}",
        "data: {\"type\": \"content_block_delta\", \"delta\": {\"type\": \"text_delta\", \"text\": \"T\"}}",
        "data: {\"type\": \"content_block_delta\", \"delta\": {\"type\": \"text_delta\", \"text\": \"e\"}}",
        "data: {\"type\": \"content_block_delta\", \"delta\": {\"type\": \"text_delta\", \"text\": \"s\"}}",
        "data: {\"type\": \"content_block_delta\", \"delta\": {\"type\": \"text_delta\", \"text\": \"t\"}}",
        "data: {\"type\": \"content_block_stop\", \"index\": 0}",
        "data: {\"type\": \"message_delta\", \"delta\": {\"stop_reason\": \"end_turn\"}}",
        "data: {\"type\": \"message_stop\"}",
    ]
    gen = _make_sse_generator(sse_lines)

    result = handler.handle_stream(gen, "anthropic")
    chunks = await _consume_stream(result)

    types = []
    for chunk in chunks:
        json_str = chunk[len("data: "):]
        evt = json.loads(json_str)
        types.append(evt["type"])

    # PASS criterion: at least 1 content_block_delta + 1 message_stop
    assert "content_block_delta" in types
    assert "message_stop" in types


# ---------------------------------------------------------------------------
# TC-C05-SYS-003: identify phase — no SSE events sent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sys_identify_phase_no_sse_events():
    """TC-C05-SYS-003: During identify phase, no SSE events are emitted.

    Source: system-interaction-spec.md §7.3 — loading state — identify in progress.

    Given: pipeline is identifying images (stage 3).
    When: check if any SSE events are emitted.
    Then: no SSE events, connection stays alive.
    PASS: identify phase yields no SSE output, connection not broken.
    FAIL: events sent during identify.
    """
    handler = ResponseHandler()
    # During identify phase the generator yields nothing (empty).
    gen = _make_sse_generator([])

    result = handler.handle_stream(gen, "anthropic")
    chunks = await _consume_stream(result)

    # No SSE events at all during identify.
    assert chunks == []


# ---------------------------------------------------------------------------
# TC-C05-SYS-004: target model inference — token-by-token forwarding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sys_target_inference_token_by_token_forwarding():
    """TC-C05-SYS-004: Target model inference forwards tokens one by one.

    Source: system-interaction-spec.md §7.3 — loading state — target model inference.

    Given: target stream produces tokens incrementally.
    When: handle_stream
    Then: client receives each token incrementally in real time.
    PASS: client receives incremental text_delta.
    FAIL: all tokens arrive at once (non-streaming).
    """
    handler = ResponseHandler()
    # Simulate token-by-token streaming — each delta is one token.
    tokens = ["Hello", ",", " ", "world", "!"]
    sse_lines = []
    for tok in tokens:
        sse_lines.append(
            "data: {\"type\": \"content_block_delta\", "
            "\"delta\": {\"type\": \"text_delta\", \"text\": \"" + tok + "\"}}"
        )
    sse_lines.append("data: {\"type\": \"message_stop\"}")
    gen = _make_sse_generator(sse_lines)

    result = handler.handle_stream(gen, "anthropic")
    chunks = await _consume_stream(result)

    # Each chunk is a separate SSE event — tokens arrive individually.
    delta_count = 0
    texts: list[str] = []
    for chunk in chunks:
        json_str = chunk[len("data: "):]
        evt = json.loads(json_str)
        if evt.get("type") == "content_block_delta":
            delta_count += 1
            texts.append(evt["delta"]["text"])

    assert delta_count == len(tokens)
    assert texts == tokens
    # Verify tokens did NOT arrive all at once (each is a separate chunk).
    assert len(chunks) == len(tokens) + 1  # +1 for message_stop
    # Last event is message_stop
    last_json = chunks[-1][len("data: "):]
    assert json.loads(last_json)["type"] == "message_stop"


# ---------------------------------------------------------------------------
# Additional coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_non_stream_preserves_target_status_code():
    """Non-200 target status codes are passed through (C07 will handle error mapping)."""
    handler = ResponseHandler()
    body = {"error": "something went wrong"}
    target_resp = _make_httpx_response(status_code=500, body=body)

    result = await handler.handle_non_stream(target_resp, "anthropic")

    assert result.status_code == 500
    assert json.loads(result.body) == body


@pytest.mark.asyncio
async def test_handle_non_stream_openai_format():
    """OpenAI format body is also returned as JSONResponse."""
    handler = ResponseHandler()
    body = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }
        ],
    }
    target_resp = _make_httpx_response(status_code=200, body=body)

    result = await handler.handle_non_stream(target_resp, "openai")

    assert result.status_code == 200
    assert json.loads(result.body) == body


@pytest.mark.asyncio
async def test_handle_stream_empty_generator():
    """Empty SSE generator results in empty StreamingResponse."""
    handler = ResponseHandler()
    gen = _make_sse_generator([])

    result = handler.handle_stream(gen, "anthropic")
    chunks = await _consume_stream(result)

    assert chunks == []
    assert result.media_type == "text/event-stream"


@pytest.mark.asyncio
async def test_response_handler_is_stateless():
    """ResponseHandler can be reused across multiple invocations."""
    handler = ResponseHandler()

    # First call — non-streaming.
    r1 = await handler.handle_non_stream(
        _make_httpx_response(200, {"a": 1}), "anthropic"
    )
    assert r1.status_code == 200

    # Second call — streaming.
    gen = _make_sse_generator(["data: ping"])
    r2 = handler.handle_stream(gen, "anthropic")
    assert r2.media_type == "text/event-stream"

    # Third call — non-streaming again (no side effects from prior calls).
    r3 = await handler.handle_non_stream(
        _make_httpx_response(200, {"b": 2}), "anthropic"
    )
    assert json.loads(r3.body) == {"b": 2}
