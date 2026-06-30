"""
Configuration management for the multimodal proxy gateway.

Uses pydantic-settings to load configuration from environment variables
and .env file. All field names follow Python snake_case convention and
are automatically mapped to UPPER_CASE environment variables.

.env must be placed at the project root (current working directory).
"""

from pydantic_settings import BaseSettings


QWEN_VISION_BASE_URL = "https://coding.dashscope.aliyuncs.com"
QWEN_VISION_MODEL = "qwen3.7-plus"


class ProxyConfig(BaseSettings):
    """Proxy gateway configuration loaded from .env and environment variables.

    Required fields:
        - vision_api_key: Vision model API key
        - decision_api_key: Decision engine API key

    Target API keys come from client request headers (x-api-key), not config.
    """

    # --- Proxy service ---
    proxy_host: str = "0.0.0.0"
    proxy_port: int = 9856
    proxy_api_key: str = ""

    # --- Vision service ---
    vision_api_key: str = ""
    vision_base_url: str = QWEN_VISION_BASE_URL
    vision_model: str = QWEN_VISION_MODEL
    vision_timeout: int = 180

    # --- Decision engine (lightweight intent recognition) ---
    decision_api_key: str = ""
    decision_base_url: str = "https://api.deepseek.com/v1"
    decision_model: str = "deepseek-chat"
    decision_timeout: int = 5

    model_config = {
        "env_file": [".env", "backend/.env"],
        "env_file_encoding": "utf-8",
    }

    def validate_required(self) -> None:
        """Validate that all required configuration fields are set.

        VISION_API_KEY + DECISION_API_KEY are required server-side.
        Target routing is driven by the client request path, not by fixed config.
        """
        missing = []
        if not self.vision_api_key:
            missing.append("VISION_API_KEY（视觉模型密钥）")
        if not self.decision_api_key:
            missing.append("DECISION_API_KEY（决策模型密钥）")
        if missing:
            raise ValueError(
                "Missing required configuration:\n  "
                + "\n  ".join(missing)
                + "\n\n请编辑项目根目录的 .env 文件，填入以上密钥。\n"
                "Docker 运行时请确保 .env 在项目根目录。"
            )
