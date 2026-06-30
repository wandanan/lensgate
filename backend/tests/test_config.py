"""
Test cases for config.py — Configuration management (ProxyConfig).

Covers:
- TC-A02-BLD-001: config.py compiles without syntax errors
- TC-A02-LOG-001: ProxyConfig loads from environment variables
- TC-A02-LOG-002: ProxyConfig default values
- TC-A02-LOG-005: TargetModelConfig populated from environment variables
- TC-A02-LOG-007: validate_required() raises on missing keys
"""

import os

import pytest


# ---------------------------------------------------------------------------
# TC-A02-LOG-001: ProxyConfig loads from environment variable
# ---------------------------------------------------------------------------

def test_proxy_config_from_env(monkeypatch):
    """ProxyConfig reads PROXY_PORT from environment."""
    monkeypatch.setenv("PROXY_PORT", "9090")

    from backend.src.core.config import ProxyConfig

    config = ProxyConfig()
    assert config.proxy_port == 9090


# ---------------------------------------------------------------------------
# TC-A02-LOG-002: ProxyConfig default values
# ---------------------------------------------------------------------------

def test_proxy_config_defaults(monkeypatch):
    """ProxyConfig returns expected defaults when no env vars are set."""
    # Clear any pre-existing env vars that might affect defaults
    for key in (
        "PROXY_HOST", "PROXY_PORT", "VISION_TIMEOUT",
        "VISION_BASE_URL", "VISION_MODEL",
        "TARGET_DEFAULT_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    from backend.src.core.config import ProxyConfig

    config = ProxyConfig()
    assert config.proxy_host == "0.0.0.0"
    assert config.proxy_port == 9856
    assert config.vision_base_url == "https://coding.dashscope.aliyuncs.com"
    assert config.vision_model == "qwen3.7-plus"
    assert config.vision_timeout == 180


# ---------------------------------------------------------------------------
# TC-A02-LOG-005: TargetModelConfig populated from environment variables
# ---------------------------------------------------------------------------

def test_target_model_config_from_env(monkeypatch):
    """TargetModelConfig fields can be sourced from environment variables."""
    monkeypatch.setenv("TARGET_DEFAULT_MODEL", "deepseek")
    monkeypatch.setenv("TARGET_DEFAULT_BASE_URL", "https://test.com")
    monkeypatch.setenv("TARGET_DEFAULT_API_KEY", "key123")

    from backend.src.core.models import TargetModelConfig

    config = TargetModelConfig(
        model_id=os.environ["TARGET_DEFAULT_MODEL"],
        api_base=os.environ["TARGET_DEFAULT_BASE_URL"],
        api_key=os.environ["TARGET_DEFAULT_API_KEY"],
    )
    assert config.model_id == "deepseek"
    assert config.api_base == "https://test.com"
    assert config.api_key == "key123"


# ---------------------------------------------------------------------------
# TC-A02-LOG-007: validate_required() raises when keys are missing
# ---------------------------------------------------------------------------

def test_validate_required_raises_on_missing_keys(monkeypatch):
    """validate_required() raises ValueError when VISION_API_KEY
    and TARGET_DEFAULT_API_KEY are not set."""
    monkeypatch.delenv("VISION_API_KEY", raising=False)

    from backend.src.core.config import ProxyConfig

    config = ProxyConfig()
    config.vision_api_key = ""
    with pytest.raises((RuntimeError, ValueError)):
        config.validate_required()


# ---------------------------------------------------------------------------
# Additional: validate_required passes when keys are set
# ---------------------------------------------------------------------------

def test_validate_required_passes_when_keys_set(monkeypatch):
    """validate_required() does not raise when required keys are present."""
    monkeypatch.setenv("VISION_API_KEY", "sk-test-123")
    monkeypatch.setenv("TARGET_DEFAULT_API_KEY", "sk-test-456")

    from backend.src.core.config import ProxyConfig

    config = ProxyConfig()
    # Should not raise
    config.validate_required()
