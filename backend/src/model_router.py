"""
Model router for the multimodal proxy gateway.

Resolves incoming model name requests to the appropriate TargetModelConfig
using case-insensitive substring matching, with a fallback to a "default" entry.
"""

from backend.src.config import ProxyConfig
from backend.src.models import TargetModelConfig


class ModelRouter:
    """Routes requested model names to TargetModelConfig entries.

    Constructor accepts a dict mapping config keys (e.g. "deepseek", "glm",
    "default") to TargetModelConfig instances.  The resolve() method uses
    case-insensitive substring matching: if any config key's lowercased form
    appears inside the lowercased requested model name, that config is used.
    Otherwise the entry keyed "default" is returned.
    """

    def __init__(self, configs: dict[str, TargetModelConfig]) -> None:
        self._configs = configs

    def resolve(self, requested_model: str) -> TargetModelConfig:
        """Return the best-matching TargetModelConfig for *requested_model*.

        1. Lowercases *requested_model* and iterates over each registered key.
        2. If a lowercased key is a substring of the lowercased model name,
           the corresponding config is returned immediately.
        3. Falls back to ``self._configs["default"]`` when no key matches.
        """
        target = requested_model.lower()
        for key, config in self._configs.items():
            if key.lower() in target:
                return config
        return self._configs["default"]


def build_router(config: ProxyConfig) -> ModelRouter:
    """Build a ModelRouter pre-populated from *config*.

    Creates two entries:
    * ``"default"`` — from ``target_default_*`` fields
    * ``"glm"``     — from ``target_glm_*`` fields
    """
    default_config = TargetModelConfig(
        model_id=config.target_default_model,
        api_base=config.target_default_base_url,
        api_key=config.target_default_api_key,
    )

    glm_config = TargetModelConfig(
        model_id=config.target_glm_model,
        api_base=config.target_glm_base_url,
        api_key=config.target_glm_api_key,
    )

    return ModelRouter(
        {
            "default": default_config,
            "glm": glm_config,
        }
    )
