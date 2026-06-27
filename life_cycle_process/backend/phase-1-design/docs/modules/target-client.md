# Target Client — 目标模型客户端

## 职责

封装火山引擎 DeepSeek / GLM 5.2 API 调用，支持流式（SSE）和非流式两种模式。

## 模型路由配置

```python
@dataclass
class TargetModelConfig:
    model_id: str           # 目标模型 ID（如 "deepseek-chat"）
    api_base: str           # API endpoint
    api_key: str            # API key
    extra_params: dict      # 额外请求参数（如 temperature, top_p 等）

class ModelRouter:
    """根据请求中的 model 字段选择目标模型配置"""

    def __init__(self, configs: dict[str, TargetModelConfig]):
        self.configs = configs

    def resolve(self, requested_model: str) -> TargetModelConfig:
        # 按配置的 mapping 匹配
        # 如 "deepseek-chat" → DeepSeek 配置
        #    "glm-5.2" → GLM 5.2 配置
        for name, config in self.configs.items():
            if name in requested_model.lower():
                return config
        # fallback 到默认模型
        return self.configs["default"]
```

配置示例（`.env`）：

```env
# 默认目标模型
TARGET_DEFAULT_MODEL=deepseek-chat
TARGET_DEFAULT_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
TARGET_DEFAULT_API_KEY=xxx

# GLM 5.2
TARGET_GLM_MODEL=glm-5.2
TARGET_GLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
TARGET_GLM_API_KEY=xxx
```

## API 调用

目标模型使用 OpenAI 兼容接口（火山引擎 ARK）：

```python
class TargetModelClient:
    def __init__(self, router: ModelRouter):
        self.router = router

    async def forward(self, request_body: dict, config: TargetModelConfig) -> Response:
        """非流式转发"""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{config.api_base}/chat/completions",
                headers={"Authorization": f"Bearer {config.api_key}"},
                json=request_body,
                timeout=60.0,
            )
            return resp

    async def forward_stream(self, request_body: dict, config: TargetModelConfig):
        """流式转发 (SSE)"""
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{config.api_base}/chat/completions",
                headers={"Authorization": f"Bearer {config.api_key}"},
                json=request_body,
                timeout=120.0,
            ) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        yield line
```

## 响应处理

| 目标模型响应 | Proxy 处理 |
|-------------|-----------|
| 200 + JSON body | 解析 `choices[0].message.content` |
| 200 + SSE stream | 逐行透传 SSE 事件 |
| 4xx | 透传错误 status + body |
| 5xx | 返回 503 + `{"error": "target_model_unavailable"}` |
| 超时 | 返回 504 + `{"error": "target_model_timeout"}` |

## 限界上下文

- 不做请求体改写（由 Request Rewriter 负责）
- 不做响应格式转换（由 Response Handler 负责）
- 仅负责调用目标模型 + 返回原始响应
