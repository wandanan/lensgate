"""
Test cases for B02 — Image Extractor (image_extractor.py).

Covers:
- TC-B02-BLD-001: Compile check
- TC-B02-LOG-001: has_images returns False for text-only request
- TC-B02-LOG-002: has_images returns True for request with images
- TC-B02-LOG-003: extract_images returns all ImageBlocks (3 images)
- TC-B02-LOG-004: Anthropic base64 source correctly decodes
- TC-B02-LOG-005: ImageBlock records message_index and block_index
- TC-B02-LOG-006: media_type extracted from Anthropic source
- TC-B02-LOG-007: OpenAI data URI parsed correctly
- TC-B02-LOG-008: extract_images returns empty list for no images
- TC-B02-SYS-001: System behavior — has_images + extract_images with image
- TC-B02-SYS-002: System behavior — has_images returns False, skip extract
- TC-B02-SYS-003: System behavior — URL download failure → is_error, no raise
- TC-B02-SYS-004: System behavior — valid format passes validation
- TC-B02-SYS-005: System behavior — invalid format fails validation
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.src.models import ImageBlock, Message, ProxyRequest, TextBlock


# ============================================================================
# Helper factories
# ============================================================================

def _proxy_request_text_only() -> ProxyRequest:
    """Create a ProxyRequest with a single text-only message."""
    return ProxyRequest(
        source_format="anthropic",
        target_model="deepseek",
        messages=[
            Message(role="user", content=[TextBlock(text="Hello, world!")])
        ],
    )


def _proxy_request_with_images(
    images: list[ImageBlock] | None = None,
) -> ProxyRequest:
    """Create a ProxyRequest with one or more ImageBlocks."""
    if images is None:
        images = [
            ImageBlock(
                media_type="image/png",
                source_type="base64",
                source_data="aGVsbG8=",
            ),
        ]
    return ProxyRequest(
        source_format="anthropic",
        target_model="deepseek",
        messages=[
            Message(role="user", content=[TextBlock(text="describe"), images[0]]),
        ],
    )


# ============================================================================
# TC-B02-BLD-001: Compile check
# ============================================================================

def test_compile_check():
    """image_extractor.py compiles without syntax errors."""
    import backend.src.image_extractor  # noqa: F401


# ============================================================================
# TC-B02-LOG-001: has_images — text-only → False
# ============================================================================

def test_has_images_text_only_returns_false():
    """has_images returns False when all content blocks are TextBlock."""
    from backend.src.image_extractor import has_images

    request = _proxy_request_text_only()
    assert has_images(request) is False


# ============================================================================
# TC-B02-LOG-002: has_images — with image → True
# ============================================================================

def test_has_images_with_image_returns_true():
    """has_images returns True when a message contains an ImageBlock."""
    from backend.src.image_extractor import has_images

    request = _proxy_request_with_images()
    assert has_images(request) is True


# ============================================================================
# TC-B02-LOG-003: extract_images returns all ImageBlocks
# ============================================================================

@pytest.mark.asyncio
async def test_extract_images_returns_all_image_blocks():
    """extract_images returns a list with all ImageBlocks across all messages."""
    from backend.src.image_extractor import extract_images

    img1 = ImageBlock(
        media_type="image/png",
        source_type="base64",
        source_data="aGVsbG8=",
    )
    img2 = ImageBlock(
        media_type="image/jpeg",
        source_type="base64",
        source_data="d29ybGQ=",
    )
    img3 = ImageBlock(
        media_type="image/webp",
        source_type="base64",
        source_data="Zm9vYmFy",
    )

    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek",
        messages=[
            Message(
                role="user",
                content=[TextBlock(text="msg0"), img1, img2],
            ),
            Message(
                role="user",
                content=[TextBlock(text="msg1"), img3],
            ),
        ],
    )

    result = await extract_images(request)
    assert len(result) == 3
    assert result[0] is img1
    assert result[1] is img2
    assert result[2] is img3


# ============================================================================
# TC-B02-LOG-004: Anthropic base64 source correctly decodes
# ============================================================================

@pytest.mark.asyncio
async def test_anthropic_base64_decode():
    """Anthropic source.type='base64' with source_data='aGVsbG8=' → b'hello'."""
    from backend.src.image_extractor import extract_images

    img = ImageBlock(
        media_type="image/png",
        source_type="base64",
        source_data="aGVsbG8=",  # "hello" in base64
    )
    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek",
        messages=[Message(role="user", content=[img])],
    )

    result = await extract_images(request)
    assert len(result) == 1
    assert result[0].image_data == b"hello"
    assert result[0].source_type == "base64"
    assert result[0].is_error is False


# ============================================================================
# TC-B02-LOG-005: ImageBlock records message_index and block_index
# ============================================================================

@pytest.mark.asyncio
async def test_image_block_indices_set():
    """ImageBlock in message 1, content position 2 gets correct indices."""
    from backend.src.image_extractor import extract_images

    img = ImageBlock(
        media_type="image/jpeg",
        source_type="base64",
        source_data="d29ybGQ=",
    )
    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek",
        messages=[
            Message(role="user", content=[TextBlock(text="first")]),
            Message(
                role="user",
                content=[
                    TextBlock(text="before image"),
                    img,  # message_index=1, block_index=1
                    TextBlock(text="after image"),
                ],
            ),
        ],
    )

    result = await extract_images(request)
    assert len(result) == 1
    assert result[0].message_index == 1
    assert result[0].block_index == 1


# ============================================================================
# TC-B02-LOG-006: media_type from Anthropic source extracted
# ============================================================================

@pytest.mark.asyncio
async def test_media_type_from_anthropic_source():
    """Anthropic source.media_type='image/jpeg' preserved on ImageBlock."""
    from backend.src.image_extractor import extract_images

    img = ImageBlock(
        media_type="image/jpeg",
        source_type="base64",
        source_data="d29ybGQ=",
    )
    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek",
        messages=[Message(role="user", content=[img])],
    )

    result = await extract_images(request)
    assert result[0].media_type == "image/jpeg"


# ============================================================================
# TC-B02-LOG-007: OpenAI data URI parsed correctly
# ============================================================================

@pytest.mark.asyncio
async def test_openai_data_uri_parsed():
    """OpenAI data:image/png;base64,aGVsbG8= → b'hello', media_type='image/png'."""
    from backend.src.image_extractor import extract_images

    img = ImageBlock(
        source_type="data_uri",
        source_data="data:image/png;base64,aGVsbG8=",
    )
    request = ProxyRequest(
        source_format="openai",
        target_model="deepseek",
        messages=[Message(role="user", content=[img])],
    )

    result = await extract_images(request)
    assert len(result) == 1
    assert result[0].image_data == b"hello"
    assert result[0].media_type == "image/png"
    assert result[0].source_type == "data_uri"


# ============================================================================
# TC-B02-LOG-008: extract_images returns empty list for no images
# ============================================================================

@pytest.mark.asyncio
async def test_extract_images_text_only_returns_empty():
    """extract_images returns [] when request contains no ImageBlocks."""
    from backend.src.image_extractor import extract_images

    request = _proxy_request_text_only()
    result = await extract_images(request)
    assert result == []


# ============================================================================
# TC-B02-SYS-001: System behavior — image detected → EXTRACTING
# ============================================================================

@pytest.mark.asyncio
async def test_sys_image_check_with_image_enters_extracting():
    """When request has image: has_images() == True, extract_images() non-empty."""
    from backend.src.image_extractor import extract_images, has_images

    img = ImageBlock(
        media_type="image/png",
        source_type="base64",
        source_data="aGVsbG8=",
    )
    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek",
        messages=[Message(role="user", content=[TextBlock(text="look"), img])],
    )

    assert has_images(request) is True
    extracted = await extract_images(request)
    assert len(extracted) >= 1


# ============================================================================
# TC-B02-SYS-002: System behavior — no image → FORWARDING
# ============================================================================

def test_sys_image_check_no_image_skips_extract():
    """When request has no image: has_images() == False."""
    from backend.src.image_extractor import has_images

    request = _proxy_request_text_only()
    assert has_images(request) is False


# ============================================================================
# TC-B02-SYS-003: System behavior — URL download failure (is_error, no raise)
# ============================================================================

@pytest.mark.asyncio
async def test_sys_url_download_failure_marks_error():
    """URL download fails → is_error=True, image_data=None, no exception raised."""
    from backend.src.image_extractor import extract_images

    img = ImageBlock(
        media_type="image/png",
        source_type="url",
        source_data="https://example.com/missing.png",
    )
    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek",
        messages=[Message(role="user", content=[img])],
    )

    # Mock httpx.AsyncClient.get to simulate a connection error
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("Connection timeout")

        # Must not raise
        result = await extract_images(request)

    assert len(result) == 1
    assert result[0].is_error is True
    assert result[0].image_data is None


# ============================================================================
# TC-B02-SYS-004: System behavior — valid format passes validation
# ============================================================================

def test_sys_validate_valid_format():
    """validate_image_format returns True for image/png."""
    from backend.src.image_extractor import validate_image_format

    block = ImageBlock(media_type="image/png")
    assert validate_image_format(block) is True


def test_validate_all_supported_formats():
    """All four whitelisted formats pass validation."""
    from backend.src.image_extractor import validate_image_format

    for fmt in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        block = ImageBlock(media_type=fmt)
        assert validate_image_format(block) is True, f"{fmt} should be valid"


# ============================================================================
# TC-B02-SYS-005: System behavior — invalid format fails validation
# ============================================================================

def test_sys_validate_invalid_format():
    """validate_image_format returns False for unsupported image/bmp."""
    from backend.src.image_extractor import validate_image_format

    block = ImageBlock(media_type="image/bmp")
    assert validate_image_format(block) is False


def test_validate_various_invalid_formats():
    """Various unsupported formats all return False."""
    from backend.src.image_extractor import validate_image_format

    for fmt in ("image/bmp", "image/tiff", "image/svg+xml", "video/mp4", ""):
        block = ImageBlock(media_type=fmt)
        assert validate_image_format(block) is False, f"{fmt!r} should be invalid"


# ============================================================================
# Additional edge-case tests
# ============================================================================

@pytest.mark.asyncio
async def test_multiple_images_same_message():
    """Multiple ImageBlocks in the same message are all extracted with correct indices."""
    from backend.src.image_extractor import extract_images

    img1 = ImageBlock(
        media_type="image/png",
        source_type="base64",
        source_data="aGVsbG8=",
    )
    img2 = ImageBlock(
        media_type="image/jpeg",
        source_type="base64",
        source_data="d29ybGQ=",
    )
    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek",
        messages=[Message(role="user", content=[img1, TextBlock(text="mid"), img2])],
    )

    result = await extract_images(request)
    assert len(result) == 2
    assert result[0].block_index == 0
    assert result[1].block_index == 2


@pytest.mark.asyncio
async def test_image_data_already_populated_skipped():
    """When image_data is already set, resolution is skipped (no double-decode)."""
    from backend.src.image_extractor import extract_images

    img = ImageBlock(
        image_data=b"already_here",
        media_type="image/png",
        source_type="base64",
        source_data="dGhpc19pc19ub3RfdmFsaWRfYmFzZTY0",  # not valid base64
    )
    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek",
        messages=[Message(role="user", content=[img])],
    )

    result = await extract_images(request)
    assert len(result) == 1
    # Should keep original data; should NOT try to decode source_data
    assert result[0].image_data == b"already_here"


@pytest.mark.asyncio
async def test_openai_data_uri_jpeg():
    """OpenAI data URI for JPEG is correctly parsed."""
    from backend.src.image_extractor import extract_images

    # "world" in base64
    img = ImageBlock(
        source_type="data_uri",
        source_data="data:image/jpeg;base64,d29ybGQ=",
    )
    request = ProxyRequest(
        source_format="openai",
        target_model="deepseek",
        messages=[Message(role="user", content=[img])],
    )

    result = await extract_images(request)
    assert result[0].image_data == b"world"
    assert result[0].media_type == "image/jpeg"


@pytest.mark.asyncio
async def test_invalid_data_uri_marks_error():
    """Malformed data URI → is_error=True, no exception raised."""
    from backend.src.image_extractor import extract_images

    img = ImageBlock(
        source_type="data_uri",
        source_data="not-a-valid-data-uri",
    )
    request = ProxyRequest(
        source_format="openai",
        target_model="deepseek",
        messages=[Message(role="user", content=[img])],
    )

    result = await extract_images(request)
    assert len(result) == 1
    assert result[0].is_error is True
    assert result[0].image_data is None


@pytest.mark.asyncio
async def test_unknown_source_type_marks_error():
    """ImageBlock with unknown source_type → is_error=True."""
    from backend.src.image_extractor import extract_images

    img = ImageBlock(
        media_type="image/png",
        source_type="unknown_source",
        source_data="some_data",
    )
    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek",
        messages=[Message(role="user", content=[img])],
    )

    result = await extract_images(request)
    assert len(result) == 1
    assert result[0].is_error is True


@pytest.mark.asyncio
async def test_successful_url_download():
    """Successful URL download populates image_data correctly."""
    from backend.src.image_extractor import extract_images

    img = ImageBlock(
        media_type="image/png",
        source_type="url",
        source_data="https://example.com/image.png",
    )
    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek",
        messages=[Message(role="user", content=[img])],
    )

    mock_response = AsyncMock()
    mock_response.raise_for_status = lambda: None
    mock_response.content = b"\x89PNG\r\n\x1a\nfake_png_data"

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await extract_images(request)

    assert len(result) == 1
    assert result[0].image_data == b"\x89PNG\r\n\x1a\nfake_png_data"
    assert result[0].is_error is False
