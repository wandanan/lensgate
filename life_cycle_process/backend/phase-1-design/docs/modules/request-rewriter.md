# Request Rewriter — 请求体重写

## 职责

将原始请求中的 image content block 替换为识图结果的 text content block，生成用于转发目标模型的最终请求体。

## 重写逻辑

```python
class RequestRewriter:
    def rewrite(
        self,
        request: ProxyRequest,
        image_results: list[tuple[ImageBlock, str]],  # (原图片, 识图文字)
    ) -> dict:
        """将图片 block 替换为识图文字，返回目标模型可接受的请求体"""
        ...
```

## 替换策略

对于每张图片，在原消息的 content 数组中：
- **删除** image content block
- **插入** text content block（内容为识图结果描述）

```python
# Anthropic 格式重写示例
# 前：
{"role": "user", "content": [
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}},
    {"type": "text", "text": "这张图里有什么？"}
]}

# 后：
{"role": "user", "content": [
    {"type": "text", "text": "[图片描述：一张包含文本和图表的截图...]"},
    {"type": "text", "text": "这张图里有什么？"}
]}
```

## 提示词模板

识图结果包裹为上下文提示：

```
[图片 1/N 的描述：{vision_result}]
```

多图场景带编号，方便目标模型区分：

```
[图片 1/3 的描述：{result_1}]

[图片 2/3 的描述：{result_2}]

[图片 3/3 的描述：{result_3}]
```

## 目标模型请求体构造

根据 `source_format` 构造对应格式的请求体：

```python
def build_target_request(
    self,
    request: ProxyRequest,
    rewritten_messages: list[dict],
    target_config: TargetModelConfig,
) -> dict:
    if request.source_format == "anthropic":
        return {
            "model": target_config.model_id,
            "max_tokens": request.max_tokens,
            "messages": rewritten_messages,
            "stream": request.stream,
            **(target_config.extra_params or {}),
        }
    else:
        return {
            "model": target_config.model_id,
            "max_tokens": request.max_tokens,
            "messages": rewritten_messages,
            "stream": request.stream,
            **(target_config.extra_params or {}),
        }
```

## 限界上下文

- 不调用视觉模型（由 Vision Client 负责）
- 不转发请求（由 Target Client 负责）
- 仅做请求体的图片→文字替换
