# 多模态代理网关 (TLMA)

> [English version](README_EN.md)

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
  │  ③ 决策引擎 (DeepSeek Chat) — 选图、定向、single/compare/replicate  │
  │  ④ 视觉模型 (Qwen 3.7 Plus) — 识图 → 文字描述              │
  │  ⑤ 请求重写 (image block → text block)                     │
  │  ⑥ 目标转发 (火山方舟 Coding Plan)                         │
  │  ⑦ 响应返回 (SSE 流式 / JSON)                              │
  │                                                            │
  └── 纯文本请求跳过 ③④⑤，直通 ⑥ ─────────────────────────────┘
```

## 与传统视觉代理方案的区别

传统的视觉代理只是简单的"图片→文字→转发"管道。TLMA 在此基础上做了四个关键设计：

### 1. 三模型协同 vs 两模型串联

```
传统方案:  视觉模型 → 目标模型           (固定串联,每一张图都要调视觉API)
TLMA:      决策引擎 → 视觉模型 → 目标模型  (决策引擎先判断"值不值得调视觉")
```

**决策引擎**在视觉调用前先做一次轻量文本判断(< 0.5s):用户是在追问还是发了新图?这张图需要重识还是用缓存?单图看细节还是双图对比?——决策引擎输出 `mode: single | compare | replicate`,按需触发视觉调用。无图片的纯文本请求连决策引擎都跳过,零额外开销。

### 2. VI-Spec 精确视觉规范 vs 自然语言描述

```
传统方案:  截图 → "暖黄色按钮,大号圆角,浅色背景" → 目标模型猜 #f59e0b? 14px? #f8f7f4?
TLMA:     截图 → :root { --accent: #f59e0b; --radius-md: 14px; --bg-primary: #f8f7f4; } → 直接用
```

**replicate 模式**下,视觉模型被当作"设计测量工具",从截图精确提取 CSS 变量。500 字节的 CSS 消除"暖黄色"→`#f59e0b` 的语义损失。目标模型收到的是精确值,不是模糊描述。适用场景:UI 设计稿→代码、VI 规范复刻、设计评审。

### 3. 智能缓存 vs 无缓存/简单缓存

```
传统方案:  同一张图被反复识图(每次追问都重新调用视觉API)
TLMA:     (图片SHA-256, 聚焦指令) 组合键缓存,同一张图不同视角可分别命中
```

缓存不再是简单的"图片→描述",而是 `(图片哈希, 聚焦指令)` 组合键。用户换一个角度问同一张图("这次看按钮颜色" vs "上次看整体布局"),不会命中旧缓存,而是用新 focus 重新识图。同一视角重复追问则零成本命中。

### 4. 路径级透明路由 vs 固定目标配置

```
传统方案:  代理写死转发到某个模型,换目标要改配置重启
TLMA:     POST /api.deepseek.com/anthropic/v1/messages → 转发到 DeepSeek
          POST /ark.cn-beijing.volces.com/api/coding/v1/messages → 转发到火山方舟
          目标编码在 URL 路径里,换目标不改配置
```

不需要在服务端配置"转发到哪个模型"。客户端把目标主机塞进 URL 路径,代理自动解析、透传认证、保留完整后缀。同一个代理服务可以同时服务多个不同目标模型的客户端。

## 快速开始

> **重要：`.env` 必须放在项目根目录。** 程序启动时按 `根目录 .env` → `backend/.env` 顺序查找，根目录优先。缺失必填 Key 会**启动失败并报错**，不会静默降级。

### Docker（推荐）

```bash
# 1. 在项目根目录创建 .env，填入 API Key
cp backend/.env.example .env
# 编辑 .env：填入 VISION_API_KEY、DECISION_API_KEY
#   ⚠️ 放根目录，不是 backend/ 下

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

# 2. 在项目根目录创建 .env
cp backend/.env.example .env
# 编辑 .env，填入 VISION_API_KEY、DECISION_API_KEY
#   ⚠️ 放根目录，不是 backend/ 下

# 3. 启动
# Git Bash / WSL
PYTHONPATH=. python -m backend.src.main

# PowerShell
$env:PYTHONPATH="."; python -m backend.src.main

# cmd
set PYTHONPATH=. && python -m backend.src.main
```

服务监听 `http://0.0.0.0:9856`。

> **启动失败？** 检查根目录 `.env` 中 `VISION_API_KEY` 和 `DECISION_API_KEY` 是否已填写。缺失任一 Key 都会报错退出，不会被绕过。

## 配置参考

所有配置通过 `.env` 文件或环境变量设置。

| 变量 | 必需 | 默认值 | 说明 |
|------|:--:|--------|------|
| **视觉服务** | | | |
| `VISION_API_KEY` | 是 | — | 阿里云百炼 Coding Plan API Key（`sk-sp-xxxxx` 格式） |
| `VISION_BASE_URL` | 否 | `https://coding.dashscope.aliyuncs.com` | 识图服务地址 |
| `VISION_MODEL` | 否 | `qwen3.7-plus` | 千问视觉模型或别名；可替换为其他兼容模型 |
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
| `qwen3.7-plus` | **默认**。阿里云百炼原生千问视觉模型，适合复杂截图分析 |
| `qwen-default` / `qwen-strong` | `qwen3.7-plus` 的内置别名 |
| `qwen-simple` / `qwen-light` | 轻量千问视觉别名，解析为 `qwen3.6-plus` |
| `kimi-k2.5` | 可选。单图约 8s，双图 compare 约 12s（1024 压缩后） |
| `qwen3.6-plus` | 上代旗舰，较快但识别质量稍弱 |

视觉模型通过阿里云百炼 Coding Plan 调用（`coding.dashscope.aliyuncs.com`），API Key 须为 Coding Plan 专属格式（`sk-sp-` 前缀）。
也可以直接填任意兼容 OpenAI Chat Completions 图片输入格式的视觉模型名，并配套修改 `VISION_BASE_URL`。

TLMA 的定位不是替换目标文本模型，而是为 DeepSeek、GLM、火山方舟 Coding Plan 等强文本模型派生一层图片分析能力：先用轻量视觉模型把图片转为聚焦描述或 VI-Spec，再把结果交给目标文本模型继续推理和生成。

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

### 视觉复刻 (VI-Spec)

当用户说"照着这个做页面""复刻这个设计"时，决策引擎触发 `replicate` 模式。视觉模型不再输出文字描述，而是被当作**设计测量工具**，从截图精确提取 CSS 变量：

```css
--accent: #f59e0b; --radius-md: 14px; --bg-primary: #f8f7f4;
```

目标模型直接使用精确值生成代码，消除"暖黄色"→`#f59e0b` 的语义翻译损失。500 字节 CSS 替代 200 字模糊描述。

### 降级策略

视觉服务不可用时不影响基本使用：
- 超时 / 非 200 / JSON 解析失败 → 返回 `[图片无法识别]` 占位文本
- 请求继续转发给目标模型，目标模型基于文字描述回答

## 模块

| 层 | 模块 | 文件 | 职责 |
|------|------|------|------|
| **core/** | 配置管理 | `core/config.py` | 环境变量 / .env 加载（回退查找根目录→backend/） |
| | 数据模型 | `core/models.py` | ProxyRequest / ImageBlock / ContentBlock |
| | 错误处理 | `core/error_handler.py` | 400/413/503/504 统一异常映射 + 启动校验 |
| | 日志 | `core/logging_config.py` | 结构化日志 (structlog JSON) |
| **pipeline/** | 格式检测 | `pipeline/format_detector.py` | Anthropic / OpenAI 请求解析 |
| | 图片提取 | `pipeline/image_extractor.py` | content blocks 图像提取 + 缓存 |
| | 视觉识别 | `pipeline/vision_client.py` | Qwen / OpenAI-compatible 识图 + 压缩 |
| | 请求重写 | `pipeline/request_rewriter.py` | ImageBlock → TextBlock 替换 |
| | 决策引擎 | `pipeline/decision_engine.py` | 注意力路由（单图/对比/复刻/跳过） |
| | 缓存存储 | `pipeline/cache_store.py` | SHA-256 + focus 组合键缓存 |
| | 目标转发 | `pipeline/target_client.py` | 火山方舟 / DeepSeek 转发 |
| | 响应处理 | `pipeline/response_handler.py` | SSE 流式 + JSON 非流式 |
| **middleware/** | 认证中间件 | `middleware/auth.py` | x-api-key 校验与转发 |
| **tools/** | 请求探针 | `tools/probe.py` | API 请求结构探查 |
| — | 管道编排 | `app.py` | 7 阶段管道 + 纯文本直通 |
| — | 启动入口 | `main.py` | uvicorn server |

## 测试

```bash
# 全部测试
PYTHONPATH=. .venv/Scripts/pytest backend/tests/ -v

# 仅视觉相关
PYTHONPATH=. .venv/Scripts/pytest backend/tests/test_vision_client.py -v

# 仅路由
PYTHONPATH=. .venv/Scripts/pytest backend/tests/test_routes.py -v
```
