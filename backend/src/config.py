"""
Configuration management for the multimodal proxy gateway.

Uses pydantic-settings to load configuration from environment variables
and .env files. All field names follow Python snake_case convention and
are automatically mapped to UPPER_CASE environment variables.
"""

from pydantic_settings import BaseSettings


class ProxyConfig(BaseSettings):
    """Proxy gateway configuration loaded from config.ini and environment variables.

    Required fields:
        - vision_api_key: Aliyun Bailian Coding Plan API key for Qwen vision model

    Target API keys come from client request headers (x-api-key), not config.
    """

    # --- Proxy service ---
    proxy_host: str = "0.0.0.0"
    proxy_port: int = 9856
    proxy_api_key: str = ""

    # --- Qwen vision service (Aliyun Bailian Coding Plan) ---
    vision_api_key: str = ""
    vision_base_url: str = "https://coding.dashscope.aliyuncs.com"
    vision_model: str = "qwen3.7-plus"
    vision_timeout: int = 30

    # --- Decision engine (lightweight intent recognition) ---
    decision_api_key: str = ""
    decision_base_url: str = "https://api.deepseek.com/v1"
    decision_model: str = "deepseek-chat"
    decision_timeout: int = 5

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    def validate_required(self) -> None:
        """Validate that all required configuration fields are set.

        Only VISION_API_KEY is required server-side.  Target routing
        is driven by the client request path, not by fixed config.
        """
        if not self.vision_api_key:
            raise ValueError(
                "Missing required configuration: VISION_API_KEY. "
                "Set it via environment variable or .env file."
            )
