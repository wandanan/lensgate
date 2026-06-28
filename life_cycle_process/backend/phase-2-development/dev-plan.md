# 多模态代理网关 — 开发计划

> 创建时间：2026-06-28 | 基于：PRD + 设计文档 + 系统交互规格

## 项目信息
- 项目：多模态代理网关 (multimodal-proxy)
- 功能域：backend
- 技术栈：Python 3.11+ / FastAPI / httpx (async) / pydantic-settings / structlog

## 任务列表

| # | 任务ID | 标题 | 状态 | 依赖 | 预计产出 |
|---|--------|------|------|------|----------|
| 0 | A01 | 项目基础设施 | ✅ | — | FastAPI app, pyproject.toml, Dockerfile, 入口文件 |
| 1 | A02 | 配置管理 + 数据模型 | ✅ | A01 | config.py, models.py (ProxyRequest/Message/ContentBlock) |
| 2 | B01 | 请求路由 + Format Detector | ✅ | A01, A02 | routes/*.py, format_detector.py |
| 3 | B02 | Image Extractor | ✅ | A02 | image_extractor.py |
| 4 | C01 | Model Router + 健康检查 | ✅ | A02 | model_router.py, GET /health |
| 5 | C02 | Vision Client (Qwen 3.7 Plus) | ✅ | A02, B02 | vision_client.py |
| 6 | C03 | Request Rewriter | ✅ | A02, C02 | request_rewriter.py |
| 7 | C04 | Target Client (Volcengine) | ✅ | A02, C01 | target_client.py |
| 8 | C05 | Response Handler (SSE + JSON) | ✅ | A02, C04 | response_handler.py |
| 9 | C06 | API Key 校验 + 纯文本直通 | ✅ | B01, B02 | middleware/auth.py, pipeline 集成 |
| 10 | C07 | 错误处理 + 降级 | ✅ | C04, C05 | error_handler.py |
| 11 | C08 | 日志 + 监控 | ✅ | A01 | logging_config.py |

状态： ⏳ 待办 | 🔄 进行中 | ✅ 完成 | ⚠️ 低质量通过

## 依赖图

```
A01 (基础)
 ├── A02 (配置+模型)
 │    ├── B01 (路由+格式检测)
 │    │    └── C06 (API Key + 直通)
 │    ├── B02 (图片提取)
 │    │    └── C02 (Vision Client)
 │    │         └── C03 (Request Rewriter)
 │    ├── C01 (Model Router)
 │    │    └── C04 (Target Client)
 │    │         └── C05 (Response Handler)
 │    └── C07 (错误处理)
 └── C08 (日志)
```

## 功能组件清单

1. FastAPI 应用入口 — `/v1/messages` + `/v1/chat/completions` + `GET /health` — A01, B01, C01
2. 配置模型 — ProxyConfig (pydantic-settings) — A02
3. 内部数据模型 — ProxyRequest / Message / ContentBlock — A02
4. Format Detector — Anthropic ↔ OpenAI 格式识别 + 解析 — B01
5. Image Extractor — 检测 image block, base64 decode, URL download — B02
6. Model Router — config-based 目标模型路由 — C01
7. Vision Client — Qwen 3.7 Plus API 封装 (阿里百炼 Coding Plan) — C02
8. Request Rewriter — image block → text block 替换 — C03
9. Target Client — 火山引擎 Coding Plan Anthropic 端点转发 — C04
10. Response Handler — SSE streaming + JSON non-streaming — C05
11. API Key 中间件 — x-api-key 校验 — C06
12. 纯文本直通 — 无图片跳过 Vision/Rewrite — C06
13. 错误处理 — 降级/503/504/重试 — C07
14. 日志 — structlog JSON 输出 + 请求/图片/错误日志 — C08
