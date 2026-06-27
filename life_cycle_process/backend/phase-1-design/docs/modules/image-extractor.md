# Image Extractor — 图片检测与提取

## 职责

遍历 `ProxyRequest.messages[].content[]`，检测所有 image content block，提取图片二进制数据。

## 检测逻辑

```python
def has_images(request: ProxyRequest) -> bool:
    for msg in request.messages:
        for block in msg.content:
            if isinstance(block, ImageBlock):
                return True
    return False

def extract_images(request: ProxyRequest) -> list[ImageBlock]:
    images = []
    for msg in request.messages:
        for block in msg.content:
            if isinstance(block, ImageBlock):
                images.append(block)
    return images
```

## ImageBlock 结构

```python
@dataclass
class ImageBlock:
    image_data: bytes        # 解码后的图片二进制
    media_type: str          # "image/png" | "image/jpeg" | "image/webp" | "image/gif"
    source_type: str         # "base64" | "url" | "data_uri"
    message_index: int       # 所在消息索引（用于回填）
    block_index: int         # 所在 content block 索引（用于回填）
```

## 数据来源处理

| 来源类型 | 处理方式 |
|----------|---------|
| Anthropic `source.type: "base64"` | base64 decode → bytes |
| Anthropic `source.type: "url"` | HTTP GET 下载 → bytes |
| OpenAI `image_url.url: "data:..."` | 解析 data URI → bytes |
| OpenAI `image_url.url: "https://..."` | HTTP GET 下载 → bytes |

## 支持的 media_type

- `image/png`
- `image/jpeg`
- `image/webp`
- `image/gif`

（对齐 Qwen 3.7 Plus 能力）

## 限界上下文

- 不调用视觉模型（由 Vision Client 负责）
- 不做请求体重写（由 Request Rewriter 负责）
- 仅做检测 + 二进制数据提取
