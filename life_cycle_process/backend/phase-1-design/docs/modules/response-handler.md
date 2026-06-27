# Response Handler — 响应处理

## 职责

接收目标模型的响应，处理流式/非流式两种模式，将响应返回给原始客户端。

## 非流式响应

```python
async def handle_non_stream(
    target_response: httpx.Response,
    source_format: str,  # "anthropic" | "openai"
) -> JSONResponse:
    """非流式：直接返回目标模型的 JSON 响应"""
    data = target_response.json()

    # 透传即可——目标模型返回的已是 OpenAI 格式
    # Anthropic 格式客户端需要格式转换
    if source_format == "anthropic":
        return openai_to_anthropic(data)
    return JSONResponse(data)
```

## SSE 流式响应

```python
async def handle_stream(
    target_stream,          # async iterator of SSE lines
    source_format: str,
) -> StreamingResponse:
    """流式：逐行转发 SSE 事件"""
    return StreamingResponse(
        _stream_generator(target_stream, source_format),
        media_type="text/event-stream",
    )

async def _stream_generator(target_stream, source_format):
    async for line in target_stream:
        if source_format == "anthropic":
            # OpenAI SSE chunk → Anthropic SSE event
            line = convert_sse_chunk(line)
        yield line
```

## Anthropic SSE 事件格式

Anthropic 的 SSE 事件类型：

| 事件类型 | 说明 |
|----------|------|
| `message_start` | 消息开始 |
| `content_block_start` | 内容块开始（text/thinking/tool_use） |
| `content_block_delta` | 内容增量 |
| `content_block_stop` | 内容块结束 |
| `message_delta` | 消息级更新（stop_reason, usage） |
| `message_stop` | 消息结束 |

OpenAI SSE chunk → Anthropic SSE event 的映射：

```
OpenAI chunk.choices[0].delta.content →
  Anthropic content_block_delta (text_delta)

OpenAI chunk.choices[0].finish_reason →
  Anthropic message_delta (stop_reason) + message_stop
```

## 格式转换：OpenAI → Anthropic

```python
def openai_to_anthropic(openai_response: dict) -> dict:
    """OpenAI Chat Completion → Anthropic Message"""
    choice = openai_response["choices"][0]
    return {
        "id": f"msg_{uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": openai_response.get("model", ""),
        "content": [
            {
                "type": "text",
                "text": choice["message"]["content"]
            }
        ],
        "stop_reason": _map_stop_reason(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": {
            "input_tokens": openai_response.get("usage", {}).get("prompt_tokens", 0),
            "output_tokens": openai_response.get("usage", {}).get("completion_tokens", 0),
        }
    }
```

## 纯文本直通优化

无图片的请求跳过管道阶段 3-5，直接转发。Response Handler 无需做任何处理：

```python
if not has_images(request):
    # 直通路径
    config = router.resolve(request.target_model)
    if request.stream:
        return await handle_stream(
            target_client.forward_stream(request.original_body, config),
            request.source_format,
        )
    else:
        resp = await target_client.forward(request.original_body, config)
        return await handle_non_stream(resp, request.source_format)
```

## 限界上下文

- 不解析请求（由 Format Detector 负责）
- 不调用目标模型（由 Target Client 负责）
- 仅负责响应流式/非流式返回 + 必要的格式转换
