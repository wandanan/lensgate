"""
Format Detector — detects API format and parses requests into canonical ProxyRequest.

Supports:
- Anthropic Messages API   (POST /v1/messages)
- OpenAI Chat Completions API (POST /v1/chat/completions)

The detector uses the request path to determine the source format and delegates
to the appropriate parser.  Each parser walks the messages/content structure,
extracting text blocks, image blocks (base64 / url / data_uri), tool_use,
tool_result, and thinking blocks into the internal canonical model.

Images are detected at both the top-level message content AND recursively
inside tool_result content blocks (Claude Code embeds images read by the
Read tool inside tool_result.content).
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from backend.src.models import (
    ImageBlock,
    Message,
    ProxyRequest,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DATA_URI_RE = re.compile(r"^data:(image/\w+);base64,(.+)$")


def detect_format(path: str, body: dict[str, Any] | None = None) -> str:
    """Detect the API format from the request path.

    Returns ``"anthropic"`` or ``"openai"``.

    Raises ValueError if the path does not match a known API endpoint.
    """
    clean = path.split("?")[0].rstrip("/")
    if clean.endswith("/v1/messages"):
        return "anthropic"
    if clean.endswith("/v1/chat/completions"):
        return "openai"
    raise ValueError(f"Unknown API endpoint path: {path}")


def parse_anthropic_request(body: dict[str, Any]) -> ProxyRequest:
    """Parse an Anthropic Messages API request body into a ProxyRequest.

    Handles content-block types: text, image, tool_use, tool_result, thinking.
    Images inside tool_result.content are recursively parsed.
    """
    model = body.get("model", "")
    max_tokens = body.get("max_tokens", 4096)
    stream = body.get("stream", False)
    # system is an array of content blocks in real Claude Code (not a string).
    system = body.get("system")

    messages = _parse_anthropic_messages(body.get("messages", []))

    return ProxyRequest(
        source_format="anthropic",
        target_model=model,
        messages=messages,
        stream=stream,
        max_tokens=max_tokens,
        system=system,
        original_body=body,
    )


def parse_openai_request(body: dict[str, Any]) -> ProxyRequest:
    """Parse an OpenAI Chat Completions API request body into a ProxyRequest."""
    model = body.get("model", "")
    max_tokens = body.get("max_tokens", 4096)
    stream = body.get("stream", False)

    messages = _parse_openai_messages(body.get("messages", []))

    return ProxyRequest(
        source_format="openai",
        target_model=model,
        messages=messages,
        stream=stream,
        max_tokens=max_tokens,
        system=None,
        original_body=body,
    )


# ---------------------------------------------------------------------------
# Anthropic content parsers
# ---------------------------------------------------------------------------


def _parse_anthropic_messages(raw_messages: list[dict[str, Any]]) -> list[Message]:
    """Parse the ``messages`` array of an Anthropic request."""
    result: list[Message] = []
    for msg_idx, raw_msg in enumerate(raw_messages):
        role = raw_msg.get("role", "user")
        content = raw_msg.get("content", "")
        blocks = _parse_anthropic_content(content, msg_idx)
        result.append(Message(role=role, content=blocks))
    return result


def _parse_anthropic_content(
    content: Any,
    msg_idx: int,
    parent_block_idx: int | None = None,
) -> list:
    """Parse a ``content`` field (string or list of blocks).

    When *parent_block_idx* is set the content belongs to a tool_result
    block — images discovered here carry that parent reference so the
    rewriter can navigate the nested structure correctly.
    """
    if isinstance(content, str):
        return [TextBlock(text=content)]

    if not isinstance(content, list):
        return [TextBlock(text=str(content))]

    blocks: list = []
    for block_idx, item in enumerate(content):
        if not isinstance(item, dict):
            blocks.append(TextBlock(text=str(item)))
            continue

        block_type = item.get("type", "")

        if block_type == "text":
            blocks.append(TextBlock(text=item.get("text", "")))

        elif block_type == "image":
            blocks.append(
                _build_anthropic_image_block(item, msg_idx, block_idx, parent_block_idx)
            )

        elif block_type == "tool_use":
            blocks.append(
                ToolUseBlock(
                    id=item.get("id", ""),
                    name=item.get("name", ""),
                    input=item.get("input", {}),
                )
            )

        elif block_type == "tool_result":
            # Recursively parse tool_result.content — images from Read tool
            # live here.
            nested = _parse_anthropic_content(
                item.get("content", []), msg_idx, block_idx
            )
            blocks.append(
                ToolResultBlock(
                    tool_use_id=item.get("tool_use_id", ""),
                    content=nested,
                    is_error=item.get("is_error", False),
                )
            )

        elif block_type == "thinking":
            blocks.append(
                ThinkingBlock(
                    thinking=item.get("thinking", ""),
                    signature=item.get("signature", ""),
                )
            )

        else:
            # Unknown block type — keep as text for safety.
            blocks.append(TextBlock(text=json.dumps(item, ensure_ascii=False)))

    return blocks


def _build_anthropic_image_block(
    item: dict[str, Any],
    msg_idx: int,
    block_idx: int,
    parent_block_idx: int | None = None,
) -> ImageBlock | TextBlock:
    """Build an ImageBlock (or fallback TextBlock) from an Anthropic image block."""
    source = item.get("source", {})
    source_type = source.get("type", "")

    if source_type == "base64":
        b64_data = source.get("data", "")
        media_type = source.get("media_type", "image/png")
        try:
            image_data = base64.b64decode(b64_data)
        except Exception:
            image_data = b""
        return ImageBlock(
            image_data=image_data,
            media_type=media_type,
            source_type="base64",
            source_data=b64_data,
            message_index=msg_idx,
            block_index=block_idx,
            parent_block_index=parent_block_idx,
        )

    if source_type == "url":
        url = source.get("url", "")
        media_type = source.get("media_type", "image/png")
        return ImageBlock(
            image_data=b"",
            media_type=media_type,
            source_type="url",
            source_data=url,
            message_index=msg_idx,
            block_index=block_idx,
            parent_block_index=parent_block_idx,
        )

    return TextBlock(text=f"[unsupported image source: {source_type}]")


# ---------------------------------------------------------------------------
# OpenAI content parsers
# ---------------------------------------------------------------------------


def _parse_openai_messages(raw_messages: list[dict[str, Any]]) -> list[Message]:
    """Parse the ``messages`` array of an OpenAI request."""
    result: list[Message] = []
    for msg_idx, raw_msg in enumerate(raw_messages):
        role = raw_msg.get("role", "user")
        content = raw_msg.get("content", "")
        blocks = _parse_openai_content(content, msg_idx)
        result.append(Message(role=role, content=blocks))
    return result


def _parse_openai_content(content: Any, msg_idx: int) -> list:
    """Parse a single message's ``content`` field (string or list of parts)."""
    if isinstance(content, str):
        return [TextBlock(text=content)]

    if not isinstance(content, list):
        return [TextBlock(text=str(content))]

    blocks: list = []
    for block_idx, item in enumerate(content):
        if not isinstance(item, dict):
            blocks.append(TextBlock(text=str(item)))
            continue

        block_type = item.get("type", "")

        if block_type == "text":
            blocks.append(TextBlock(text=item.get("text", "")))

        elif block_type == "image_url":
            blocks.append(_build_openai_image_block(item, msg_idx, block_idx))

        else:
            blocks.append(TextBlock(text=json.dumps(item, ensure_ascii=False)))

    return blocks


def _build_openai_image_block(
    item: dict[str, Any], msg_idx: int, block_idx: int
) -> ImageBlock | TextBlock:
    """Build an ImageBlock (or fallback TextBlock) from an OpenAI image_url part."""
    image_url_obj = item.get("image_url", {})
    url = image_url_obj.get("url", "")

    m = _DATA_URI_RE.match(url)
    if m:
        media_type = m.group(1)
        b64_data = m.group(2)
        try:
            image_data = base64.b64decode(b64_data)
        except Exception:
            image_data = b""
        return ImageBlock(
            image_data=image_data,
            media_type=media_type,
            source_type="data_uri",
            source_data=url,
            message_index=msg_idx,
            block_index=block_idx,
        )

    if url:
        return ImageBlock(
            image_data=b"",
            media_type="image/png",
            source_type="url",
            source_data=url,
            message_index=msg_idx,
            block_index=block_idx,
        )

    return TextBlock(text="[image_url with empty url]")
