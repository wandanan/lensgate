# LIFECYCLE — backend

> 项目: 多模态代理网关 (multimodal-proxy)
> 域: backend
> 最后更新: 2026-06-28

## 阶段状态

| 阶段 | 状态 | 完成时间 |
|------|------|---------|
| Phase 0 — 需求 | ✅ 完成 | 2026-06-28 |
| Phase 1 — 设计 | ✅ 完成 | 2026-06-28 |
| Phase 2 — 开发 | 🔄 进行中 | — |
| Phase 3 — 迭代 | ⏳ 待开始 | — |

## 产物索引

| 阶段 | 文件 | 说明 |
|------|------|------|
| Phase 0 | `phase-0-prd/multimodal-proxy-spec.md` | PRD v1.1 |
| Phase 1 | `phase-1-design/docs/architecture.md` | 架构概览 |
| Phase 1 | `phase-1-design/docs/modules/format-detector.md` | 格式检测模块 |
| Phase 1 | `phase-1-design/docs/modules/image-extractor.md` | 图片提取模块 |
| Phase 1 | `phase-1-design/docs/modules/vision-client.md` | 视觉服务客户端 |
| Phase 1 | `phase-1-design/docs/modules/request-rewriter.md` | 请求体重写模块 |
| Phase 1 | `phase-1-design/docs/modules/target-client.md` | 目标模型客户端 |
| Phase 1 | `phase-1-design/docs/modules/response-handler.md` | 响应处理模块 |
| Phase 1 | `phase-1-design/docs/data-contracts.md` | 数据契约 |
| Phase 1 | `phase-1-design/docs/api-integration-reference.md` | 第三方 API 集成参考 |
| Phase 1 | `phase-1-design/system-interaction-spec.md` | 系统交互规格 |

## 关键决策

- 后端框架: FastAPI (async)
- HTTP 客户端: httpx (async)
- 内部规范格式: 屏蔽 Anthropic/OpenAI 差异
- 目标模型转发: 直接使用 Anthropic 格式（Coding Plan 提供 Anthropic 兼容端点）
- 纯文本直通: 无图片时跳过识图管道
- 识图降级: 失败不阻断请求

## 依赖

- 阿里云百炼 Coding Plan (Qwen 3.7 Plus — 识图)
- 火山引擎 Coding Plan (DeepSeek / GLM — 文本推理)
