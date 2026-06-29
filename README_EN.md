# Multimodal Proxy Gateway (TLMA)

A transparent multimodal proxy layer for text-only LLMs (DeepSeek, GLM, etc.). The proxy intercepts images, converts them to text descriptions via a vision model, then forwards the result to your target model — giving text-only models the ability to "see" images.

A decision engine routes visual attention based on user intent: single-image description, multi-image comparison, or skip irrelevant history.

## Architecture

```
Claude Code ──POST /api.deepseek.com/anthropic/v1/messages──▶ TLMA :9856
                                                               │
  ┌──────────────────── Pipeline ─────────────────────────────┤
  │                                                            │
  │  ① Format Detection (Anthropic/OpenAI)                     │
  │  ② Image Extraction + Cache Lookup                         │
  │  ③ Decision Engine (DeepSeek Chat) — route, focus, mode   │
  │  ④ Vision Model (Kimi-K2.5) — image → text / CSS vars     │
  │  ⑤ Request Rewrite (image block → text block)              │
  │  ⑥ Target Forwarding (Coding Plan / API)                   │
  │  ⑦ Response (SSE streaming / JSON)                         │
  │                                                            │
  └── Pure-text requests skip ③④⑤, direct to ⑥ ──────────────┘
```

## What Makes This Different

Traditional vision proxies are simple "image → text → forward" pipelines. TLMA adds four key design decisions:

### 1. Three-Model Collaboration vs Two-Model Chain

```
Traditional:  Vision Model → Target Model           (always calls vision, every image)
TLMA:         Decision Engine → Vision → Target     (decides IF vision is worth calling)
```

A lightweight text call (< 0.5s) runs before any vision API invocation. Is the user asking about a new image or following up? Should we re-vision or use cache? Single-image detail or side-by-side comparison? The decision engine outputs `mode: single | compare | replicate` and triggers vision only when needed. Pure-text requests skip even the decision engine — zero overhead.

### 2. VI-Spec CSS Variables vs Natural Language

```
Traditional:  screenshot → "warm yellow button, large rounded corners, light bg" → guess #f59e0b?
TLMA:         screenshot → :root { --accent: #f59e0b; --radius-md: 14px; --bg: #f8f7f4; } → exact match
```

In **replicate mode**, the vision model acts as a design measurement tool, extracting precise CSS custom properties from UI screenshots. 500 bytes of CSS eliminate the "warm yellow → `#f59e0b`" guessing game. The target model receives exact values, not vague descriptions.

### 3. Composite-Key Cache vs Simple Cache

```
Traditional:  same image re-visioned on every follow-up question
TLMA:         (image SHA-256, focus prompt) composite key — different angles, different cache entries
```

The cache key is `(image_hash, focus_instruction)`, not just the image. Asking "check the button color" vs "describe the overall layout" on the same image yields two independent cache entries. Same angle, repeat question = zero-cost cache hit. Different angle = fresh vision call with new focus.

### 4. Path-Based Routing vs Fixed Target Config

```
Traditional:  proxy hard-codes one target model; switching requires config change + restart
TLMA:         POST /api.deepseek.com/anthropic/v1/messages → forwards to DeepSeek
              POST /ark.cn-beijing.volces.com/api/coding/v1/messages → forwards to Volcengine
              Target is encoded in the URL path — no config change needed
```

No server-side target configuration. The client encodes the target host in the URL path. The proxy parses it, forwards authentication, preserves the full path suffix. A single proxy instance serves multiple clients targeting different models simultaneously.

## Quick Start

> **Important: `.env` must be placed at the project root.** On startup, the config loader checks `root .env` first, then falls back to `backend/.env`. Missing required keys will **crash at startup** with a clear error — no silent degradation.

### Docker (Recommended)

```bash
# 1. Create .env at project root and fill in your API keys
cp backend/.env.example .env
# Edit .env: fill in VISION_API_KEY, DECISION_API_KEY
#   ⚠️ Place at project root, NOT inside backend/

# 2. Build image + start container
bash docker/build-local.sh

# 3. Verify
curl http://localhost:9856/health
# → {"status":"ok","version":"1.0.0",...}
```

### Local Development

```bash
# 1. Virtual environment
python -m venv .venv
source .venv/bin/pip install -r backend/requirements.txt   # Linux/macOS
# .venv/Scripts/pip install -r backend/requirements.txt    # Windows

# 2. Create .env at project root
cp backend/.env.example .env
# Edit .env: fill in VISION_API_KEY, DECISION_API_KEY
#   ⚠️ Place at project root, NOT inside backend/

# 3. Run
PYTHONPATH=. python -m backend.src.main
```

Listens on `http://0.0.0.0:9856`.

> **Startup failure?** Check that `VISION_API_KEY` and `DECISION_API_KEY` are set in the root `.env`. Missing either key causes a hard crash — the proxy will not start with incomplete config.

## Configuration

All settings via `.env` file or environment variables.

| Variable | Required | Default | Description |
|------|:--:|--------|------|
| **Vision Service** | | | |
| `VISION_API_KEY` | Yes | — | API key for the vision service |
| `VISION_BASE_URL` | No | `https://coding.dashscope.aliyuncs.com` | Vision API endpoint |
| `VISION_MODEL` | No | `qwen3.7-plus` | Vision model (`kimi-k2.5` recommended) |
| `VISION_TIMEOUT` | No | `180` | Vision timeout in seconds |
| **Decision Engine** | | | |
| `DECISION_API_KEY` | Yes | — | DeepSeek API key |
| `DECISION_BASE_URL` | No | `https://api.deepseek.com/v1` | Decision model endpoint |
| `DECISION_MODEL` | No | `deepseek-chat` | Decision model |
| `DECISION_TIMEOUT` | No | `5` | Decision timeout in seconds |
| **Proxy Service** | | | |
| `PROXY_API_KEY` | No | `""` | Proxy auth key. Empty = no auth check |
| `PROXY_HOST` | No | `0.0.0.0` | Listen address |
| `PROXY_PORT` | No | `9856` | Listen port |

### Minimal `.env`

```env
VISION_API_KEY=sk-sp-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DECISION_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Only these two are required. Everything else has sensible defaults.

### Vision Model Options

| Model | Notes |
|------|------|
| `kimi-k2.5` | **Recommended**. ~8s single image, ~12s dual-image compare (at 1024px) |
| `qwen3.7-plus` | Native vision model with thinking. ~27s single image, best for complex analysis |
| `qwen3.6-plus` | Previous generation, faster but slightly lower quality |

The vision service uses the OpenAI Chat Completions protocol (`/v1/chat/completions`). Any compatible vision provider works — just change `VISION_BASE_URL`, `VISION_MODEL`, and `VISION_API_KEY`.

## Usage

### Claude Code Setup

**Change exactly one thing:** prefix your existing API base URL with `http://localhost:9856/`. Everything else stays the same.

```
Before:  https://api.deepseek.com/anthropic
After:   http://localhost:9856/api.deepseek.com/anthropic
```

That's it. API key, model name, all other settings — don't touch them.

```bash
claude config set anthropic_base_url http://localhost:9856/api.deepseek.com/anthropic
```

If you're using a different provider:

| Your current URL | Change to |
|------------------|-----------|
| `https://api.deepseek.com/anthropic` | `http://localhost:9856/api.deepseek.com/anthropic` |
| `https://ark.cn-beijing.volces.com/api/coding` | `http://localhost:9856/ark.cn-beijing.volces.com/api/coding` |

The pattern: `http://localhost:9856/` + your original URL with `https://` stripped.

### Non-Conversational Endpoints

`/v1/messages/count_tokens` and similar metadata endpoints are detected automatically and forwarded verbatim — they bypass the vision pipeline entirely. Future Anthropic endpoints (e.g. `/v1/messages/batches`) are auto-covered by the same passthrough logic.

## API Endpoints

| Method | Path | Description |
|------|------|------|
| `GET` | `/health` | Health check |
| `POST` | `/v1/messages` | Anthropic Messages API — vision pipeline |
| `POST` | `/v1/chat/completions` | OpenAI Chat Completions — vision pipeline |
| `POST` | `/v1/messages/count_tokens` | Token counting — passthrough |
| `GET/HEAD/OPTIONS` | `any path` | Forwarded to target |

Auth: `x-api-key` header (or `Authorization: Bearer <key>`). When `PROXY_API_KEY` is empty, no proxy-level auth check — `x-api-key` is forwarded as the target API key.

## Pipeline Details

### Decision Engine

On each request, the decision engine determines:
- Which images need re-visioning? (matched by SHA-256 hash in cache)
- What to focus on? (generates a focused instruction for the vision model)
- Single, compare, or replicate? (`mode: single | compare | replicate`)

When the decision engine is skipped:
- Pure-text request + empty cache → **direct passthrough**, zero overhead
- New images + empty cache → default to full vision pass

### Caching

Vision results are cached by `(image SHA-256 hash, focus prompt)`. The same image with the same focus hits cache instantly; a different focus on the same image triggers a fresh vision call. No redundant API costs for repeated questions.

### Multi-Image Comparison

In `compare` mode, multiple images are sent in a **single** vision API call, allowing the model to perform cross-image attention for true side-by-side analysis.

### Visual Replication (VI-Spec)

When the user says "replicate this design" or "build a page from this screenshot", the decision engine triggers `replicate` mode. Instead of a text description, the vision model outputs precise CSS custom properties:

```css
--accent: #f59e0b; --radius-md: 14px; --bg-primary: #f8f7f4;
```

The target model uses exact values directly, eliminating the precision loss of natural-language color descriptions. 500 bytes of CSS replace 200 words of vague description.

### Degradation

Vision service failures never block the user:
- Timeout / non-200 / JSON parse error → placeholder text `[Image not recognized]`
- Request continues to the target model, which answers based on available text

## Modules

| Layer | Module | File | Responsibility |
|------|------|------|------|
| **core/** | Config | `core/config.py` | Env / .env loading (fallback: root → backend/) |
| | Models | `core/models.py` | ProxyRequest / ImageBlock / ContentBlock |
| | Error Handler | `core/error_handler.py` | 400/413/503/504 mapping + startup validation |
| | Logging | `core/logging_config.py` | Structured logging (structlog JSON) |
| **pipeline/** | Format Detection | `pipeline/format_detector.py` | Anthropic / OpenAI request parsing |
| | Image Extraction | `pipeline/image_extractor.py` | Image extraction + cache |
| | Vision Client | `pipeline/vision_client.py` | Kimi-K2.5 / Qwen recognition + compression |
| | Request Rewriter | `pipeline/request_rewriter.py` | ImageBlock → TextBlock replacement |
| | Decision Engine | `pipeline/decision_engine.py` | Attention routing (single/compare/replicate/skip) |
| | Cache Store | `pipeline/cache_store.py` | SHA-256 + focus composite-key cache |
| | Target Client | `pipeline/target_client.py` | Volcengine / DeepSeek forwarding |
| | Response Handler | `pipeline/response_handler.py` | SSE streaming + JSON non-streaming |
| **middleware/** | Auth Middleware | `middleware/auth.py` | x-api-key validation and forwarding |
| **tools/** | Probe | `tools/probe.py` | API request structure inspection |
| — | Pipeline | `app.py` | 7-stage pipeline + pure-text fast path |
| — | Entry Point | `main.py` | uvicorn server |

## Testing

```bash
# All tests
PYTHONPATH=. .venv/Scripts/pytest backend/tests/ -v

# Vision only
PYTHONPATH=. .venv/Scripts/pytest backend/tests/test_vision_client.py -v

# Routes only
PYTHONPATH=. .venv/Scripts/pytest backend/tests/test_routes.py -v
```
