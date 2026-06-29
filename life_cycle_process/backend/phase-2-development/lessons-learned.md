# Lessons Learned — 开发经验记录

> 项目: 多模态代理网关 | 更新: 2026-06-28

## A01 — 项目基础设施

### 经验 1: 测试导入路径对齐
- **问题**: pytest 测试中 `from backend.src.app import app` 需要 `backend/__init__.py` 存在，否则 Python 不将 backend 识别为包
- **解决**: 创建空的 `backend/__init__.py` 和 `backend/src/__init__.py`
- **适用**: 所有需要从项目根运行 pytest 的任务

### 经验 2: Docker CMD 模块路径
- **问题**: Dockerfile 的 CMD 路径 `src.main:app` 与本地运行的 `backend.src.main:app` 不同，因为容器内 WORKDIR 直接是 backend 内容
- **解决**: 保持两套路径 — 本地测试用完整包路径，Docker 内用相对路径
- **注意**: 后续 CI 配置需注意 WORKDIR 位置

## A02 — 视觉服务调优与协议兼容

### 经验 3: 视觉模型 image_tokens 按像素计费，压缩字节无效
- **问题**: 双图 compare 调用持续 30s 超时降级为 `[图片无法识别]`。最初以为是网络/认证问题，实测发现根因是任务负载过重
- **根因**:
  - 阿里云百炼视觉模型的 `image_tokens` 按**像素分辨率**线性计算（~0.0013 token/像素），与字节数无关
  - `_compress_image` 当时主要做 PNG→JPEG（省字节）但 `_MAX_DIMENSION=2048` 太宽松，1600 宽的图**根本不缩放**，image_tokens 没真正降下来
  - `vision_timeout=30s` 远不够双图 compare 实际耗时
- **解决**:
  - `_MAX_DIMENSION` 2048→1024（实测双图 image_tokens 2324→1608，1024 级别足以体现代码细节）
  - `vision_timeout` 30→180
  - 超时类失败重试上限收敛到 2 次（任务过重时重试 5 次只会重复超时，纯浪费 wall-clock）
- **验证**: 修复前 211s 超时降级 → 修复后 19.3s 正常识别
- **适用**: 任何调整视觉服务 token/耗时的地方。**先按像素算 token 预估，别被字节数误导**

### 经验 4: 视觉 prompt 必须显式约束任务边界，否则模型跑偏
- **问题**: kimi-k2.5 在双图 compare 任务下不输出对比结论，而是吐了一个 17KB 的 `<!DOCTYPE html>...` 页面，撞 `max_tokens=4096` 上限才停（耗时 119s）
- **根因**: `_build_prompt` 只传 focus（"看什么"），没传角色/输出约束（"你是谁、只做什么、禁止什么"），模型自由发挥跑去生成代码
- **解决**: prompt 前置系统约束 "你是视觉分析器，仅输出图像观察结论，禁止生成代码/HTML/文档/实现方案"，`max_tokens` 4096→1500 截断跑偏
- **验证**: 修复后输出 592 字对比结论，`finish_reason=stop`（自然结束非截断）
- **适用**: 任何调用会"理解任务"的 LLM。focus 描述目标，约束划定边界，缺一不可

### 经验 5: 非对话端点必须透传，不能进 detect_format 白名单
- **问题**: Anthropic SDK 发大请求前会 POST `/v1/messages/count_tokens` 预检 token 数，`detect_format()` 白名单只有 `/v1/messages` 和 `/v1/chat/completions` → `ValueError` → 不被 AppError 处理器捕获 → 500 暴露堆栈
- **关键认知**: count_tokens 响应格式（`{"input_tokens": N}`）与 `/v1/messages` 完全不同，且目标 URL 必须保留 `/count_tokens` 后缀。**不能放行当 anthropic 解析走完整管道**，否则改坏请求
- **解决**: `_run_pipeline` 在解析 JSON 前用 `_is_passthrough_path` 判断 — 非对话端点走 `forward_passthrough`（带 body 原样透传，跳过 vision/rewrite 管道）。**反向判断**：只有两个对话端点走管道，其余 POST 一律透传，未来新增元数据端点自动兜住
- **验证**: 修复前 500 堆栈 → 修复后正常路由到目标（目标返回 401 认证拒绝，是目标侧问题非代理错误）
- **适用**: 代理转发类服务。`detect_format` 这类白名单遇未知路径应走兜底透传而非抛异常，且异常若非 AppError 子类不会被统一处理器捕获 → 会直接 500

### 经验 6: 容器内 Python 版本与本地不一致
- **现象**: 本地 `.pyc` 是 `cpython-314`（Python 3.14），但容器日志堆栈显示 `/usr/local/lib/python3.12/`（Python 3.12）
- **影响**: 本地测试通过不代表容器行为一致。Python 3.14 上 httpx/FastAPI 未经充分测试，容器用 3.12 更稳
- **适用**: 调试时以**容器内实际日志堆栈**为准，本地环境可能误导
