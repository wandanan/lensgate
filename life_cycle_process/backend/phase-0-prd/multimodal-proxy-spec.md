# 多模态代理网关 — PRD

> 版本: 1.1 | 日期: 2026-06-28 | 域: backend

---

## 1. 产品定位

为仅支持文本的 LLM（DeepSeek、GLM 5.2）提供透明多模态代理层。**主要场景：Claude Code 用户使用纯文本模型时，代理自动拦截 Anthropic Messages API 请求中的图片内容，由视觉模型（Qwen 3.7 Plus）识图后转为文字描述，注入请求体并转发文本模型。** 同时兼容 OpenAI Chat Completions 格式。

核心价值：纯文本模型收到图片（base64 编码等）会直接报 400 错误，导致 Claude Code 的绘图/识图功能废掉。代理层在中间拦截并转换，让纯文本模型"看懂"图片。

---

## 2. 目标用户

- **Claude Code 开发者**：Claude Code 配置自定义 API endpoint 指向代理，使用 DeepSeek/GLM 等纯文本模型进行带图片的多模态对话
- **后端/AI 应用开发者**：通过 Anthropic Messages API 或 OpenAI Chat Completions API 调用代理

---

## 3. 核心功能清单

### P0 — 最小可用（Anthropic 格式优先）

| ID | 功能 | 说明 |
|----|------|------|
| F01 | 代理服务启动与路由 | HTTP 服务，接收 Anthropic `/v1/messages` 格式请求（兼容 OpenAI `/v1/chat/completions`） |
| F02 | 图片检测与拦截 | 解析 Anthropic Messages 请求体 `messages[].content` 数组，识别 `type: "image"` 的图片内容（base64 内联 + URL 两种 source 格式） |
| F03 | 视觉模型识图 | 将图片转发到 Qwen 3.7 Plus (阿里云百炼)，获取图片文字描述 |
| F04 | 请求体重写 | 将 Anthropic `image` content block 替换为 `text` block（识图结果），再转发目标文本模型 |
| F05 | 目标模型转发 | 将重写后的请求转发到 DeepSeek / GLM 5.2 (火山引擎)，流式/非流式均支持 |
| F06 | 纯文本直通 | 请求中无图片时，直接透传请求到目标模型，零开销 |

### P1 — 生产就绪

| ID | 功能 | 说明 |
|----|------|------|
| F07 | 模型路由配置 | 支持配置多个目标模型的路由规则（API endpoint / key / model mapping） |
| F08 | 流式响应支持 | SSE (Server-Sent Events) 流式转发，支持 Anthropic SSE 事件流和 OpenAI SSE 格式 |
| F09 | 请求日志与监控 | 记录代理请求量、识图耗时、转发耗时、错误率 |
| F10 | 错误处理与降级 | 识图失败时降级为文本提示，目标模型不可用时返回明确错误 |

### P2 — 体验增强

| ID | 功能 | 说明 |
|----|------|------|
| F11 | 多图并行识图 | 单条消息多张图片时并行调视觉模型，减少等待时间 |
| F12 | 对话历史优化 | 多轮对话中重复图片只识图一次，缓存结果 |
| F13 | API Key 管理 | 内置 API Key 管理，开发者只需一个 Proxy Key |
| F14 | 全格式兼容 | 同时支持 Anthropic Messages API + OpenAI Chat Completions API 两种请求/响应格式 |

---

## 4. 请求格式说明

### 4.1 Anthropic Messages API（主要支持格式）

端点：`POST /v1/messages`

Headers：
```
Content-Type: application/json
x-api-key: <proxy-key>
anthropic-version: 2023-06-01
```

图片在 content 数组中的格式：

```json
// base64 内联图片
{
  "type": "image",
  "source": {
    "type": "base64",
    "media_type": "image/png",
    "data": "<base64-string>"
  }
}

// URL 图片
{
  "type": "image",
  "source": {
    "type": "url",
    "url": "https://example.com/image.png"
  }
}
```

### 4.2 OpenAI Chat Completions API（兼容格式）

端点：`POST /v1/chat/completions`

```json
{
  "type": "image_url",
  "image_url": {
    "url": "data:image/png;base64,<base64>" 
    // 或 "url": "https://example.com/image.png"
  }
}
```

---

## 5. 非功能需求

### 5.1 性能
- 纯文本透传延迟 < 50ms（相比直连目标模型）
- 识图 + 转发总延迟 < 3s（单张图片 1024px 以内）
- 支持并发 50 请求/秒

### 5.2 可靠性
- 识图失败不导致整个请求失败（降级为"[图片无法识别]"提示文本）
- 目标模型不可用时返回 503 + 明确错误信息
- 请求超时 60s

### 5.3 兼容性
- 请求格式：Anthropic `/v1/messages` + OpenAI `/v1/chat/completions`
- 响应格式：Anthropic Message / Message Stream (SSE) + OpenAI Chat Completion / Chat Completion Chunk (SSE)
- 图片格式：PNG / JPEG / WebP / GIF（对齐 Qwen 3.7 Plus 能力）

---

## 6. 技术约束

- **后端语言**：Python（FastAPI / aiohttp）
- **部署方式**：单进程服务，支持 Docker 容器化
- **外部依赖**：
  - 阿里云百炼 API（Qwen 3.7 Plus — 识图）
  - 火山引擎 API（DeepSeek + GLM 5.2 — 文本推理）
- **存储**：无数据库，纯代理转发
- **配置**：环境变量 + 配置文件管理多模型路由

---

## 7. 数据流

```
Claude Code / 开发者请求 → Proxy →
  ├── 无图片 → 直通目标模型 → 返回
  └── 有图片（Anthropic image block 或 OpenAI image_url block）→
        提取图片 → Qwen 3.7 Plus 识图 →
        图片 block 替换为 text block（描述文字）→
        请求体重写完成 → 目标模型推理 →
        返回（保持原请求格式）
```
