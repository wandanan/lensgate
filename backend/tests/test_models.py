"""
Test cases for models.py — Data model dataclasses.

Covers:
- TC-A02-BLD-002: models.py compiles without syntax errors
- TC-A02-LOG-003: ProxyRequest creation and field access
- TC-A02-LOG-004: Message role restricted to Literal["user","assistant","system"]
- TC-A02-LOG-006: TextBlock and ImageBlock are valid ContentBlock union members
"""

import pytest


# ---------------------------------------------------------------------------
# TC-A02-LOG-003: ProxyRequest creation and field access
# ---------------------------------------------------------------------------

def test_proxy_request_creation():
    """ProxyRequest can be constructed and fields are accessible."""
    from backend.src.core.models import ProxyRequest, Message, TextBlock

    request = ProxyRequest(
        source_format="anthropic",
        target_model="deepseek",
        messages=[Message(role="user", content=[TextBlock(text="hello")])],
    )
    assert request.source_format == "anthropic"
    assert request.target_model == "deepseek"
    assert len(request.messages) == 1
    assert request.stream is False
    assert request.max_tokens == 4096


# ---------------------------------------------------------------------------
# TC-A02-LOG-004: Message role is Literal["user", "assistant", "system"]
# ---------------------------------------------------------------------------

def test_message_role_valid():
    """Message accepts valid role values without exception."""
    from backend.src.core.models import Message, TextBlock

    content = [TextBlock(text="hi")]

    msg_user = Message(role="user", content=content)
    assert msg_user.role == "user"

    msg_assistant = Message(role="assistant", content=content)
    assert msg_assistant.role == "assistant"

    msg_system = Message(role="system", content=content)
    assert msg_system.role == "system"


# ---------------------------------------------------------------------------
# TC-A02-LOG-006: TextBlock and ImageBlock are ContentBlock union members
# ---------------------------------------------------------------------------

def test_content_block_union():
    """Both TextBlock and ImageBlock can be used as ContentBlock."""
    from backend.src.core.models import TextBlock, ImageBlock, ContentBlock

    text_block: ContentBlock = TextBlock(text="hello")
    assert isinstance(text_block, TextBlock)
    assert text_block.text == "hello"

    image_block: ContentBlock = ImageBlock(
        image_data=b"abc",
        media_type="image/png",
        source_type="base64",
        message_index=0,
        block_index=1,
    )
    assert isinstance(image_block, ImageBlock)
    assert image_block.media_type == "image/png"
    assert image_block.source_type == "base64"
    assert image_block.message_index == 0
    assert image_block.block_index == 1


# ---------------------------------------------------------------------------
# Additional: ImageBlock fields are all accessible
# ---------------------------------------------------------------------------

def test_image_block_fields():
    """ImageBlock stores and returns all fields correctly."""
    from backend.src.core.models import ImageBlock

    block = ImageBlock(
        image_data=b"\x89PNG\r\n\x1a\n",
        media_type="image/png",
        source_type="base64",
        message_index=2,
        block_index=0,
    )
    assert block.image_data == b"\x89PNG\r\n\x1a\n"
    assert block.media_type == "image/png"
    assert block.source_type == "base64"
    assert block.message_index == 2
    assert block.block_index == 0


# ---------------------------------------------------------------------------
# Additional: TargetModelConfig with extra_params
# ---------------------------------------------------------------------------

def test_target_model_config():
    """TargetModelConfig stores model_id, api_base, api_key, extra_params."""
    from backend.src.core.models import TargetModelConfig

    config = TargetModelConfig(
        model_id="deepseek-v3.2",
        api_base="https://ark.cn-beijing.volces.com/api/coding",
        api_key="sk-xxx",
        extra_params={"temperature": 0.7},
    )
    assert config.model_id == "deepseek-v3.2"
    assert config.api_base == "https://ark.cn-beijing.volces.com/api/coding"
    assert config.api_key == "sk-xxx"
    assert config.extra_params == {"temperature": 0.7}


# ---------------------------------------------------------------------------
# Additional: TargetModelConfig default extra_params
# ---------------------------------------------------------------------------

def test_target_model_config_default_extra_params():
    """TargetModelConfig extra_params defaults to empty dict."""
    from backend.src.core.models import TargetModelConfig

    config = TargetModelConfig(
        model_id="deepseek",
        api_base="https://test.com",
        api_key="key123",
    )
    assert config.extra_params == {}
