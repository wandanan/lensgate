# Format Detector — 请求格式检测与解析

## 职责

识别输入请求是 Anthropic Messages API 还是 OpenAI Chat Completions API 格式，解析为内部规范格式 `ProxyRequest`。

## 检测策略

| 端点路径 | 格式 | 判断逻辑 |
|----------|------|---------|
| `/v1/messages` | Anthropic | `messages[].content` 是 string 或 `ContentBlock[]` |
| `/v1/chat/completions` | OpenAI | `messages[].content` 是 string 或 `ContentPart[]` |

## Anthropic Content Block 类型

```json
// 纯文本 block（直接透传）
{"type": "text", "text": "..."}

// 图片 block（需要拦截）
{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}
{"type": "image", "source": {"type": "url", "url": "https://..."}}

// tool_use block（透传）
{"type": "tool_use", "id": "...", "name": "...", "input": {...}}

// tool_result block（透传）
{"type": "tool_result", "tool_use_id": "...", "content": "..."}
```

## OpenAI Content Part 类型

```json
// 纯文本（直接透传）
{"type": "text", "text": "..."}

// 图片（需要拦截）
{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
{"type": "image_url", "image_url": {"url": "https://..."}}
```

## 内部输出

```python
@dataclass
class ProxyRequest:
    source_format: Literal["anthropic", "openai"]
    target_model: str          # 从 model 字段提取，用于路由
    messages: list[Message]
    stream: bool
    max_tokens: int
    system: str | None         # Anthropic 的 system 字段
    original_body: dict        # 保留原始请求体（用于透传）
```

## 限界上下文

- 不做图片检测（由 Image Extractor 负责）
- 不做模型路由决策（由 Model Router 负责）
- 仅做格式识别 + 结构化解析
