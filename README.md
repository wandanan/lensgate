# 多模态代理网关 (TLMA)

为纯文本 LLM（如DeepSeek、GLM）提供透明多模态代理层。代理自动拦截图片、识图转为文字描述、再转发给文本模型，让纯文本模型也能"看见"图片。

决策引擎根据用户意图自动路由视觉注意力：单图描述、多图对比、或跳过无关历史图。

## 架构

```
Claude Code ──POST /api.deepseek.com/anthropic/v1/messages──▶ TLMA :9856
                                                               │
  ┌──────────────────── 管道 ────────────────────────────────┤
  │                                                            │
  │  ① 格式检测 (Anthropic/OpenAI)                              │
  │  ② 图片提取 + 缓存查询                                     │
  │  ③ 决策引擎 (DeepSeek Chat) — 选图、定向、single/compare  │
  │  ④ 视觉模型 (Kimi-K2.5) — 识图 → 文字描述                  │
  │  ⑤ 请求重写 (image block → text block)                     │
  │  ⑥ 目标转发 (火山方舟 Coding Plan)                         │
  │  ⑦ 响应返回 (SSE 流式 / JSON)                              │
  │                                                            │
  └── 纯文本请求跳过 ③④⑤，直通 ⑥ ─────────────────────────────┘
```

## 快速开始

### Docker（推荐）

```bash
# 1. 编辑 .env，填入 API Key
cp backend/.env.example .env
# 编辑 .env：填入 VISION_API_KEY、DECISION_API_KEY

# 2. 构建镜像 + 启动容器
bash docker/build-local.sh

# 3. 验证
curl http://localhost:9856/health
# → {"status":"ok","version":"1.0.0",...}
```

### 本地开发

```bash
# 1. 虚拟环境
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt   # Windows
# source .venv/bin/pip install -r backend/requirements.txt  # Linux/macOS

# 2. 环境变量
cp backend/.env.example .env
# 编辑 .env，填入 VISION_API_KEY、DECISION_API_KEY

# 3. 启动
# Git Bash / WSL
PYTHONPATH=. python -m backend.src.main

# PowerShell
$env:PYTHONPATH="."; python -m backend.src.main

# cmd
set PYTHONPATH=. && python -m backend.src.main
```

服务监听 `http://0.0.0.0:9856`。

## 配置参考

所有配置通过 `.env` 文件或环境变量设置。

| 变量 | 必需 | 默认值 | 说明 |
|------|:--:|--------|------|
| **视觉服务** | | | |
| `VISION_API_KEY` | 是 | — | 阿里云百炼 Coding Plan API Key（`sk-sp-xxxxx` 格式） |
| `VISION_BASE_URL` | 否 | `https://coding.dashscope.aliyuncs.com` | 识图服务地址 |
| `VISION_MODEL` | 否 | `qwen3.7-plus` | 识图模型（推荐 `kimi-k2.5`） |
| `VISION_TIMEOUT` | 否 | `180` | 识图超时（秒）。双图 compare 任务需要较长时间 |
| **决策引擎** | | | |
| `DECISION_API_KEY` | 是 | — | DeepSeek API Key |
| `DECISION_BASE_URL` | 否 | `https://api.deepseek.com/v1` | 决策模型地址 |
| `DECISION_MODEL` | 否 | `deepseek-chat` | 决策模型 |
| `DECISION_TIMEOUT` | 否 | `5` | 决策超时（秒）。纯文本快模型 |
| **代理服务** | | | |
| `PROXY_API_KEY` | 否 | `""` | 代理自身认证 Key。留空则不校验 |
| `PROXY_HOST` | 否 | `0.0.0.0` | 监听地址 |
| `PROXY_PORT` | 否 | `9856` | 监听端口 |

### 最小 `.env` 示例

```env
VISION_API_KEY=sk-sp-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DECISION_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

仅这两个必填。其余字段有合理默认值。

### 视觉模型选择

| 模型 | 说明 |
|------|------|
| `kimi-k2.5` | **推荐**。单图约 8s，双图 compare 约 12s（1024 压缩后） |
| `qwen3.7-plus` | 阿里云百炼原生视觉模型。thinking 模型，单图约 27s，适合复杂分析 |
| `qwen3.6-plus` | 上代旗舰，较快但识别质量稍弱 |

视觉模型通过阿里云百炼 Coding Plan 调用（`coding.dashscope.aliyuncs.com`），API Key 须为 Coding Plan 专属格式（`sk-sp-` 前缀）。

## 使用方式

### Claude Code 配置

**只改一个地方：** 把原来的 API 地址前面加上 `http://localhost:9856/`，其他全部不变。

```
原来：https://api.deepseek.com/anthropic
改成：http://localhost:9856/api.deepseek.com/anthropic
```

就这一处。API Key、模型名称、其他所有配置都不用动。

```bash
claude config set anthropic_base_url http://localhost:9856/api.deepseek.com/anthropic
```

如果你的 Claude Code 原来用的是别的源，同理：

| 你原来的地址 | 改成 |
|-------------|------|
| `https://api.deepseek.com/anthropic` | `http://localhost:9856/api.deepseek.com/anthropic` |
| `https://ark.cn-beijing.volces.com/api/coding` | `http://localhost:9856/ark.cn-beijing.volces.com/api/coding` |

格式就是：`http://localhost:9856/` + 去掉 `https://` 的原地址。

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `POST` | `/v1/messages` | Anthropic Messages API — 识图管道 |
| `POST` | `/v1/chat/completions` | OpenAI Chat Completions API — 识图管道 |
| `POST` | `/v1/messages/count_tokens` | Token 计数 — 透传 |
| `GET/HEAD/OPTIONS` | `任意路径` | 透传到目标 |

认证：`x-api-key` header（或 `Authorization: Bearer <key>`）。若 `PROXY_API_KEY` 为空，不校验代理层认证——`x-api-key` 仅作为目标转发 Key。

## 管道详解

### 决策引擎：智能注意力路由

每次请求到达时，决策引擎判断：
- 哪些图片需要重识？（通过缓存中的 SHA-256 哈希匹配）
- 关注什么？（生成聚焦指令给视觉模型）
- 单图还是对比？（`mode: single` 或 `compare`）

不触发决策引擎的情况：
- 纯文本请求 + 缓存为空 → **直通**，零额外开销
- 新图片 + 缓存为空 → 默认全量识图

### 缓存机制

识图结果按 `(图片SHA-256哈希, 聚焦指令)` 缓存。同一张图、同一个视角的重复请求直接命中缓存，不重复调用视觉 API。

### 多图对比

当决策引擎判定为 `compare` 模式时，多张图片在**一次**视觉 API 调用中发送，让模型在图像 token 间做交叉注意力，实现真正的跨图对比分析。

### 降级策略

视觉服务不可用时不影响基本使用：
- 超时 / 非 200 / JSON 解析失败 → 返回 `[图片无法识别]` 占位文本
- 请求继续转发给目标模型，目标模型基于文字描述回答

## 模块

| 模块 | 文件 | 职责 |
|------|------|------|
| 配置管理 | `config.py` | 环境变量 / .env 加载 |
| 格式检测 | `format_detector.py` | Anthropic / OpenAI 请求解析 |
| 图片提取 | `image_extractor.py` | content blocks 图像提取 + 缓存 |
| 视觉识别 | `vision_client.py` | Kimi-K2.5 / Qwen 识图 + 压缩 |
| 请求重写 | `request_rewriter.py` | ImageBlock → TextBlock 替换 |
| 决策引擎 | `decision_engine.py` | 注意力路由（单图/对比/跳过） |
| 缓存存储 | `cache_store.py` | SHA-256 + focus 组合键缓存 |
| 目标转发 | `target_client.py` | 火山方舟 / DeepSeek 转发 |
| 响应处理 | `response_handler.py` | SSE 流式 + JSON 非流式 |
| 认证中间件 | `middleware/auth.py` | x-api-key 校验与转发 |
| 错误处理 | `error_handler.py` | 400/413/503/504 统一异常映射 |
| 日志 | `logging_config.py` | 结构化日志 |
| 管道编排 | `app.py` | 7 阶段管道 + 纯文本直通 |

## 测试

```bash
# 全部测试
PYTHONPATH=. .venv/Scripts/pytest backend/tests/ -v

# 仅视觉相关
PYTHONPATH=. .venv/Scripts/pytest backend/tests/test_vision_client.py -v

# 仅路由
PYTHONPATH=. .venv/Scripts/pytest backend/tests/test_routes.py -v
```
