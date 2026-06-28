# 多模态代理网关

为纯文本 LLM（DeepSeek、GLM）提供透明多模态代理层。Claude Code 配置自定义 API endpoint 指向本代理后，代理自动拦截图片内容，由 Qwen 3.7 Plus 识图后转为文字描述，再转发文本模型。

## 架构

```
Claude Code → POST /v1/messages
  ├── 纯文本 → 直通转发 → 火山引擎 Coding Plan → 返回
  └── 含图片 → Qwen 3.7 Plus 识图 → 文本替换 → 火山引擎 → 返回
```

## 快速启动

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt
```

**环境变量：**

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `PROXY_API_KEY` | 是 | — | 代理自身 API Key（Claude Code 配置此项） |
| `VISION_API_KEY` | 是 | — | 阿里云百炼 Coding Plan API Key |
| `TARGET_DEFAULT_API_KEY` | 是 | — | 火山引擎 Coding Plan API Key |

**启动代理网关：**

```bash
# Git Bash / WSL bash
PYTHONPATH=. python -m backend.src.main

# PowerShell
$env:PYTHONPATH="."; python -m backend.src.main

# cmd
set PYTHONPATH=. && python -m backend.src.main
```

**启动探查工具**（捕获 Claude Code 实际请求体）：

```bash
# Git Bash
PYTHONPATH=. python -m backend.src.probe

# PowerShell
$env:PYTHONPATH="."; python -m backend.src.probe

# cmd
set PYTHONPATH=. && python -m backend.src.probe
```

探查工具监听 `http://127.0.0.1:9856/v1/messages`，请求体写入 `dev/probe_requests_.jsonl`。将 Claude Code 供应商配置指向该地址即可收集真实请求体。

## 完整环境变量

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `PROXY_API_KEY` | 是 | — | 代理自身 API Key |
| `VISION_API_KEY` | 是 | — | 阿里云百炼 Coding Plan API Key |
| `VISION_BASE_URL` | 否 | `https://coding.dashscope.aliyuncs.com` | 识图服务地址 |
| `VISION_MODEL` | 否 | `qwen3.7-plus` | 识图模型 |
| `VISION_TIMEOUT` | 否 | `30` | 识图超时（秒） |
| `TARGET_DEFAULT_API_KEY` | 是 | — | 火山引擎 Coding Plan API Key |
| `TARGET_DEFAULT_BASE_URL` | 否 | `https://ark.cn-beijing.volces.com/api/coding` | 目标模型地址 |
| `TARGET_DEFAULT_MODEL` | 否 | `deepseek-v3.2` | 默认目标模型 |
| `HOST` | 否 | `0.0.0.0` | 监听地址 |
| `PORT` | 否 | `8000` | 监听端口 |

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 服务信息 |
| GET | `/health` | 健康检查 |
| POST | `/v1/messages` | Anthropic Messages API（主要） |
| POST | `/v1/chat/completions` | OpenAI Chat Completions API（兼容） |

认证：`x-api-key` header（与 `PROXY_API_KEY` 一致）。

## 模块

| 模块 | 文件 | 职责 |
|------|------|------|
| 格式检测 | `format_detector.py` | Anthropic / OpenAI 请求解析 |
| 图片提取 | `image_extractor.py` | base64 解码 + URL 下载 |
| 视觉识别 | `vision_client.py` | Qwen 3.7 Plus 识图 |
| 请求重写 | `request_rewriter.py` | ImageBlock → TextBlock 替换 |
| 模型路由 | `model_router.py` | 目标模型匹配 |
| 目标转发 | `target_client.py` | 火山引擎 Coding Plan 转发 |
| 响应处理 | `response_handler.py` | SSE 流式 + JSON 非流式 |
| 管道编排 | `pipeline.py` | 7 阶段管道 + 纯文本直通 |
| 认证中间件 | `middleware/auth.py` | x-api-key 校验 |
| 错误处理 | `error_handler.py` | 400/413/503/504 异常映射 |
| 日志 | `logging_config.py` | structlog JSON 输出 |

## 测试

```bash
# Git Bash
PYTHONPATH=. .venv/Scripts/pytest backend/tests/ -v

# PowerShell
$env:PYTHONPATH="."; .venv/Scripts/pytest backend/tests/ -v

# cmd
set PYTHONPATH=. && .venv/Scripts/pytest backend/tests/ -v
```
