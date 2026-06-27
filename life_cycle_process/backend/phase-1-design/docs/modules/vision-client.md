# Vision Client — Qwen 3.7 Plus 识图服务

## 职责

封装阿里云百炼 Qwen 3.7 Plus API，将图片发送给视觉模型，获取文字描述。

## API 调用

```python
class QwenVisionClient:
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url or "https://dashscope.aliyuncs.com"
        self.model = "qwen-vl-plus"  # Qwen 3.7 Plus 视觉模型

    async def recognize(self, image: ImageBlock) -> str:
        """识别单张图片，返回文字描述"""
        ...
```

## 调用方式

使用阿里云 DashScope SDK 或 OpenAI 兼容接口：

```python
# OpenAI 兼容方式调用 Qwen 3.7 Plus
async def recognize(self, image: ImageBlock) -> str:
    import httpx
    import base64

    b64 = base64.b64encode(image.image_data).decode()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{self.base_url}/compatible-mode/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": "qwen-vl-plus",
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image.media_type};base64,{b64}"
                            }
                        },
                        {
                            "type": "text",
                            "text": "请详细描述这张图片中的所有内容，包括文字、物体、人物、场景、颜色、布局等。"
                        }
                    ]
                }]
            },
            timeout=30.0
        )
        data = resp.json()
        return data["choices"][0]["message"]["content"]
```

## 并行识图

多张图片时并行调用，减少总延迟：

```python
async def recognize_batch(self, images: list[ImageBlock]) -> list[str]:
    tasks = [self.recognize(img) for img in images]
    return await asyncio.gather(*tasks, return_exceptions=True)
    # 单个失败不阻塞其他，失败项返回 "[图片无法识别]"
```

## 降级策略

| 情况 | 处理 |
|------|------|
| API 返回非 200 | 返回 `"[图片无法识别]"` |
| 超时 30s | 返回 `"[图片无法识别]"` |
| 响应解析失败 | 返回 `"[图片无法识别]"` |
| 图片格式不支持 | 返回 `"[不支持的图片格式: {media_type}]"` |

## 配置

```env
VISION_API_KEY=sk-xxx
VISION_BASE_URL=https://dashscope.aliyuncs.com
VISION_MODEL=qwen-vl-plus
VISION_TIMEOUT=30
```

## 限界上下文

- 不处理请求解析（由 Format Detector 负责）
- 不回填请求体（由 Request Rewriter 负责）
- 仅负责图片 → 文字描述的转换
