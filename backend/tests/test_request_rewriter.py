"""
Test cases for C03 — Request Rewriter (request_rewriter.py).

Covers:
- TC-C03-BLD-001: Compile check
- TC-C03-LOG-001: Single image block replaced with text block
- TC-C03-LOG-002: Multiple image blocks replaced with numbered text blocks
- TC-C03-LOG-003: Non-image content blocks preserved
- TC-C03-LOG-004: Rewritten body preserves source format (Anthropic)
- TC-C03-LOG-005: Image-only content → only vision result text
- TC-C03-SYS-001: System behavior — 2 images fully replaced
- TC-C03-SYS-002: System behavior — image-only, pure text output
"""

from __future__ import annotations

import py_compile
from pathlib import Path

import pytest

from backend.src.models import ImageBlock, Message, ProxyRequest, TextBlock
from backend.src.request_rewriter import RequestRewriter


# ============================================================================
# Helper factories
# ============================================================================


def _make_image_block(
    media_type: str = "image/png",
    message_index: int = 0,
    block_index: int = 0,
) -> ImageBlock:
    return ImageBlock(
        media_type=media_type,
        source_type="base64",
        source_data="fake_base64==",
        image_data=b"fake_bytes",
        message_index=message_index,
        block_index=block_index,
    )


def _make_anthropic_body(
    messages: list[dict],
    model: str = "deepseek-v3.2",
    max_tokens: int = 4096,
) -> dict:
    """Build an Anthropic-format body dict."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }


def _make_anthropic_msg_content(content: list[dict]) -> dict:
    return {"role": "user", "content": content}


# ============================================================================
# TC-C03-BLD-001: Compile check
# ============================================================================


def test_compiles():
    """TC-C03-BLD-001: request_rewriter.py compiles without syntax errors."""
    src = Path(__file__).parent.parent / "src" / "request_rewriter.py"
    py_compile.compile(str(src), doraise=True)


# ============================================================================
# TC-C03-LOG-001: Single image block replaced with text block
# ============================================================================


def test_single_image_replaced():
    """TC-C03-LOG-001: Single image block → single text block with description."""
    img = _make_image_block(message_index=0, block_index=0)

    original_body = _make_anthropic_body(
        messages=[
            _make_anthropic_msg_content([
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}},
                {"type": "text", "text": "这是啥"},
            ])
        ]
    )

    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek-v3.2",
        messages=[
            Message(role="user", content=[img, TextBlock(text="这是啥")]),
        ],
        original_body=original_body,
    )

    rewriter = RequestRewriter()
    result = rewriter.rewrite(request, [(img, "一张包含文字的截图")])

    # Check internal messages: no ImageBlock remains
    content = result.messages[0].content
    assert all(not isinstance(b, ImageBlock) for b in content), (
        "ImageBlock should be replaced"
    )
    assert any(
        isinstance(b, TextBlock) and "[图片 1/1 的描述：一张包含文字的截图]" in b.text
        for b in content
    ), "TextBlock should contain the numbered description"

    # Non-image block preserved
    assert any(
        isinstance(b, TextBlock) and b.text == "这是啥" for b in content
    ), "Existing TextBlock should be preserved"


# ============================================================================
# TC-C03-LOG-002: Multiple image blocks replaced with numbered text blocks
# ============================================================================


def test_multiple_images_numbered():
    """TC-C03-LOG-002: 3 images → numbered text blocks 1/3, 2/3, 3/3."""
    img_a = _make_image_block(message_index=0, block_index=0)
    img_b = _make_image_block(message_index=0, block_index=1)
    img_c = _make_image_block(message_index=0, block_index=2)

    original_body = _make_anthropic_body(
        messages=[
            _make_anthropic_msg_content([
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "a"}},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "b"}},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "c"}},
            ])
        ]
    )

    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek-v3.2",
        messages=[
            Message(role="user", content=[img_a, img_b, img_c]),
        ],
        original_body=original_body,
    )

    rewriter = RequestRewriter()
    result = rewriter.rewrite(
        request,
        [(img_a, "结果A"), (img_b, "结果B"), (img_c, "结果C")],
    )

    content = result.messages[0].content
    assert len(content) == 3
    assert "[图片 1/3 的描述：结果A]" in content[0].text
    assert "[图片 2/3 的描述：结果B]" in content[1].text
    assert "[图片 3/3 的描述：结果C]" in content[2].text


# ============================================================================
# TC-C03-LOG-003: Non-image content blocks preserved
# ============================================================================


def test_non_image_content_preserved():
    """TC-C03-LOG-003: TextBlocks flanking an ImageBlock stay untouched."""
    img = _make_image_block(message_index=0, block_index=1)

    original_body = _make_anthropic_body(
        messages=[
            _make_anthropic_msg_content([
                {"type": "text", "text": "hello"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}},
                {"type": "text", "text": "world"},
            ])
        ]
    )

    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek-v3.2",
        messages=[
            Message(
                role="user",
                content=[
                    TextBlock(text="hello"),
                    img,
                    TextBlock(text="world"),
                ],
            ),
        ],
        original_body=original_body,
    )

    rewriter = RequestRewriter()
    result = rewriter.rewrite(request, [(img, "图片描述")])

    content = result.messages[0].content
    assert len(content) == 3
    assert content[0].text == "hello"
    assert "[图片 1/1 的描述：图片描述]" in content[1].text
    assert content[2].text == "world"


# ============================================================================
# TC-C03-LOG-004: Rewritten body preserves source_format (Anthropic)
# ============================================================================


def test_preserves_anthropic_format():
    """TC-C03-LOG-004: Output body still has Anthropic messages/model/max_tokens structure."""
    img = _make_image_block(message_index=0, block_index=0)

    original_body = _make_anthropic_body(
        messages=[
            _make_anthropic_msg_content([
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "y"}},
            ])
        ],
        model="deepseek-v3.2",
        max_tokens=2048,
    )

    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek-v3.2",
        messages=[Message(role="user", content=[img])],
        max_tokens=2048,
        original_body=original_body,
    )

    rewriter = RequestRewriter()
    result = rewriter.rewrite(request, [(img, "描述内容")])

    body = result.original_body
    assert "messages" in body
    assert body["model"] == "deepseek-v3.2"
    assert body["max_tokens"] == 2048
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"

    # The content block should now be text, not image
    new_block = body["messages"][0]["content"][0]
    assert new_block["type"] == "text"
    assert "[图片 1/1 的描述：描述内容]" in new_block["text"]


# ============================================================================
# TC-C03-LOG-005: Image-only content → only vision result text
# ============================================================================


def test_image_only_content_forwarded():
    """TC-C03-LOG-005: Only image, no text → content becomes single text block."""
    img = _make_image_block(message_index=0, block_index=0)

    original_body = _make_anthropic_body(
        messages=[
            _make_anthropic_msg_content([
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "z"}},
            ])
        ]
    )

    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek-v3.2",
        messages=[Message(role="user", content=[img])],
        original_body=original_body,
    )

    rewriter = RequestRewriter()
    result = rewriter.rewrite(request, [(img, "这张图显示了一个日落场景")])

    content = result.messages[0].content
    assert len(content) == 1
    assert isinstance(content[0], TextBlock)
    assert "[图片 1/1 的描述：这张图显示了一个日落场景]" in content[0].text


# ============================================================================
# TC-C03-SYS-001: System behavior — 2 images fully replaced
# ============================================================================


def test_two_images_fully_replaced():
    """TC-C03-SYS-001: 2 ImageBlocks → 0 ImageBlocks, 2 TextBlocks with descriptions."""
    img_a = _make_image_block(message_index=0, block_index=0)
    img_b = _make_image_block(message_index=0, block_index=1)

    original_body = _make_anthropic_body(
        messages=[
            _make_anthropic_msg_content([
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "a"}},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "b"}},
            ])
        ]
    )

    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek-v3.2",
        messages=[Message(role="user", content=[img_a, img_b])],
        original_body=original_body,
    )

    rewriter = RequestRewriter()
    result = rewriter.rewrite(
        request,
        [(img_a, "图片一描述"), (img_b, "图片二描述")],
    )

    content = result.messages[0].content
    assert len(content) == 2

    # Zero ImageBlock
    image_count = sum(1 for b in content if isinstance(b, ImageBlock))
    assert image_count == 0, "No ImageBlock should remain"

    # Two TextBlock with "图片" keyword
    text_blocks = [b for b in content if isinstance(b, TextBlock)]
    assert len(text_blocks) == 2
    assert all("图片" in b.text for b in text_blocks)

    # Verify original_body also has zero image blocks
    body_blocks = result.original_body["messages"][0]["content"]
    assert all(b["type"] == "text" for b in body_blocks)


# ============================================================================
# TC-C03-SYS-002: System behavior — image-only, pure text output
# ============================================================================


def test_image_only_pure_text_output():
    """TC-C03-SYS-002: messages[0].content = [ImageBlock] → [TextBlock] only."""
    img = _make_image_block(message_index=0, block_index=0)

    original_body = _make_anthropic_body(
        messages=[
            _make_anthropic_msg_content([
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "q"}},
            ])
        ]
    )

    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek-v3.2",
        messages=[Message(role="user", content=[img])],
        original_body=original_body,
    )

    rewriter = RequestRewriter()
    result = rewriter.rewrite(request, [(img, "含文字的UI截图")])

    content = result.messages[0].content
    assert len(content) == 1
    assert isinstance(content[0], TextBlock)
    assert "[图片 1/1 的描述：含文字的UI截图]" in content[0].text

    # Verify rewritten body is non-empty and has text content
    body = result.original_body
    assert body is not None
    body_blocks = body["messages"][0]["content"]
    assert len(body_blocks) >= 1
    assert body_blocks[0]["type"] == "text"
    assert len(body_blocks[0]["text"]) > 0


# ============================================================================
# Edge cases
# ============================================================================


def test_empty_vision_results_noop():
    """Empty vision_results → request returned unchanged (safe no-op)."""
    img = _make_image_block(message_index=0, block_index=0)

    original_body = _make_anthropic_body(
        messages=[
            _make_anthropic_msg_content([
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}},
            ])
        ]
    )

    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek-v3.2",
        messages=[Message(role="user", content=[img])],
        original_body=original_body,
    )

    rewriter = RequestRewriter()
    result = rewriter.rewrite(request, [])

    # Should still have ImageBlock (unchanged)
    assert isinstance(result.messages[0].content[0], ImageBlock)
    # original_body is the same object (unchanged)
    assert result.original_body is original_body
