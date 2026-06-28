"""Unit / logic tests for the Format Detector.

Coverage:
- TC-B01-LOG-001 … 008: detect_format + parse_anthropic_request + parse_openai_request
"""

from __future__ import annotations

import base64
import json

import pytest

from backend.src.format_detector import (
    detect_format,
    parse_anthropic_request,
    parse_openai_request,
)
from backend.src.models import ImageBlock, TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock


# ---------------------------------------------------------------------------
# TC-B01-LOG-001: recognise Anthropic endpoint
# ---------------------------------------------------------------------------


def test_detect_format_anthropic():
    """detect_format returns ``"anthropic"`` for /v1/messages."""
    assert detect_format("/v1/messages") == "anthropic"
    assert detect_format("/v1/messages?foo=bar") == "anthropic"
    assert detect_format("/api/v1/messages") == "anthropic"


# ---------------------------------------------------------------------------
# TC-B01-LOG-002: recognise OpenAI endpoint
# ---------------------------------------------------------------------------


def test_detect_format_openai():
    """detect_format returns ``"openai"`` for /v1/chat/completions."""
    assert detect_format("/v1/chat/completions") == "openai"
    assert detect_format("/v1/chat/completions/") == "openai"


# ---------------------------------------------------------------------------
# TC-B01-LOG-003: parse anthropic plain-text request
# ---------------------------------------------------------------------------


def test_parse_anthropic_plain_text():
    body = {
        "model": "claude",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hello"}],
    }
    req = parse_anthropic_request(body)

    assert req.source_format == "anthropic"
    assert req.target_model == "claude"
    assert req.max_tokens == 100
    assert len(req.messages) == 1
    assert req.messages[0].role == "user"
    assert len(req.messages[0].content) == 1
    assert isinstance(req.messages[0].content[0], TextBlock)
    assert req.messages[0].content[0].text == "hello"


# ---------------------------------------------------------------------------
# TC-B01-LOG-004: parse anthropic multi-block (text + image)
# ---------------------------------------------------------------------------

_SAMPLE_B64 = base64.b64encode(b"fake-image-data").decode()


def test_parse_anthropic_text_and_image():
    body = {
        "model": "claude",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this image"},
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
    req = parse_anthropic_request(body)

    assert req.source_format == "anthropic"
    content = req.messages[0].content
    assert len(content) == 2
    assert isinstance(content[0], TextBlock)
    assert content[0].text == "describe this image"
    assert isinstance(content[1], ImageBlock)
    assert content[1].source_type == "base64"
    assert content[1].media_type == "image/png"
    assert content[1].image_data == b"fake-image-data"
    assert content[1].message_index == 0
    assert content[1].block_index == 1


# ---------------------------------------------------------------------------
# TC-B01-LOG-005: parse anthropic system prompt
# ---------------------------------------------------------------------------


def test_parse_anthropic_system_prompt():
    body = {
        "model": "claude",
        "max_tokens": 100,
        "system": "You are helpful",
        "messages": [{"role": "user", "content": "hello"}],
    }
    req = parse_anthropic_request(body)
    assert req.system == "You are helpful"


# ---------------------------------------------------------------------------
# TC-B01-LOG-006: parse openai plain-text request
# ---------------------------------------------------------------------------


def test_parse_openai_plain_text():
    body = {
        "model": "gpt",
        "messages": [{"role": "user", "content": "hello"}],
    }
    req = parse_openai_request(body)

    assert req.source_format == "openai"
    assert req.target_model == "gpt"
    assert len(req.messages) == 1
    assert req.messages[0].role == "user"
    assert len(req.messages[0].content) == 1
    assert isinstance(req.messages[0].content[0], TextBlock)
    assert req.messages[0].content[0].text == "hello"
    assert req.system is None


# ---------------------------------------------------------------------------
# TC-B01-LOG-007: parse openai multi-part (text + image_url)
# ---------------------------------------------------------------------------


def test_parse_openai_text_and_image_url():
    body = {
        "model": "gpt",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this image"},
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
    req = parse_openai_request(body)

    assert req.source_format == "openai"
    content = req.messages[0].content
    assert len(content) == 2
    assert isinstance(content[0], TextBlock)
    assert content[0].text == "describe this image"
    assert isinstance(content[1], ImageBlock)
    assert content[1].source_type == "data_uri"
    assert content[1].media_type == "image/png"
    assert content[1].image_data == b"fake-image-data"
    assert content[1].message_index == 0
    assert content[1].block_index == 1


# ---------------------------------------------------------------------------
# TC-B01-LOG-008: parse stream parameter
# ---------------------------------------------------------------------------


def test_parse_anthropic_stream_true():
    body = {
        "model": "claude",
        "max_tokens": 100,
        "stream": True,
        "messages": [{"role": "user", "content": "hello"}],
    }
    req = parse_anthropic_request(body)
    assert req.stream is True


def test_parse_anthropic_stream_default_false():
    body = {
        "model": "claude",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hello"}],
    }
    req = parse_anthropic_request(body)
    assert req.stream is False


# ---------------------------------------------------------------------------
# Additional coverage — edge cases
# ---------------------------------------------------------------------------


def test_parse_anthropic_content_string():
    """messages[].content is a plain string (not a list)."""
    body = {
        "model": "claude",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "plain string"}],
    }
    req = parse_anthropic_request(body)
    assert len(req.messages[0].content) == 1
    assert isinstance(req.messages[0].content[0], TextBlock)
    assert req.messages[0].content[0].text == "plain string"


def test_parse_anthropic_url_image():
    """Anthropic image block with source.type == \"url\"."""
    body = {
        "model": "claude",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": "https://example.com/cat.png",
                        },
                    },
                ],
            }
        ],
    }
    req = parse_anthropic_request(body)
    content = req.messages[0].content
    assert len(content) == 1
    assert isinstance(content[0], ImageBlock)
    assert content[0].source_type == "url"
    assert content[0].image_data == b""  # URL images are downloaded later


def test_parse_openai_url_image():
    """OpenAI image_url with a plain HTTPS URL (not a data URI)."""
    body = {
        "model": "gpt",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/cat.png"},
                    },
                ],
            }
        ],
    }
    req = parse_openai_request(body)
    content = req.messages[0].content
    assert len(content) == 1
    assert isinstance(content[0], ImageBlock)
    assert content[0].source_type == "url"
    assert content[0].image_data == b""


def test_parse_anthropic_tool_use():
    """tool_use blocks are parsed as ToolUseBlock."""
    body = {
        "model": "claude",
        "max_tokens": 100,
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_001",
                        "name": "get_weather",
                        "input": {"city": "Beijing"},
                    },
                ],
            },
        ],
    }
    req = parse_anthropic_request(body)
    content = req.messages[0].content
    assert len(content) == 1
    assert isinstance(content[0], ToolUseBlock)
    assert content[0].id == "toolu_001"
    assert content[0].name == "get_weather"
    assert content[0].input == {"city": "Beijing"}


def test_parse_anthropic_tool_result():
    """tool_result blocks are parsed as ToolResultBlock."""
    body = {
        "model": "claude",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_001",
                        "content": "Sunny, 25C",
                    },
                ],
            },
        ],
    }
    req = parse_anthropic_request(body)
    content = req.messages[0].content
    assert len(content) == 1
    assert isinstance(content[0], ToolResultBlock)
    assert content[0].tool_use_id == "toolu_001"
    assert len(content[0].content) == 1
    assert isinstance(content[0].content[0], TextBlock)
    assert content[0].content[0].text == "Sunny, 25C"


def test_parse_openai_stream_true():
    body = {
        "model": "gpt",
        "stream": True,
        "messages": [{"role": "user", "content": "hello"}],
    }
    req = parse_openai_request(body)
    assert req.stream is True


def test_parse_anthropic_original_body_preserved():
    """original_body matches the input dict exactly."""
    body = {
        "model": "claude",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hello"}],
    }
    req = parse_anthropic_request(body)
    assert req.original_body == body


def test_parse_anthropic_default_max_tokens():
    """max_tokens defaults to 4096 when missing."""
    body = {
        "model": "claude",
        "messages": [{"role": "user", "content": "hello"}],
    }
    req = parse_anthropic_request(body)
    assert req.max_tokens == 4096


def test_detect_format_unknown_path_raises():
    """detect_format raises ValueError for unknown paths."""
    with pytest.raises(ValueError):
        detect_format("/v1/completions")
    with pytest.raises(ValueError):
        detect_format("/unknown")


def test_parse_anthropic_invalid_base64_graceful():
    """Non-base64 image data is handled gracefully (empty bytes)."""
    body = {
        "model": "claude",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "!!!not-valid-base64!!!",
                        },
                    },
                ],
            }
        ],
    }
    req = parse_anthropic_request(body)
    content = req.messages[0].content
    assert isinstance(content[0], ImageBlock)
    assert content[0].image_data == b""


def test_parse_anthropic_multiple_messages():
    """Multiple messages are each parsed independently."""
    body = {
        "model": "claude",
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
            {"role": "user", "content": "third"},
        ],
    }
    req = parse_anthropic_request(body)
    assert len(req.messages) == 3
    assert req.messages[0].role == "user"
    assert req.messages[0].content[0].text == "first"
    assert req.messages[1].role == "assistant"
    assert req.messages[1].content[0].text == "second"
    assert req.messages[2].role == "user"
    assert req.messages[2].content[0].text == "third"
