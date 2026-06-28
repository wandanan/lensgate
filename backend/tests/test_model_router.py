"""
Test cases for model_router.py — ModelRouter resolve logic.

Covers:
- TC-C01-BLD-001: model_router.py compiles without syntax errors
- TC-C01-LOG-001: resolve matches by substring
- TC-C01-LOG-002: unmatched model falls back to default
- TC-C01-LOG-003: case-insensitive matching
- TC-C01-LOG-004: TargetModelConfig field completeness
"""

import pytest

from backend.src.config import ProxyConfig
from backend.src.model_router import ModelRouter, build_router
from backend.src.models import TargetModelConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_deepseek_config() -> TargetModelConfig:
    return TargetModelConfig(
        model_id="deepseek-chat",
        api_base="https://deepseek.example.com/api",
        api_key="sk-deepseek",
    )


def _make_glm_config() -> TargetModelConfig:
    return TargetModelConfig(
        model_id="glm-5.2",
        api_base="https://glm.example.com/api",
        api_key="sk-glm",
    )


def _make_default_config() -> TargetModelConfig:
    return TargetModelConfig(
        model_id="default-model",
        api_base="https://default.example.com/api",
        api_key="sk-default",
    )


# ---------------------------------------------------------------------------
# TC-C01-LOG-001: resolve matches by substring
# ---------------------------------------------------------------------------


def test_resolve_substring_match():
    """When requested_model contains a config key substring, that config is returned."""
    router = ModelRouter(
        {
            "deepseek": _make_deepseek_config(),
            "glm": _make_glm_config(),
            "default": _make_default_config(),
        }
    )

    result = router.resolve("deepseek-chat")
    assert result.model_id == "deepseek-chat"
    assert result.api_base == "https://deepseek.example.com/api"
    assert result.api_key == "sk-deepseek"


# ---------------------------------------------------------------------------
# TC-C01-LOG-002: fallback to default on no match
# ---------------------------------------------------------------------------


def test_resolve_fallback_to_default():
    """When no config key matches, the "default" entry is returned."""
    router = ModelRouter(
        {
            "deepseek": _make_deepseek_config(),
            "default": _make_default_config(),
        }
    )

    result = router.resolve("unknown-model")
    assert result.model_id == "default-model"
    assert result.api_base == "https://default.example.com/api"
    assert result.api_key == "sk-default"


# ---------------------------------------------------------------------------
# TC-C01-LOG-003: case-insensitive matching
# ---------------------------------------------------------------------------


def test_resolve_case_insensitive():
    """Matching is case-insensitive per the spec."""
    router = ModelRouter(
        {
            "deepseek": _make_deepseek_config(),
            "default": _make_default_config(),
        }
    )

    result = router.resolve("DeepSeek-Chat")
    assert result.model_id == "deepseek-chat"


# ---------------------------------------------------------------------------
# TC-C01-LOG-004: TargetModelConfig field completeness
# ---------------------------------------------------------------------------


def test_target_model_config_fields():
    """TargetModelConfig exposes model_id, api_base, api_key, extra_params."""
    config = TargetModelConfig(
        model_id="test-model",
        api_base="https://test.api",
        api_key="test-key",
        extra_params={"temperature": 0.5},
    )
    assert config.model_id == "test-model"
    assert config.api_base == "https://test.api"
    assert config.api_key == "test-key"
    assert config.extra_params == {"temperature": 0.5}


# ---------------------------------------------------------------------------
# Additional: extra_params defaults to empty dict
# ---------------------------------------------------------------------------


def test_target_model_config_extra_params_default():
    """extra_params defaults to empty dict when not provided."""
    config = TargetModelConfig(
        model_id="m",
        api_base="https://b",
        api_key="k",
    )
    assert config.extra_params == {}


# ---------------------------------------------------------------------------
# Additional: build_router from ProxyConfig
# ---------------------------------------------------------------------------


def test_build_router_from_proxy_config(monkeypatch):
    """build_router reads ProxyConfig fields and creates correct entries."""
    monkeypatch.setenv("TARGET_DEFAULT_MODEL", "deepseek-v3.2")
    monkeypatch.setenv("TARGET_DEFAULT_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding")
    monkeypatch.setenv("TARGET_DEFAULT_API_KEY", "sk-default-key")
    monkeypatch.setenv("TARGET_GLM_MODEL", "glm-5.2")
    monkeypatch.setenv("TARGET_GLM_BASE_URL", "https://glm.volces.com/api")
    monkeypatch.setenv("TARGET_GLM_API_KEY", "sk-glm-key")
    monkeypatch.setenv("VISION_API_KEY", "sk-vision-key")

    config = ProxyConfig()
    router = build_router(config)

    # default entry
    default_result = router.resolve("anything-not-matching")
    assert default_result.model_id == "deepseek-v3.2"
    assert default_result.api_key == "sk-default-key"

    # glm entry
    glm_result = router.resolve("glm-5.2-chat")
    assert glm_result.model_id == "glm-5.2"
    assert glm_result.api_key == "sk-glm-key"
