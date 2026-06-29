"""
Test cases for C02 — Vision Client (vision_client.py).

Covers:
- TC-C02-BLD-001: Compile check
- TC-C02-LOG-001: recognize sends correct API request
- TC-C02-LOG-002: recognize returns non-empty string on 200
- TC-C02-LOG-003: image_data correctly base64-encoded in request
- TC-C02-LOG-004: recognize_batch parallel (3 images × 100 ms < 150 ms)
- TC-C02-LOG-005: recognize_batch single failure does not block others
- TC-C02-LOG-006: timeout returns fallback text
- TC-C02-SYS-001: RECOGNIZING API success
- TC-C02-SYS-002: RECOGNIZING API failure degradation
- TC-C02-SYS-003: 429 retry success
- TC-C02-SYS-004: 429 retry still fails
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from backend.src.core.config import ProxyConfig
from backend.src.core.models import ImageBlock


# ============================================================================
# Helper factories
# ============================================================================

def _config(**overrides) -> ProxyConfig:
    """Build a ProxyConfig with test-friendly defaults."""
    defaults = {
        "vision_api_key": "sk-test-key",
        "vision_base_url": "https://coding.dashscope.aliyuncs.com",
        "vision_model": "qwen3.7-plus",
        "vision_timeout": 30,
    }
    defaults.update(overrides)
    return ProxyConfig(**defaults)


def _image_block(
    image_data: bytes = b"test-image-bytes",
    media_type: str = "image/png",
) -> ImageBlock:
    """Build an ImageBlock with the given payload."""
    return ImageBlock(image_data=image_data, media_type=media_type)


def _make_mock_response(status_code: int = 200, content: str = "一张测试图片的描述") -> AsyncMock:
    """Create a mock httpx.Response with the given status and content."""
    resp = AsyncMock()
    resp.status_code = status_code
    resp.json = lambda: {"choices": [{"message": {"content": content}}]}
    return resp


def _setup_httpx_mock(mock_client_cls, responses: list[AsyncMock]) -> AsyncMock:
    """Wire up a mock httpx.AsyncClient that returns the given responses in order.

    Returns the mock client so the test can inspect ``.post.call_args_list`` etc.
    """
    mock_client = mock_client_cls.return_value
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    if len(responses) == 1:
        mock_client.post = AsyncMock(return_value=responses[0])
    else:
        mock_client.post = AsyncMock(side_effect=responses)
    return mock_client


# ============================================================================
# TC-C02-BLD-001: Compile check
# ============================================================================

def test_compile_check():
    """vision_client.py compiles without syntax errors."""
    import backend.src.pipeline.vision_client  # noqa: F401


# ============================================================================
# TC-C02-LOG-001: recognize sends correct API request
# ============================================================================

@pytest.mark.asyncio
async def test_recognize_sends_correct_request():
    """recognize() POSTs to the correct URL with correct model and image_url."""
    config = _config()
    image = _image_block(b"hello-world", "image/png")

    mock_resp = _make_mock_response(200, "描述文字")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = _setup_httpx_mock(mock_client_cls, [mock_resp])

        from backend.src.pipeline.vision_client import QwenVisionClient

        client = QwenVisionClient(config)
        result = await client.recognize(image)

    # Verify return value.
    assert result == "描述文字"

    # Verify the request was sent to the correct URL.
    call_args = mock_client.post.call_args
    url = call_args[0][0]  # first positional arg
    assert url == "https://coding.dashscope.aliyuncs.com/v1/chat/completions"

    # Verify the JSON payload.
    payload = call_args[1]["json"]
    assert payload["model"] == "qwen3.7-plus"
    assert payload["max_tokens"] == 1500

    # Verify the content contains an image_url block.
    messages = payload["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert len(content) == 2

    # First content block should be image_url.
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")

    # Second content block should be text.
    assert content[1]["type"] == "text"
    assert len(content[1]["text"]) > 0

    # Verify headers.
    headers = call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer sk-test-key"


# ============================================================================
# Prompt constraint is prepended (prevents model drift into codegen)
# ============================================================================

@pytest.mark.asyncio
async def test_prompt_has_task_constraint():
    """The vision prompt is prefixed with the role/limitation constraint.

    Without it, kimi-k2.5 drifts into generating HTML/code instead of an
    observation report, burning the token budget and stalling for 100s+.
    """
    config = _config()
    image = _image_block()

    mock_resp = _make_mock_response(200, "ok")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = _setup_httpx_mock(mock_client_cls, [mock_resp])

        from backend.src.pipeline.vision_client import QwenVisionClient, _TASK_CONSTRAINT

        client = QwenVisionClient(config)
        await client.recognize(image, focus_prompt="检查代码高亮")

    text_block = mock_client.post.call_args[1]["json"]["messages"][0]["content"][1]["text"]
    assert text_block.startswith(_TASK_CONSTRAINT)
    assert "检查代码高亮" in text_block


# ============================================================================
# TC-C02-LOG-002: recognize returns non-empty string on 200
# ============================================================================

@pytest.mark.asyncio
async def test_recognize_returns_non_empty_string():
    """recognize() returns a non-empty string when the API responds 200."""
    config = _config()
    image = _image_block()

    mock_resp = _make_mock_response(200, "这是一张包含文字和图形的截图")

    with patch("httpx.AsyncClient") as mock_client_cls:
        _setup_httpx_mock(mock_client_cls, [mock_resp])

        from backend.src.pipeline.vision_client import QwenVisionClient

        client = QwenVisionClient(config)
        result = await client.recognize(image)

    assert isinstance(result, str)
    assert len(result) > 0


# ============================================================================
# TC-C02-LOG-003: image_data correctly base64-encoded
# ============================================================================

@pytest.mark.asyncio
async def test_image_data_base64_encoded_correctly():
    """image_data is transmitted as a valid data URI with correct base64."""
    config = _config()
    image = ImageBlock(image_data=b"test-image-bytes", media_type="image/png")

    mock_resp = _make_mock_response(200, "ok")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = _setup_httpx_mock(mock_client_cls, [mock_resp])

        from backend.src.pipeline.vision_client import QwenVisionClient

        client = QwenVisionClient(config)
        await client.recognize(image)

    payload = mock_client.post.call_args[1]["json"]
    image_url_str = payload["messages"][0]["content"][0]["image_url"]["url"]
    assert image_url_str == "data:image/png;base64,dGVzdC1pbWFnZS1ieXRlcw=="


# ============================================================================
# TC-C02-LOG-004: recognize_batch parallel (3 images × 100 ms < 150 ms)
# ============================================================================

@pytest.mark.asyncio
async def test_recognize_batch_parallel():
    """recognize_batch runs 3 recognitions in parallel (total < 150 ms)."""

    async def delayed_response(*args, **kwargs):
        await asyncio.sleep(0.1)
        return _make_mock_response(200, "ok")

    from backend.src.pipeline.vision_client import QwenVisionClient

    config = _config()
    client = QwenVisionClient(config)

    images = [_image_block(b"a"), _image_block(b"b"), _image_block(b"c")]

    # We need the mock .post to return an already-resolved response after a
    # short sleep.  We achieve this by making .post a coroutine that sleeps.
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        # When the client context manager is entered, it should start a new
        # "session" that can handle concurrent calls.  We can simply return
        # the same mock_client every time; httpx's real AsyncClient does not
        # serialize calls, so our mock must allow concurrent .post() calls too.
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=delayed_response)

        start = time.perf_counter()
        results = await client.recognize_batch(images)
        elapsed = time.perf_counter() - start

    assert len(results) == 3
    assert all(r == "ok" for r in results)
    # 3 concurrent calls, each 100 ms → total should be just over 100 ms,
    # not 300 ms.  We allow generous headroom (150 ms) for test overhead.
    assert elapsed < 0.150, f"parallel calls took {elapsed:.3f}s, expected < 0.150s"


# ============================================================================
# TC-C02-LOG-005: recognize_batch single failure does not block others
# ============================================================================

@pytest.mark.asyncio
async def test_recognize_batch_single_failure_does_not_block():
    """When one image fails (500), the other two succeed normally."""
    from backend.src.pipeline.vision_client import QwenVisionClient

    config = _config()
    client = QwenVisionClient(config)

    images = [_image_block(b"a"), _image_block(b"b"), _image_block(b"c")]

    resp_ok = _make_mock_response(200, "ok")
    resp_500 = _make_mock_response(500, "")

    with patch("httpx.AsyncClient") as mock_client_cls:
        # Use side_effect to deliver different responses per call.
        mock_client = mock_client_cls.return_value
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=[resp_ok, resp_500, resp_ok])

        results = await client.recognize_batch(images)

    assert len(results) == 3
    assert results[0] == "ok"
    assert results[1] == "[图片无法识别]"
    assert results[2] == "ok"


# ============================================================================
# TC-C02-LOG-006: timeout returns fallback text
# ============================================================================

@pytest.mark.asyncio
async def test_recognize_timeout_returns_fallback():
    """When the vision API times out, return '[图片无法识别]'."""
    config = _config(vision_timeout=1)  # 1 s timeout
    image = _image_block()

    from backend.src.pipeline.vision_client import QwenVisionClient

    client = QwenVisionClient(config)

    # Make the mock raise httpx.TimeoutException.
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        result = await client.recognize(image)

    assert result == "[图片无法识别]"


# ============================================================================
# TC-C02-SYS-001: RECOGNIZING API success
# ============================================================================

@pytest.mark.asyncio
async def test_sys_recognizing_api_success():
    """System behaviour §4: RECOGNIZING API success → text description."""
    config = _config()
    image = _image_block()
    expected = "图中显示了一个蓝色的按钮，上面写着'提交'"

    mock_resp = _make_mock_response(200, expected)

    with patch("httpx.AsyncClient") as mock_client_cls:
        _setup_httpx_mock(mock_client_cls, [mock_resp])

        from backend.src.pipeline.vision_client import QwenVisionClient

        client = QwenVisionClient(config)
        result = await client.recognize(image)

    assert isinstance(result, str)
    assert len(result) > 0
    assert result == expected


# ============================================================================
# TC-C02-SYS-002: RECOGNIZING API failure degradation
# ============================================================================

@pytest.mark.asyncio
async def test_sys_recognizing_api_failure_degradation():
    """System behaviour §4: non-200 → '[图片无法识别]', no exception."""
    config = _config()
    image = _image_block()

    # Test several failure status codes.
    for status in (400, 401, 403, 500, 502, 503):
        mock_resp = _make_mock_response(status, "")

        with patch("httpx.AsyncClient") as mock_client_cls:
            _setup_httpx_mock(mock_client_cls, [mock_resp])

            from backend.src.pipeline.vision_client import QwenVisionClient

            client = QwenVisionClient(config)
            result = await client.recognize(image)

        assert result == "[图片无法识别]", f"status {status} should degrade"
        # Must not raise.


# ============================================================================
# TC-C02-SYS-003: 429 retry success
# ============================================================================

@pytest.mark.asyncio
async def test_sys_429_retry_success():
    """First attempt 429 → wait 1s → second attempt 200 → success."""
    config = _config()
    image = _image_block()

    resp_429 = _make_mock_response(429, "")
    resp_200 = _make_mock_response(200, "retry succeeded")

    from backend.src.pipeline.vision_client import QwenVisionClient

    client = QwenVisionClient(config)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=[resp_429, resp_200])

        # Also patch asyncio.sleep to skip the real 1 s wait in tests.
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.recognize(image)

    assert result == "retry succeeded"
    # Confirm exactly 2 API calls were made.
    assert mock_client.post.call_count == 2
    # Confirm sleep was called once with ≈1 s.
    mock_sleep.assert_awaited_once_with(1.0)


# ============================================================================
# TC-C02-SYS-004: 429 retry still fails
# ============================================================================

@pytest.mark.asyncio
async def test_sys_429_retry_still_fails():
    """Both attempts return 429 → '[图片无法识别]', exactly 2 calls."""
    config = _config()
    image = _image_block()

    resp_429 = _make_mock_response(429, "")

    from backend.src.pipeline.vision_client import QwenVisionClient

    client = QwenVisionClient(config)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=resp_429)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.recognize(image)

    assert result == "[图片无法识别]"
    assert mock_client.post.call_count == 6  # 1 initial + 5 retries
    assert mock_sleep.await_count == 5


# ============================================================================
# Edge case: image_data is None
# ============================================================================

@pytest.mark.asyncio
async def test_recognize_with_none_image_data_returns_fallback():
    """When image_data is None, return fallback without making any API call."""
    config = _config()
    image = ImageBlock(image_data=None, media_type="image/png")

    from backend.src.pipeline.vision_client import QwenVisionClient

    client = QwenVisionClient(config)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        result = await client.recognize(image)

    assert result == "[图片无法识别]"
    # No API call should have been made.
    mock_client.post.assert_not_called()


# ============================================================================
# Edge case: recognize_batch with empty list
# ============================================================================

@pytest.mark.asyncio
async def test_recognize_batch_empty_list():
    """recognize_batch with an empty list returns an empty list."""
    config = _config()

    from backend.src.pipeline.vision_client import QwenVisionClient

    client = QwenVisionClient(config)
    results = await client.recognize_batch([])
    assert results == []


# ============================================================================
# Edge case: JSON decode error
# ============================================================================

@pytest.mark.asyncio
async def test_recognize_json_decode_error_returns_fallback():
    """Malformed JSON in response → '[图片无法识别]'."""
    config = _config()
    image = _image_block()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        bad_resp = AsyncMock()
        bad_resp.status_code = 200
        # httpx.Response.json() is synchronous, so use Mock (not AsyncMock)
        # for the side_effect so the exception is raised inline.
        bad_resp.json = Mock(side_effect=json.JSONDecodeError("bad", "d", 0))
        mock_client.post = AsyncMock(return_value=bad_resp)

        from backend.src.pipeline.vision_client import QwenVisionClient

        client = QwenVisionClient(config)
        result = await client.recognize(image)

    assert result == "[图片无法识别]"


# ============================================================================
# Edge case: RequestError (network failure)
# ============================================================================

@pytest.mark.asyncio
async def test_recognize_request_error_returns_fallback():
    """httpx.RequestError → '[图片无法识别]'."""
    config = _config()
    image = _image_block()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=httpx.RequestError("connection refused"))

        from backend.src.pipeline.vision_client import QwenVisionClient

        client = QwenVisionClient(config)
        result = await client.recognize(image)

    assert result == "[图片无法识别]"
