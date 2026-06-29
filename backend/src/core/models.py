"""
Data models for the multimodal proxy gateway.

Defines the internal canonical representation for proxy requests,
messages, and content blocks. These models decouple the proxy logic
from the external API formats (Anthropic / OpenAI).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union


# ---------------------------------------------------------------------------
# Content blocks
# ---------------------------------------------------------------------------


@dataclass
class TextBlock:
    """A plain-text content block."""

    text: str


@dataclass
class ImageBlock:
    """An image content block extracted from the incoming request.

    Attributes:
        image_data: Raw image bytes.  None when is_error is True.
        media_type: MIME type, e.g. ``"image/png"``.
        source_type: How the image was provided — ``"base64"``, ``"url"``, or ``"data_uri"``.
        source_data: Raw source reference (base64 string, URL, or full data URI).
        message_index: Position of the parent message in the messages list.
        block_index: Position of this block within the content list (top-level or
                     inside a ``tool_result`` content).
        parent_block_index: If the image is nested inside a ``tool_result`` block,
                            the index of that tool_result within the message's
                            content list.  ``None`` when at message content top-level.
        is_error: True when the image data could not be resolved.
    """

    image_data: bytes | None = None
    media_type: str = ""
    source_type: str = ""
    source_data: str = ""
    message_index: int = -1
    block_index: int = -1
    parent_block_index: int | None = None
    is_error: bool = False


@dataclass
class ToolUseBlock:
    """A tool-use request from the assistant."""

    id: str
    name: str
    input: dict = field(default_factory=dict)


@dataclass
class ToolResultBlock:
    """A tool-result returned to the assistant.

    The nested ``content`` list may contain ``ImageBlock`` entries when
    Claude Code reads an image file (e.g. via the Read tool).
    """

    tool_use_id: str
    content: list[ContentBlock] = field(default_factory=list)
    is_error: bool = False


@dataclass
class ThinkingBlock:
    """Extended thinking content from the assistant."""

    thinking: str
    signature: str


# Forward-reference types are resolved lazily thanks to
# ``from __future__ import annotations`` at the top of the module.
ContentBlock = Union[TextBlock, ImageBlock, ToolUseBlock, ToolResultBlock, ThinkingBlock]


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


@dataclass
class Message:
    """A single message in a conversation turn.

    Attributes:
        role: One of ``"user"``, ``"assistant"``, or ``"system"``.
        content: Ordered list of ContentBlock.
    """

    role: Literal["user", "assistant", "system"]
    content: list[ContentBlock]


# ---------------------------------------------------------------------------
# Proxy request (internal canonical form)
# ---------------------------------------------------------------------------


@dataclass
class ProxyRequest:
    """Internal canonical representation of an incoming proxy request.

    Created by the format-specific parsers (Anthropic / OpenAI) and consumed
    by the proxy pipeline (image detection → vision recognition → forwarding).

    Attributes:
        source_format: The original API format — ``"anthropic"`` or ``"openai"``.
        target_model: The target text model identifier.
        messages: Parsed conversation messages.
        stream: Whether the client requested a streaming response.
        max_tokens: Maximum tokens for the target model response.
        system: System prompt(s).  Anthropic sends an array of content blocks,
                OpenAI sends a string.  Stored raw for pass-through.
        original_body: The raw JSON body for pass-through / debugging.
    """

    source_format: Literal["anthropic", "openai"]
    target_model: str
    messages: list[Message]
    stream: bool = False
    max_tokens: int = 4096
    system: list[dict] | str | None = None
    original_body: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Target model configuration
# ---------------------------------------------------------------------------


@dataclass
class TargetModelConfig:
    """Configuration for a target text model endpoint.

    Attributes:
        model_id: Model identifier string (e.g. ``"deepseek-v3.2"``).
        api_base: Base URL of the model API endpoint.
        api_key: API key for authentication.
        extra_params: Additional parameters to merge into the request body.
    """

    model_id: str
    api_base: str
    api_key: str
    extra_params: dict = field(default_factory=dict)
