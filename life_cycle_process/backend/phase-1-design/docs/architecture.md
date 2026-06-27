# 多模态代理网关 — 架构概览

> 基于：PRD v1.1 | 日期：2026-06-28

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        Claude Code / 开发者                       │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Anthropic /v1/messages 或 OpenAI /v1/chat/completions
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Proxy Gateway (FastAPI)                      │
│                                                                   │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐  │
│  │ Format       │   │ Image        │   │ Vision Service       │  │
│  │ Detector     │──▶│ Extractor    │──▶│ Client               │  │
│  │ (Anthropic/  │   │ (扫描 image  │   │ (Qwen 3.7 Plus       │  │
│  │  OpenAI)     │   │  content)    │   │  阿里云百炼)          │  │
│  └──────────────┘   └──────┬───────┘   └──────────┬───────────┘  │
│                            │                      │               │
│                            ▼                      ▼               │
│                     ┌──────────────┐   ┌──────────────────────┐  │
│                     │ Request      │   │ Target Model         │  │
│                     │ Rewriter     │──▶│ Client               │  │
│                     │ (image→text) │   │ (DeepSeek/GLM 5.2    │  │
│                     └──────────────┘   │  火山引擎)            │  │
│                                        └──────────┬───────────┘  │
│                                                   │               │
│  ┌──────────────────────────────────────────────┐ ▼               │
│  │ Response Handler (SSE stream + JSON)          │                │
│  └──────────────────────────────────────────────┘                │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
              ┌─────────────────────────┐
              │  DeepSeek / GLM 5.2     │
              │  (火山引擎 Coding Plan)  │
              └─────────────────────────┘
```

## 2. 管道阶段

请求经过 7 个管道阶段，纯文本请求跳过阶段 3-5：

| 阶段 | 模块 | 职责 | 纯文本路径 |
|------|------|------|-----------|
| 1 | Format Detector | 识别 Anthropic/OpenAI 格式，解析为内部规范格式 | ✓ |
| 2 | Image Extractor | 扫描 content blocks 中的 image，提取图片数据 | ✓ |
| 3 | Vision Client | 调用 Qwen 3.7 Plus 识图 | ✗ |
| 4 | Request Rewriter | 将 image block 替换为 text block | ✗ |
| 5 | Model Router | 根据配置选择目标模型 endpoint | ✓ |
| 6 | Target Client | 转发请求到 DeepSeek/GLM 5.2 | ✓ |
| 7 | Response Handler | SSE 流式或 JSON 非流式返回 | ✓ |

## 3. 内部规范格式

屏蔽 Anthropic / OpenAI 两种外部格式差异，管道内统一使用规范格式：

```python
# 请求
ProxyRequest(model, messages, stream, max_tokens, temperature)

# 消息
Message(role, content: list[ContentBlock])

# 内容块
TextBlock(text)
ImageBlock(image_data: bytes, media_type: str)
```

## 4. 技术选型

| 层次 | 技术 | 理由 |
|------|------|------|
| HTTP 框架 | FastAPI | 异步支持好，SSE 流式原生支持 |
| HTTP 客户端 | httpx (async) | 与 FastAPI 异步模型一致，支持 SSE |
| 配置管理 | pydantic-settings | 环境变量 + .env 自动加载 |
| 日志 | structlog | 结构化日志，便于监控 |
| 容器化 | Docker | 标准化部署 |

## 5. 错误处理策略

```
识图失败 → 降级为 "[图片无法识别]" 文本 → 继续转发
目标模型 4xx → 透传错误给客户端
目标模型 5xx → 返回 503 + 重试建议
目标模型超时 → 返回 504
请求体解析失败 → 返回 400 + 错误详情
```
