# 多模态代理网关 — 系统交互规格

> 基于：PRD v1.1 + 架构设计 docs/
> 推导时间：2026-06-28

---

## §1 系统概述

多模态代理网关是一个 HTTP 中间件服务。开发者（包括 Claude Code）向它发送 LLM API 请求，代理自动检测请求中的图片、调用视觉模型识图、将图片替换为文字描述后转发目标文本模型。对开发者来说，它"欺骗"了纯文本模型，使其能够处理多模态输入。

核心交互特征：透明代理——开发者使用标准 Anthropic/OpenAI SDK 即可，无需修改代码。

---

## §2 元素-行为矩阵

| 触发方 | 元素 | 输入方式 | 行为 | 状态变化 | 持久化 | 测试表现 |
|--------|------|---------|------|---------|--------|---------|
| 客户端 | `POST /v1/messages` | HTTP POST（Anthropic 格式） | 接收请求，解析为 ProxyRequest | _→ parsing_ | N | `response.status_code == 200` 且 body 含 Anthropic Message 结构 |
| 客户端 | `POST /v1/chat/completions` | HTTP POST（OpenAI 格式） | 接收请求，解析为 ProxyRequest | _→ parsing_ | N | `response.status_code == 200` 且 body 含 OpenAI Chat Completion 结构 |
| 代理 | Image Block（Anthropic） | 解析 `type: "image"` + `source` | 提取 base64 或下载 URL 图片 | _→ extracting_ | N | 图片数据 `len > 0` |
| 代理 | Image Block（OpenAI） | 解析 `type: "image_url"` + `image_url.url` | 提取 data URI 或下载 URL 图片 | _→ extracting_ | N | 图片数据 `len > 0` |
| 代理 | 纯文本请求 | 无图片检测 | 直接转发到目标模型 | _→ forwarding_ | N | 响应延迟 < 有图片时 |
| 代理 | Qwen 3.7 Plus API | HTTP POST（阿里云百炼） | 发送图片，获取文字描述 | _→ recognizing_ | N | 返回非空字符串 |
| 代理 | 重写后请求 | HTTP POST（火山引擎） | 转发给 DeepSeek/GLM 5.2 | _→ forwarding_ | N | `response.status_code == 200` |

---

## §3 键盘/快捷键

**不适用**。本系统是纯后端 API 代理，无 UI 交互。

---

## §4 状态机

### ProxyRequest

```
          POST /v1/messages 或 /v1/chat/completions
                         │
                         ▼
                    ┌─────────┐
                    │ PARSING │── 解析失败 ──▶ 400 返回客户端
                    └────┬────┘
                         │ 解析成功
                         ▼
                  ┌──────────────┐
                  │ IMAGE_CHECK  │
                  └──┬────────┬──┘
            无图片  │        │  有图片
                    │        ▼
                    │  ┌──────────────┐
                    │  │ EXTRACTING   │── 下载失败 ──▶ 降级文本 + 继续
                    │  └──────┬───────┘
                    │         │ 提取成功
                    │         ▼
                    │  ┌──────────────┐
                    │  │ RECOGNIZING  │── API 失败 ──▶ 降级文本 + 继续
                    │  └──────┬───────┘
                    │         │ 识图成功
                    │         ▼
                    │  ┌──────────────┐
                    │  │  REWRITING   │
                    │  └──────┬───────┘
                    │         │
                    └────┬────┘
                         ▼
                  ┌──────────────┐
                  │  FORWARDING  │── 超时/5xx ──▶ 503/504 返回客户端
                  └──────┬───────┘
                         │ 成功
                         ▼
                  ┌──────────────┐
                  │  RESPONDING  │
                  └──────────────┘
```

| 当前态 | 触发事件 | 目标态 | 副作用 | 测试表现 |
|--------|---------|--------|--------|---------|
| PARSING | 请求体 JSON 解析失败 | _→ 400_ | 返回 `{"error": "invalid_request"}` | `response.status_code == 400` |
| PARSING | 不支持的图片格式 | _→ 400_ | 返回格式错误 | `response.status_code == 400` |
| IMAGE_CHECK | 无图片 | → FORWARDING |跳过提取/识图/重写 | 请求耗时 ≈ 目标模型延迟 |
| IMAGE_CHECK | 有图片 | → EXTRACTING | — | 检测到至少 1 个 ImageBlock |
| EXTRACTING | URL 图片下载超时 | → FORWARDING | 注入 `"[图片下载失败]"` | 请求仍返回 200 |
| RECOGNIZING | Qwen API 返回非 200 | → FORWARDING | 注入 `"[图片无法识别]"` | 请求仍返回 200 |
| RECOGNIZING | 识图成功 | → REWRITING | — | 获得非空描述文字 |
| FORWARDING | 目标模型 5xx | _→ 503_ | 返回 `{"error": "target_model_unavailable"}` | `response.status_code == 503` |
| FORWARDING | 目标模型超时 | _→ 504_ | 返回 `{"error": "target_model_timeout"}` | `response.status_code == 504` |
| FORWARDING | 目标模型正常响应 | → RESPONDING | — | `response.status_code == 200` |

---

## §5 数据操作

| 实体 | 操作 | 触发方式 | API/存储动作 | 乐观更新 | 错误处理 | 测试表现 |
|------|------|---------|-------------|---------|---------|---------|
| 图片数据 | 读取 | 检测到 image block | base64 decode 或 HTTP GET 下载 | N/A | 下载失败 → 注入降级文本 | 图片 bytes 非空 |
| 识图结果 | 创建 | 图片提取完成 | `POST dashscope.aliyuncs.com` | N/A | API 失败 → 降级文本 | 返回非空 string |
| 请求体 | 更新 | 识图完成 | 内存中替换 content block | N/A | — | image block 被 text block 替代 |
| 目标模型响应 | 读取 | 转发完成 | httpx HTTP POST → 火山引擎 | N/A | 5xx/超时 → 503/504 | 响应 status 200 |

**无持久化存储。** 所有数据均为请求级别，处理完毕后销毁。

---

## §6 业务规则

| 规则 | 触发时机 | 校验逻辑 | 违反行为 | 测试表现 |
|------|---------|---------|---------|---------|
| API Key 校验 | 请求到达时 | `Authorization` 或 `x-api-key` header 匹配配置值 | 返回 401 | `response.status_code == 401` |
| 图片格式白名单 | 检测到 image block 时 | `media_type` 在 `[png, jpeg, webp, gif]` 中 | 返回 400 + `unsupported_media_type` | `response.status_code == 400` |
| 请求体大小限制 | 接收请求时 | Content-Length < 10MB | 返回 413 | `response.status_code == 413` |
| 目标模型健康检查 | 服务启动时 | GET target API base | 启动失败 | 服务进程退出 |
| 识图降级 | Qwen API 调用时 | 失败不抛异常 | 注入 `"[图片无法识别]"` | 请求仍返回 200 |

---

## §7 非功能行为

### 7.1 错误处理

| 场景 | 行为 | 测试表现 |
|------|------|---------|
| 客户端断连 | 取消目标模型请求，停止流式转发 | 无资源泄漏 |
| 目标模型流式中断 | 发送 `[ERROR]` SSE 事件后关闭连接 | SSE 客户端收到 error 事件 |
| Qwen API 限流 (429) | 等待 1s 重试 1 次，仍失败则降级 | 降级文本仍注入 |
| 配置缺失（无 API Key） | 服务启动时 panic，明确报错 | 进程退出 |

### 7.2 空状态

| 场景 | 行为 | 测试表现 |
|------|------|---------|
| 空 messages 数组 | 返回 400 | `response.status_code == 400` |
| 无文本仅图片的 content | 识图 → 仅转发识图结果 | 目标模型收到纯文本 |

### 7.3 加载态

| 场景 | 行为 | 测试表现 |
|------|------|---------|
| 识图进行中 | SSE 不发送任何事件（等待识图完成） | 客户端连接保持 |
| 目标模型推理中 | SSE 逐 token 转发 | 客户端收到增量内容 |

### 7.4 撤销/重做

**不适用。** 无状态代理，无用户操作历史。
