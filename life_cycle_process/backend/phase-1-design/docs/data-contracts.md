# 数据契约

> 基于：PRD v1.1 | 日期：2026-06-28

---

## 1. 内部规范格式

### ProxyRequest

```python
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class ProxyRequest:
    source_format: Literal["anthropic", "openai"]
    target_model: str
    messages: list[Message]
    stream: bool = False
    max_tokens: int = 4096
    system: str | None = None
    original_body: dict = field(default_factory=dict)
```

### Message

```python
@dataclass
class Message:
    role: Literal["user", "assistant", "system"]
    content: list[ContentBlock]
```

### ContentBlock (Union)

```python
from typing import Union

@dataclass
class TextBlock:
    text: str

@dataclass
class ImageBlock:
    image_data: bytes
    media_type: str          # "image/png" | "image/jpeg" | "image/webp" | "image/gif"
    source_type: str         # "base64" | "url" | "data_uri"
    message_index: int       # 消息在 messages 中的位置
    block_index: int         # content block 在消息中的位置

ContentBlock = Union[TextBlock, ImageBlock]
```

---

## 2. Anthropic Messages API Schema

### 请求

```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 4096,
  "stream": true,
  "system": "You are a helpful assistant.",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "描述这张图片"},
        {
          "type": "image",
          "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "iVBORw0KGgo..."
          }
        }
      ]
    }
  ]
}
```

### 非流式响应

```json
{
  "id": "msg_01ABC123...",
  "type": "message",
  "role": "assistant",
  "model": "claude-sonnet-4-6",
  "content": [
    {"type": "text", "text": "这张图片显示了..."}
  ],
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 150,
    "output_tokens": 80
  }
}
```

### 流式 SSE 事件

```
event: message_start
data: {"type":"message_start","message":{"id":"msg_...","type":"message","role":"assistant","model":"claude-sonnet-4-6","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":0,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"这"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"张图片"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":80}}

event: message_stop
data: {"type":"message_stop"}
```

---

## 3. OpenAI Chat Completions API Schema

### 请求

```json
{
  "model": "gpt-4",
  "max_tokens": 4096,
  "stream": true,
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "描述这张图片"},
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,iVBORw0KGgo..."
          }
        }
      ]
    }
  ]
}
```

### 非流式响应

```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "model": "gpt-4",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "这张图片显示了..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 150,
    "completion_tokens": 80,
    "total_tokens": 230
  }
}
```

### 流式 SSE Chunk

```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"这"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"张图片"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

---

## 4. TargetModelConfig

```python
@dataclass
class TargetModelConfig:
    model_id: str
    api_base: str
    api_key: str
    extra_params: dict = field(default_factory=dict)
```

配置来源：环境变量 + `.env` 文件。

---

## 5. 配置 Schema (pydantic-settings)

```python
from pydantic_settings import BaseSettings

class ProxyConfig(BaseSettings):
    # 代理服务
    proxy_host: str = "0.0.0.0"
    proxy_port: int = 8080
    proxy_api_key: str = ""

    # Qwen 视觉服务
    vision_api_key: str = ""
    vision_base_url: str = "https://dashscope.aliyuncs.com"
    vision_model: str = "qwen-vl-plus"
    vision_timeout: int = 30

    # 默认目标模型
    target_default_model: str = "deepseek-chat"
    target_default_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    target_default_api_key: str = ""

    # GLM 5.2
    target_glm_model: str = ""
    target_glm_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    target_glm_api_key: str = ""

    class Config:
        env_file = ".env"
```
