# 决策模型架构 — 逼近原生多模态能力

> 2026-06-28 | 基于 Phase 2 实测 & 设计评审修复

---

## 1. 设计原理

### 1.1 原生多模态模型的核心机制

```
用户输入 ──→ [文本 token] ──→ Attention(Q,K,V) ──→ 输出 token
                [图像 token]

Attention 计算: 每个 token 和所有 token 计算相似度, 图像 token 天然参与全上下文。
```

### 1.2 文本特征替代图像特征的可行性

```
图像 → 文本描述 → 嵌入向量
     ↓
文本描述在语义空间中与原始图像的视觉-语义投影近似。
注意力计算的本质是向量相似度 — 文本描述向量 ≈ 图像语义向量。
因此: 用文本描述参与注意力路由, 在决策层面可以近似图像 token 的计算。
```

### 1.3 三模型协同架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                      │
│  文本模型 (决策)     视觉模型 (识图)      目标模型 (推理)               │
│  ────────────────    ────────────────     ────────────────            │
│  角色: 注意力路由    角色: 像素 → 文字     角色: 最终回答               │
│  能力: 语义理解      能力: 视觉感知         能力: 文本推理               │
│  速度: <0.5s        速度: 8-12s            速度: 流式                  │
│  成本: ~0            成本: 图片大小         成本: 按 token              │
│                                                                      │
│  做什么:              做什么:                做什么:                    │
│  · 用户问的是哪张图?   · 图像中有哪些细节?     · 根据描述推理回答         │
│  · 需要识图还是直通?   · 多图之间如何对比?                             │
│  · 应该聚焦什么细节?                                                  │
│                                                                      │
│  类比: 大脑的注意系统  类比: 眼睛的视觉系统   类比: 大脑的推理系统       │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. 数据流

```
                      Claude Code 请求
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                        TLMA 代理                              │
│                                                               │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────┐     │
│  │ 格式检测     │───→│ 图片扫描      │───→│ 缓存检查      │     │
│  │ detect      │    │ scan images  │    │ cache lookup │     │
│  └─────────────┘    └──────┬───────┘    └──────┬───────┘     │
│                            │                   │              │
│                      ┌─────┴─────┐       ┌─────┴─────┐       │
│                      │ 最新消息   │       │ 历史消息   │       │
│                      │ 有图       │       │ 有图(缓存) │       │
│                      └─────┬─────┘       └─────┬─────┘       │
│                            │                   │              │
│                            ▼                   ▼              │
│                   ┌──────────────────────────────────┐       │
│                   │       决策模型 (文本, 轻量)        │       │
│                   │                                  │       │
│                   │  触发条件: 有图片 或 有缓存         │       │
│                   │  纯文本无缓存 → 短路跳过(0 开销)    │       │
│                   │                                  │       │
│                   │  输入 (~500 token, <0.5s):       │       │
│                   │    · 最近 N 条用户消息 (默认5条)    │       │
│                   │    · 全部缓存图片摘要 (含文件名)    │       │
│                   │    · 全部图片哈希列表              │       │
│                   │    · 最近 2 轮对话                │       │
│                   │                                  │       │
│                   │  输出:                            │       │
│                   │    · action: re_vision | skip    │       │
│                   │    · image_hashes: ["a1","b2"]   │       │
│                   │    · focus_prompt: "..."         │       │
│                   │    · mode: single | compare      │       │
│                   └──────────┬───────────────────────┘       │
│                              │                                │
│                    ┌─────────┴─────────┐                      │
│                    ▼                   ▼                      │
│               re_vision             skip                     │
│                    │                   │                      │
│                    ▼                   │                      │
│  ┌──────────────────────────────┐      │                      │
│  │ 视觉模型 (Qwen 3.7 Plus)      │      │                      │
│  │ prompt = focus_prompt        │      │                      │
│  │ per-hash lock 防缓存击穿      │      │                      │
│  │ Semaphore(20) 限并发          │      │                      │
│  │ 结果更新缓存                  │      │                      │
│  └──────────────┬───────────────┘      │                      │
│                 │                      │                      │
│                 ▼                      ▼                      │
│         ┌──────────────────────────────────┐                  │
│         │         Rewrite + Forward         │                  │
│         │  图片→文本, 多图→对比文本          │                  │
│         │  转发目标模型 (纯文本)              │                  │
│         └──────────────────────────────────┘                  │
│                                                               │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
                     目标模型响应
```

---

## 3. 决策模型规范

### 3.1 模型选择

| 候选模型 | 速度 | 成本 | 推理能力 | 推荐 |
|----------|------|------|----------|------|
| DeepSeek Chat (V3) | <0.3s | ~0 | 强 | ✅ 首选 |
| Qwen Turbo | <0.3s | ~0 | 中 | 备选 |
| GLM 4 Flash | <0.3s | ~0 | 中 | 备选 |
| kimi-k2.5 | ~1s | 低 | 强 | 过重 |

### 3.2 触发条件

决策模型**不是每次请求都调用**，仅在以下条件之一满足时触发：

| 条件 | 说明 |
|------|------|
| 请求体中有图片（任意位置） | 需要判断识图策略 |
| 缓存中有已识别的图片 | 用户可能追问历史图片 |

纯文本请求且缓存为空时，**短路跳过决策引擎**，直接转发 `original_body`，零额外开销。

### 3.3 输入结构

仅提取用户手写消息, 过滤 tool_result (工具输出/文件内容), 最近 5 条。

```json
{
  "role": "system",
  "content": "你是图片注意力路由器。根据用户消息和缓存图片摘要, 调用 route_decision 函数。禁止直接回答。",

  "role": "user",
  "content": "
    用户历史消息 (最近5条, 已过滤工具输出):
    1. {user_msg_1}
    ...
    5. {user_msg_5}  ← 最新

    已缓存图片:
    [hash=abc123 file=image_123.png] {summary}
    ...
    最近 assistant 回复: {last_reply}

    请调用 route_decision 函数。"
}
```

### 3.4 工具调用输出

使用 DeepSeek Chat 原生工具调用 (tool_choice="required"), API 层保证 JSON 合法性。

```json
// API 请求参数
{
  "tools": [{
    "type": "function",
    "function": {
      "name": "route_decision",
      "parameters": {
        "type": "object",
        "properties": {
          "image_hashes": {
            "type": "array", "items": {"type": "string"},
            "description": "SHA-256哈希列表, 无关时=[]"
          },
          "focus_prompt": {
            "type": "string",
            "description": "视觉识别指令(10-150字祈使句)"
          },
          "mode": {
            "type": "string", "enum": ["single","compare"]
          },
          "reasoning": {
            "type": "string", "description": "判断理由(≤50字)"
          }
        },
        "required": ["image_hashes","focus_prompt","mode","reasoning"]
      }
    }
  }],
  "tool_choice": {"type": "function", "function": {"name": "route_decision"}}
}

// API 响应 — arguments 永为合法 JSON
{
  "choices": [{"message": {
    "tool_calls": [{"function": {
      "name": "route_decision",
      "arguments": "{\"image_hashes\":[\"abc123\"],\"focus_prompt\":\"...\",\"mode\":\"single\",\"reasoning\":\"...\"}"
    }}]
  }}]
}
```

### 3.5 字段级校验 + 重试

不依赖关键词规则 — 按字段约定严格校验, 不合法 → 带错误原因重试。

```
┌───────────────┬──────────────────────────────────────────┐
│ 字段           │ 校验规则                                   │
├───────────────┼──────────────────────────────────────────┤
│ image_hashes[] │ 每项必须 /^[a-f0-9]{64}$/ (SHA-256)     │
│ focus_prompt   │ 4-200 字, 超长/过短 → 拒绝                │
│ mode           │ "single" | "compare"                     │
│ reasoning      │ 不校验 (自由文本)                          │
└───────────────┴──────────────────────────────────────────┘

重试流程:
  1. 调用模型 (tool_choice)
  2. 解析 arguments → 字段校验
  3. 校验失败 → 追加错误原因到 prompt → 重试 (最多2次)
  4. 全部失败 → 降级 skip, 不阻断请求
```

### 3.6 为什么用工具调用而非 prompt 要 JSON

```
prompt 方式:                    工具调用方式:
  模型可能输出 markdown 代码块      API 层强制 tool_calls 结构
  可能夹杂回答/评论文本              arguments 永为合法 JSON
  格式错误需正则提取                 无需解析、无需清洗
  遵循率依赖 prompt 质量             模型经过 RLHF 工具调用训练
```

---

## 4. 五种请求路径

```
PATH A — 新图首次识别
─────────────────────
用户 "描述这张图" + 新 base64
  → latest_only 提取: 有图
  → 决策: re_vision, focus=通用描述
  → Vision 识图
  → 缓存: (hash, focus) → desc
  → Rewrite → Forward
  → 延迟: ~10s

PATH B — 追问细节 (用户主动重读)
────────────────────────────────
用户 "打开 image_123.png, 看右上角写了什么" + 新 base64
  → latest_only 提取: 有图
  → 决策: re_vision, hash=新图, focus=右上角
  → Vision 识图 (带 focus)
  → 更新缓存
  → Rewrite → Forward
  → 延迟: ~10s

PATH C — 纯文本无关追问 (短路)
─────────────────────────────
用户 "今天是几号" (无图, 无缓存)
  → latest_only: 无新图
  → has_images(): False
  → cache.entries(): []
  → 短路跳过决策引擎 (0 API 调用)
  → 直通 original_body
  → 延迟: 0ms

PATH D — 语义追问未重读 (决策模型自主提取)
────────────────────────────────────────
用户 "之前那张甘特图谁负责颜色审批" (图在历史, 有缓存)
  → latest_only: 无新图
  → cache.entries(): 有摘要 (文件名 "甘特图" 等)
  → 决策: re_vision, hash=abc123 (匹配摘要 "甘特图" + 文件名),
           focus=颜色审批
  → 代理遍历请求体 messages, 找到哈希匹配的图
  → Vision 识图 (单图模式, 带 focus prompt)
  → 更新缓存 → Rewrite → Forward
  → 延迟: ~10s
  → 全程用户无感知

PATH E — 历史图首次识别 (无缓存, 全量提取)
────────────────────────────────────────
用户 "[Image #1] 这张图讲了啥" (图在历史 tool_result, 无缓存)
  → latest_only: 无新图 (最后一条消息只有文字)
  → cache.entries(): []
  → has_images(): True → 不短路
  → 缓存为空, 决策引擎无从选择 hash → 跳过决策
  → 提取请求体中全部图片 → Vision 识图
  → 缓存写入 → Rewrite → Forward
  → 延迟: ~10s
  → 全程用户无感知
```

---

## 5. 多图场景

```
用户 "对比之前那两张架构图, 哪个更适合微服务"
  (图1 和 图2 都在历史消息中)

  → 决策: action=re_vision, hashes=[图1,图2], mode=compare, focus=微服务适用性
  → 代理从请求体中找到两张图
  → Vision: recognize_compare (多图一次调用, 图像 token 间 attention)
  → Rewrite: "[图片对比分析]\n图1: ...\n图2: ...\n对比: ..."
  → Forward → 目标模型

单图追问和多图对比统一处理, 仅 prompt 和 mode 不同。
对比模式获取全部图片的 per-hash 锁 (按排序避免死锁) 后调用一次 Vision。
```

---

## 6. 与原生多模态的差距分析

```
┌────────────────────┬──────────────────┬─────────────────────────────┐
│ 能力维度            │ 原生多模态模型     │ 我们的架构                    │
├────────────────────┼──────────────────┼─────────────────────────────┤
│ 图像理解            │ 端到端, 像素级     │ 分离: 视觉模型 → 文本 → 目标  │
│ 注意力计算           │ 图像+文本 token   │ 文本决策模型 + 缓存摘要       │
│ 多图关联            │ 天然, attention  │ 决策模型触发 + 对比 prompt   │
│ 已处理图的后续提问   │ 任意时间可回溯     │ 决策模型定位 + 主动重识       │
│ 实时性              │ 端到端一次推理     │ 决策→视觉→目标, 多次调用      │
│ 成本                │ 图像 token 计费   │ 视觉按图计费, 文本按 token    │
│ 灵活性              │ 单模型决定一切     │ 三模型各自优化, 可独立替换     │
└────────────────────┴──────────────────┴─────────────────────────────┘
```

不可消除的差距: 我们的架构需要多次推理 (决策→视觉→目标), 延迟叠加。
不可消除的优势: 各模型独立选型升级, 成本可控, 视觉模型可用最强但最贵的。

---

## 7. 实现状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| 1 | 决策模型 client (DeepSeek Chat + 工具调用) | ✅ |
| 2 | 最新消息扫描 + 缓存摘要构建 (过滤 tool_result) | ✅ |
| 3 | 决策模型集成 pipeline | ✅ |
| 4 | 历史图定位+提取 (PATH D, hash 去重) | ✅ |
| 5 | 多图对比模式 (recognize_compare) | ✅ |
| 6 | 字段级校验 + 工具调用强制 + 重试机制 | ✅ |
| 7 | 纯文本短路优化 (PATH C — 跳过决策引擎) | ✅ |
| 8 | 历史图首次识别 (PATH E — 无缓存全量提取) | ✅ |
| 9 | 决策引擎触发条件: 有图或有缓存 | ✅ |
| 10 | 缓存模块独立 (CacheStore + per-hash Lock) | ✅ |
| 11 | 视觉客户端连接池 + Semaphore(20) | ✅ |
| 12 | 请求路径通配 (原始路径直传, 不拼接后缀) | ✅ |
| 13 | PATH D/E 边界情况实测 | 🔄 进行中 |

---

## 8. 并发安全

### 8.1 CacheStore — 缓存击穿防护

`cache_store.py` 独立模块, 每个图片 hash 配备 `asyncio.Lock`:

```
请求 A: cache.get(hash) → miss
请求 A: await lock.acquire() → 拿到锁
请求 B: cache.get(hash) → miss
请求 B: await lock.acquire() → 等待 A 释放
请求 A: vision → cache.set() → lock.release()
请求 B: cache.get(hash) → hit ✓ (无需再次识图)
```

### 8.2 视觉客户端 — 连接池 + 并发限流

- **httpx.AsyncClient 共享实例**: 连接复用 (max_connections=50, max_keepalive=20)
- **asyncio.Semaphore(20)**: 最多 20 个并发识图请求, 防止打爆上游 API
- **compare 模式**: 按 hash 排序获取全部锁, 避免死锁

### 8.3 上下文隔离

每个请求的 `body`、`proxy_request`、`target_config`、`decision` 都在协程局部变量中, 不经过共享状态。缓存按图片内容 hash 索引, 不同请求的不同图片天然隔离。

---

## 附录 A: 多图对比 — Vision 调用方式

### 原生多模态模型的对比机制

```
POST /v1/chat/completions
{
  "model": "gpt-4v",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,<图A>"}},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,<图B>"}},
      {"type": "text", "text": "请对比两张图的架构差异"}
    ]
  }]
}
```

多图放在同一个 messages[0].content 数组中，vision 模型一次性看到所有图，在图像 token 间做 attention。

### 我们的实现

`recognize_compare()` — 所有图一次发送, 模型在图像间做关联:

```
class QwenVisionClient:
    async def recognize_compare(
        self,
        images: list[ImageBlock],
        focus_prompt: str,
    ) -> str:
        """多图对比: 所有图一次发送, 模型在图像间做关联。"""
        content = []
        for img in images:
            b64 = base64.b64encode(img.image_data).decode()
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{img.media_type};base64,{b64}"
                }
            })
        content.append({"type": "text", "text": focus_prompt})

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 4096,
        }
        ...

recognize:          单图识别, 带 focus_prompt, per-hash lock 防击穿
recognize_compare:  N 图 1 次调用, 获取全部 per-hash lock 后调用
```

---

## 附录 B: 图片元数据 — 位置与文件名

### 信息来源

Claude Code 对话中, 图片前有 tool_use 记录文件名:

```
messages[6].content[0] → tool_use: Read, input.file_path: "D:\\...\\image_123.png"
messages[7].content[0] → tool_result: content[0] → image (base64)
```

### 缓存元数据扩展

```
缓存条目:
{
    "hash": "abc123",
    "file_name": "image_123.png",      ← 从 tool_use.input.file_path 提取
    "position": 1,                     ← 对话中第几张图 (全局递增)
    "label": "...",                    ← 视觉描述前 40 字符
    "summaries": {
        "通用描述": "项目管理甘特图...",
        "右上角文字": "...Q4 Customer Stories - On Track"
    }
}
```

### 决策模型的引用方式

缓存摘要传给决策模型时携带文件名和位置标签, 用户自然语言的引用更易匹配:

```
已缓存图片:
[第1张 file=image_123.png hash=abc123] 项目管理甘特图, 深色主题...
[第2张 file=screenshot.png hash=def456] 代码 diff 截图, 新增了 3 个文件...
```

用户说 "之前那张甘特图" → 决策模型匹配摘要中的 "甘特图" → 定位到第1张。
用户说 "图一" / "第一张" → 匹配位置标签 → 定位到第1张。

---

## 附录 C: PATH D — 有缓存的语义追问

```
PATH D — 语义追问未重读 (决策模型自主提取)
─────────────────────────────────────────
用户: "之前那张 image_123 的甘特图里谁负责颜色审批"
  (图在历史 messages[7], 用户未重读)

  → latest_only: 无新图
  → cache.entries(): 有缓存 → 不短路
  → 构建决策输入:
      用户历史消息 (5条): ["描述这张图","有哪些颜色","...","今天几号","之前...谁负责..."],
      缓存: [第1张 file=image_123.png] 项目管理甘特图...
      最近对话: ...

  → 决策模型输出:
      {
        action: "re_vision",
        image_hashes: ["abc123"],
        focus_prompt: "重点查看图中颜色审批相关的负责人信息",
        mode: "single",
        reasoning: "用户追问特定细节, 文件名和'甘特图'匹配第1张"
      }

  → 代理定位: hash=abc123 → 遍历 messages[7].content → 提取图片

  → Vision: recognize(image, focus_prompt)
     结果: "图中未找到明确的颜色审批负责人信息, 但...相关任务有颜色标记..."

  → 缓存追加: (abc123, "颜色审批") → 新描述

  → Rewrite → Forward (纯文本)

  全程用户无感知, 不需要 "提示用户重读"。
```

---

## 附录 D: 缓存 key 设计

```
单图模式:
  cache_key = SHA256(image_data)

  首次: hash(img) → desc_general
  追问: hash(img) 相同 → 不同 focus_prompt 产生不同 summary

  缓存结构:
  {
    "abc123": {
      "file_name": "image_123.png",
      "position": 1,
      "label": "项目管理甘特图, 深色主题...",
      "focus_results": {
        "通用描述": "项目管理甘特图...",
        "右上角文字": "...标签为 'Q4 Customer Stories - On Track'",
        "颜色审批": "...图中未找到颜色审批相关信息"
      }
    },
    "def456": {
      "file_name": "screenshot.png",
      "position": 2,
      "label": "微服务架构图...",
      "focus_results": {
        "通用描述": "微服务架构图..."
      }
    }
  }

对比模式: 每张图各自 hash 仍然独立缓存, 但 vision 调用是一次性的。
```

---

## 附录 E: 设计评审修复清单

本次设计评审 (2026-06-28) 发现并修复的问题:

| 严重度 | 问题 | 修复 |
|--------|------|------|
| P0 | pipeline.py 双层管道死代码 | 删除 |
| P0 | HTTP/业务层未分离, _run_pipeline 无法单测 | 抽 _execute_pipeline(body, path, config) |
| P1 | model_router.py 死代码 | 删除 |
| P1 | 缓存耦合在 image_extractor | 独立 cache_store.py + per-hash Lock |
| P1 | 决策引擎无测试 | test_decision_engine.py (23 用例) |
| P2 | 无 shutdown hook | FastAPI lifespan 关闭所有 httpx client |
| P2 | Vision+缓存逻辑重复 | 抽取 _vision_and_cache + _vision_compare_locked |
| P2 | 路径路由脆弱 | x-target-base-url header + 原始路径直传 |
| P3 | config/main 不一致 | main.py 使用 ProxyConfig 默认值 |
| — | 纯文本请求触发决策引擎 | 短路优化: 无图无缓存不调决策 |
| — | Vision 每次创建新 httpx.AsyncClient | 共享客户端 + 连接池 |
| — | 并发无上限 | Semaphore(20) 限并发 |
| — | 历史图无缓存转发 base64 | PATH E: 全量提取 + Vision → Rewrite |
| — | Docker 配置完善 | Dockerfile + compose + .dockerignore + build-local.sh |
