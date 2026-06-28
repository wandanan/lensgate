"""
Test cases for decision_engine.py — lightweight intent recognition.

Covers:
- Basic tool-calling response parsing
- Field validation (hashes, focus_prompt, mode)
- Cache entry input construction
- Retry on validation failure
- Fallback when all retries exhausted
- Valuation JSONL writing
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.decision_engine import DecisionEngine, DecisionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(api_key: str = "test-key") -> DecisionEngine:
    return DecisionEngine(
        api_key=api_key,
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        timeout=5,
    )


def _mock_tool_response(arguments: dict) -> dict:
    """Return a valid DeepSeek tool-calling API response."""
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "route_decision",
                                "arguments": '{"image_hashes":[],"focus_prompt":"","mode":"single","reasoning":"no images needed"}',
                            },
                        }
                    ],
                }
            }
        ],
    }


def _mock_tool_response_raw(args_str: str) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "route_decision",
                                "arguments": args_str,
                            },
                        }
                    ],
                }
            }
        ],
    }


# ---------------------------------------------------------------------------
# Parse tests
# ---------------------------------------------------------------------------


def test_parse_valid_single_decision():
    engine = _make_engine()
    h1, h2 = "a" * 64, "b" * 64
    raw = f'{{"image_hashes":["{h1}","{h2}"],"focus_prompt":"describe the chart","mode":"single","reasoning":"user asked about diagram"}}'
    result = engine._parse(raw)
    assert result.image_hashes == [h1, h2]
    assert result.focus_prompt == "describe the chart"
    assert result.mode == "single"
    assert "diagram" in result.reasoning


def test_parse_empty_hashes():
    engine = _make_engine()
    raw = '{"image_hashes":[],"focus_prompt":"","mode":"single","reasoning":"pure text"}'
    result = engine._parse(raw)
    assert result.image_hashes == []
    assert result.focus_prompt == ""
    assert result.mode == "single"


def test_parse_compare_mode():
    engine = _make_engine()
    h1, h2 = "a" * 64, "b" * 64
    raw = f'{{"image_hashes":["{h1}","{h2}"],"focus_prompt":"compare two charts","mode":"compare","reasoning":"comparison"}}'
    result = engine._parse(raw)
    assert result.mode == "compare"
    assert len(result.image_hashes) == 2


def test_parse_invalid_json_raises():
    engine = _make_engine()
    with pytest.raises(Exception, match="not valid JSON"):
        engine._parse("not json")


def test_parse_missing_required_fields_uses_defaults():
    engine = _make_engine()
    raw = '{}'
    result = engine._parse(raw)
    assert result.image_hashes == []
    assert result.focus_prompt == ""
    assert result.mode == "single"
    assert result.reasoning == ""


def test_parse_hashes_not_list_raises():
    engine = _make_engine()
    with pytest.raises(Exception, match="image_hashes must be an array"):
        engine._parse('{"image_hashes":"not_a_list","focus_prompt":"","mode":"single","reasoning":""}')


def test_parse_invalid_hash_format_raises():
    engine = _make_engine()
    with pytest.raises(Exception, match="must be 64-char hex"):
        engine._parse('{"image_hashes":["short"],"focus_prompt":"test prompt","mode":"single","reasoning":""}')


def test_parse_focus_too_short_raises():
    engine = _make_engine()
    h = "a" * 64
    with pytest.raises(Exception, match="too short"):
        engine._parse(f'{{"image_hashes":["{h}"],"focus_prompt":"ab","mode":"single","reasoning":""}}')


def test_parse_focus_too_long_raises():
    engine = _make_engine()
    h = "a" * 64
    with pytest.raises(Exception, match="too long"):
        engine._parse(f'{{"image_hashes":["{h}"],"focus_prompt":"{"x" * 250}","mode":"single","reasoning":""}}')


def test_parse_invalid_mode_raises():
    engine = _make_engine()
    with pytest.raises(Exception, match="mode must be"):
        engine._parse('{"image_hashes":[],"focus_prompt":"","mode":"invalid","reasoning":""}')


def test_parse_reasoning_not_string_coerced():
    engine = _make_engine()
    raw = '{"image_hashes":[],"focus_prompt":"","mode":"single","reasoning":42}'
    result = engine._parse(raw)
    assert result.reasoning == "42"


def test_parse_focus_whitespace_only_ignored():
    engine = _make_engine()
    raw = '{"image_hashes":[],"focus_prompt":"   ","mode":"single","reasoning":""}'
    result = engine._parse(raw)
    assert result.focus_prompt == ""


# ---------------------------------------------------------------------------
# Prompt builder tests
# ---------------------------------------------------------------------------


def test_build_prompt_no_cache():
    engine = _make_engine()
    prompt = engine._build_prompt(["hello"], [])
    assert "hello" in prompt
    assert "(无)" in prompt


def test_build_prompt_with_cache():
    engine = _make_engine()
    cached = [
        {
            "hash": "a" * 64,
            "file_name": "screenshot.png",
            "position": "1",
            "position_label": "第1张",
            "label": "a code editor",
            "summary": "a dark-themed code editor",
        }
    ]
    prompt = engine._build_prompt(["what is this?"], cached)
    assert "screenshot.png" in prompt
    assert "第1张" in prompt
    assert "a dark-themed code editor" in prompt


def test_build_prompt_with_new_images():
    engine = _make_engine()
    prompt = engine._build_prompt(["describe"], [], new_image_count=2)
    assert "新图片" in prompt
    assert "2 张" in prompt


# ---------------------------------------------------------------------------
# API call tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_model_success():
    engine = _make_engine()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _mock_tool_response({})

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await engine._call_model("test prompt")

    assert "image_hashes" in result


@pytest.mark.asyncio
async def test_call_model_no_tool_calls_raises():
    engine = _make_engine()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "no tool call"}}]
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(Exception, match="did not call route_decision"):
            await engine._call_model("test")


# ---------------------------------------------------------------------------
# Decide (integration) — mock the HTTP call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decide_pure_text_no_cache():
    engine = _make_engine()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _mock_tool_response({})

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await engine.decide(["hello"], [])

    assert result.mode == "single"
    assert result.image_hashes == []


@pytest.mark.asyncio
async def test_decide_retry_on_validation_error():
    engine = _make_engine()
    # First response has invalid hash, second is valid
    call_count = [0]

    def json_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            return _mock_tool_response_raw(
                '{"image_hashes":["bad_hash"],"focus_prompt":"test prompt","mode":"single","reasoning":""}'
            )
        else:
            return _mock_tool_response({})

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.side_effect = json_side_effect

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await engine.decide(["hello"], [], max_retries=1)

    # Second call succeeded with empty hashes
    assert result.image_hashes == []
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_decide_exhausted_retries_returns_default():
    engine = _make_engine()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _mock_tool_response_raw(
        '{"image_hashes":["bad"],"focus_prompt":"test prompt","mode":"single","reasoning":""}'
    )

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await engine.decide(["hello"], [], max_retries=1)

    assert result.reasoning.startswith("retries exhausted")


@pytest.mark.asyncio
async def test_decide_handles_network_error():
    engine = _make_engine()
    mock_client = AsyncMock()
    mock_client.post.side_effect = Exception("Connection refused")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await engine.decide(["hello"], [], max_retries=0)

    assert result.reasoning.startswith("retries exhausted")


# ---------------------------------------------------------------------------
# DecisionResult basics
# ---------------------------------------------------------------------------


def test_decision_result_defaults():
    d = DecisionResult()
    assert d.image_hashes == []
    assert d.focus_prompt == ""
    assert d.mode == "single"
    assert d.reasoning == ""


def test_decision_result_repr():
    d = DecisionResult(image_hashes=["a" * 64], focus_prompt="describe", mode="single")
    r = repr(d)
    assert "Decision(" in r
    assert "describe" in r
