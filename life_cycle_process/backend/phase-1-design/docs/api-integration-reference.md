# API 集成参考 — 第三方服务

> 基于：2026-06-28 联网搜索 | 持续更新

---

## 1. 火山引擎 Coding Plan（目标模型）

### 1.1 两种 API 协议

| 协议 | Base URL | 适用场景 |
|------|----------|---------|
| Anthropic 兼容 | `https://ark.cn-beijing.volces.com/api/coding` | Claude Code 等 Anthropic 协议工具 |
| OpenAI 兼容 | `https://ark.cn-beijing.volces.com/api/coding/v3` | OpenAI SDK 等工具 |

**注意：** 必须使用 Coding Plan 专属 Base URL。普通 API endpoint (`/api/v3`) 不会消耗套餐额度，会产生额外按量费用。

### 1.2 Claude Code 环境变量配置

```bash
ANTHROPIC_BASE_URL=https://ark.cn-beijing.volces.com/api/coding
ANTHROPIC_AUTH_TOKEN=<方舟 API Key>
ANTHROPIC_MODEL=ark-code-latest  # 或具体模型名
```

### 1.3 可用模型（2026.06）

| 模型 | 类型 | 说明 |
|------|------|------|
| DeepSeek-V3.2 | 文本推理 | 常规代码/对话 |
| DeepSeek-V4-Flash | 文本推理 | 快速响应版 |
| DeepSeek-V4-Pro | 文本推理 | 深度推理版 |
| GLM-5.1 | 文本推理 | 智谱最新（注意：文档显示为 5.1 而非 5.2） |
| GLM-4.7 | 文本推理 | 智谱前代 |
| Kimi-K2.5 / K2.6 | 文本推理 | Moonshot |
| Doubao-Seed-2.0-Code | 文本推理 | 字节豆包 |
| MiniMax-M2.7 | 文本推理 | MiniMax |

### 1.4 认证方式

```
Header: Authorization: Bearer {API_KEY}
```

或 Anthropic 协议下：
```
Header: x-api-key: {API_KEY}
Header: anthropic-version: 2023-06-01
```

### 1.5 请求示例（OpenAI 兼容）

```bash
curl https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-v3.2",
    "messages": [
      {"role": "user", "content": "Hello"}
    ],
    "stream": true
  }'
```

### 1.6 Proxy 转发策略

由于 Coding Plan 提供 **Anthropic 兼容端点**，Proxy 可在剥离图片后**保持 Anthropic 格式直接转发**，无需格式转换：

```
Claude Code → Proxy (Anthropic /v1/messages)
  → 检测图片 → Qwen 识图 → 替换 image block
  → 转发 Volcengine Coding Plan Anthropic 端点
  → 返回 Anthropic 格式响应
```

---

## 2. 阿里云百炼 Coding Plan（视觉模型）

### 2.1 两种 API 协议

| 协议 | Base URL | 说明 |
|------|----------|------|
| OpenAI 兼容 | `https://coding.dashscope.aliyuncs.com/v1` | 推荐用于识图调用 |
| Anthropic 兼容 | `https://coding.dashscope.aliyuncs.com/apps/anthropic` | — |

**注意：** 必须使用 Coding Plan 专属 API Key（`sk-sp-xxxxx` 格式）和专属 Base URL。百炼通用 API Key（`sk-xxxxx`）和 Base URL（`dashscope.aliyuncs.com`）不消耗套餐额度。

### 2.2 支持图片理解的模型

| 模型 | 说明 |
|------|------|
| **qwen3.7-plus** | 最新旗舰，支持图片理解 |
| qwen3.6-plus | 支持图片理解 |
| qwen3.5-plus | 支持图片理解 |
| qwen3-vl-plus | 专门视觉模型（Coding Plan 未列出，需确认） |

### 2.3 认证方式

```
Header: Authorization: Bearer {CODING_PLAN_API_KEY}
```

API Key 格式：`sk-sp-xxxxx`

### 2.4 识图请求示例

```bash
curl https://coding.dashscope.aliyuncs.com/v1/chat/completions \
  -H "Authorization: Bearer $CODING_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.7-plus",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/png;base64,iVBORw0KGgo..."
            }
          },
          {
            "type": "text",
            "text": "请详细描述这张图片中的所有内容，包括文字、物体、人物、场景、颜色、布局等。"
          }
        ]
      }
    ],
    "max_tokens": 2000
  }'
```

### 2.5 响应格式

标准 OpenAI Chat Completion：
```json
{
  "choices": [
    {
      "message": {
        "content": "这张图片显示了..."
      }
    }
  ],
  "usage": {
    "prompt_tokens": 500,
    "completion_tokens": 200
  }
}
```

---

## 3. Proxy 完整数据流

```
┌─────────────┐     Anthropic /v1/messages      ┌──────────────────┐
│  Claude Code │ ──────────────────────────────▶ │  Proxy Gateway   │
│  (开发者)     │ ◀────────────────────────────── │  :8080            │
└─────────────┘     Anthropic SSE/JSON            └──────┬───────────┘
                                                         │
                              图片检测 ─── 有图片? ──────┤
                                                         │
                    ┌────────────────────────────────────┤
                    ▼                                    │
          ┌──────────────────┐                          │
          │ Qwen 3.7 Plus    │                          │
          │ 阿里 Coding Plan  │                          │
          │ model: qwen3.7-plus                          │
          │ url: coding.dashscope.aliyuncs.com/v1        │
          └────────┬─────────┘                          │
                   │ 文字描述                            │
                   ▼                                    │
          ┌──────────────────┐                          │
          │ 请求体重写        │                          │
          │ image → text      │                          │
          └────────┬─────────┘                          │
                   │                                    │
                   ▼                                    ▼
          ┌──────────────────────────────────────────────┐
          │          Volcengine Coding Plan               │
          │  url: ark.cn-beijing.volces.com/api/coding    │
          │  model: deepseek-v3.2 / glm-5.1               │
          │  (Anthropic 兼容协议，直接转发)                 │
          └──────────────────────────────────────────────┘
```

## 4. 关键约束

| 约束 | 说明 |
|------|------|
| Coding Plan 限流 | 每 5 小时滑动窗口 + 每月总次数限制 |
| API Key 隔离 | Coding Plan Key 不能用于普通 API 端点，反之亦然 |
| 模型名精确匹配 | 必须使用 Coding Plan 支持的确切模型名 |
| 图片格式 | PNG/JPEG/WebP/GIF，base64 编码或 HTTPS URL |
