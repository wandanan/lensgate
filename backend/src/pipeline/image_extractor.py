"""
Image Extractor — detects and extracts image content blocks from proxy requests.

Handles images at top-level and recursively inside tool_result blocks.
Supports ``latest_only`` mode to skip images in historical messages that
have already been processed in previous turns.
"""

from __future__ import annotations

import base64
import hashlib
import re

import httpx

from backend.src.pipeline.cache_store import cache
from backend.src.core.models import ImageBlock, ProxyRequest, ToolResultBlock

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_MEDIA_TYPES: set[str] = {"image/png", "image/jpeg", "image/webp", "image/gif"}

_DATA_URI_RE = re.compile(r"^data:(image/[\w.+-]+);base64,(.+)$", re.ASCII)

_DEFAULT_DOWNLOAD_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def has_images(request: ProxyRequest, latest_only: bool = False) -> bool:
    """Check whether the request contains any ImageBlock.

    When *latest_only* is True, only the last message is checked.
    """
    messages = request.messages[-1:] if latest_only else request.messages
    for msg in messages:
        if _any_image(msg.content):
            return True
    return False


async def extract_images(
    request: ProxyRequest,
    latest_only: bool = False,
) -> list[ImageBlock]:
    """Extract all ImageBlocks and resolve binary data.

    When *latest_only* is True, only images from the last message are extracted.
    """
    images: list[ImageBlock] = []
    messages = request.messages[-1:] if latest_only else request.messages

    for msg_idx, msg in enumerate(messages):
        real_idx = len(request.messages) - len(messages) + msg_idx
        await _extract_from_blocks(msg.content, real_idx, images)

    return images


def validate_image_format(block: ImageBlock) -> bool:
    """Check whether the image block's media_type is in the supported whitelist."""
    return block.media_type in SUPPORTED_MEDIA_TYPES


def image_hash(block: ImageBlock) -> str:
    """SHA-256 of image data for dedup caching."""
    if block.image_data:
        return hashlib.sha256(block.image_data).hexdigest()
    return ""


def cache_get(h: str, focus: str = "") -> str | None:
    return cache.get(h, focus)


def cache_set(h: str, description: str, focus: str = "通用描述",
              file_name: str = "", position: int = 0,
              label: str = "") -> None:
    cache.set(h, description, focus, file_name, position, label)


def cache_entries() -> list[dict[str, str]]:
    return cache.entries()


def extract_file_metadata(request: ProxyRequest, img: ImageBlock) -> tuple[str, int]:
    """Extract (file_name, position) from conversation context for an image.

    Looks for a tool_use: Read block preceding the tool_result that contains
    this image, and reads input.file_path.
    """
    file_name = ""

    msg_idx = img.message_index
    parent_idx = img.parent_block_index

    if parent_idx is not None and msg_idx > 0:
        prev_msg = request.messages[msg_idx - 1]
        for block in prev_msg.content:
            from backend.src.core.models import ToolUseBlock
            if isinstance(block, ToolUseBlock) and block.name == "Read":
                file_path = block.input.get("file_path", "")
                if file_path:
                    file_name = file_path.replace("\\", "/").rsplit("/", 1)[-1]

    pos = cache.next_position() if (file_name or img.image_data) else 0
    return file_name, pos


# ---------------------------------------------------------------------------
# Recursive walkers
# ---------------------------------------------------------------------------


def _any_image(blocks: list) -> bool:
    for block in blocks:
        if isinstance(block, ImageBlock):
            return True
        if isinstance(block, ToolResultBlock):
            if _any_image(block.content):
                return True
    return False


async def _extract_from_blocks(
    blocks: list,
    msg_idx: int,
    images: list[ImageBlock],
    parent_block_idx: int | None = None,
) -> None:
    for blk_idx, block in enumerate(blocks):
        if isinstance(block, ImageBlock):
            block.message_index = msg_idx
            block.block_index = blk_idx
            if parent_block_idx is not None:
                block.parent_block_index = parent_block_idx

            if block.image_data is not None and len(block.image_data) > 0:
                images.append(block)
                continue

            try:
                await _resolve_image_data(block)
            except Exception:
                block.is_error = True
                block.image_data = None

            images.append(block)

        elif isinstance(block, ToolResultBlock):
            await _extract_from_blocks(block.content, msg_idx, images, blk_idx)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _resolve_image_data(block: ImageBlock) -> None:
    source_type = block.source_type
    source_data = block.source_data

    if source_type == "base64":
        block.image_data = base64.b64decode(source_data)
    elif source_type == "url":
        block.image_data = await _download_image(source_data)
    elif source_type == "data_uri":
        match = _DATA_URI_RE.match(source_data)
        if match is None:
            raise ValueError(f"Invalid data URI format: {source_data[:100]}...")
        mime_type, b64_payload = match.group(1), match.group(2)
        block.media_type = mime_type
        block.image_data = base64.b64decode(b64_payload)
    else:
        raise ValueError(f"Unknown source_type: {source_type!r}")


async def _download_image(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=_DEFAULT_DOWNLOAD_TIMEOUT) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content
