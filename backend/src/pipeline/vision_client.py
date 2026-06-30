"""
Vision Client — OpenAI-compatible vision service wrapper.

Encapsulates an OpenAI Chat Completions-compatible vision API. The default
configuration targets Aliyun Bailian Coding Plan with a Qwen vision model, but
other compatible image-analysis models can be selected via VISION_MODEL.

Part of the proxy pipeline (C02):
- recognize()        — send a single image, return text description
- recognize_batch()  — send multiple images in parallel via asyncio.gather

Degradation strategy:
    - Non-200 response        → return "[图片无法识别]"
    - Timeout (configurable)  → return "[图片无法识别]"
    - JSON parse failure      → return "[图片无法识别]"
    - 429 rate-limit          → wait 1 s, retry once; still failing → fallback
    - image_data is None      → return "[图片无法识别]"

Limits:
    - Does NOT parse incoming requests (that is Format Detector / B01).
    - Does NOT rewrite request bodies (that is Request Rewriter / C03).
    - Only responsible for image → text description conversion.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging

import httpx

from backend.src.core.config import ProxyConfig
from backend.src.core.error_handler import ServiceAuthError
from backend.src.core.models import ImageBlock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FALLBACK_TEXT: str = "[图片无法识别]"

DEFAULT_PROMPT: str = "请描述这张图片的内容。"

# Task-framing prefix prepended to every vision prompt.  Pins the model's
# role to "observe only" so it does not drift into generating code, HTML,
# docs, or fix proposals — which bloats output and burns the token budget.
_TASK_CONSTRAINT: str = (
    "你是视觉分析器。仅针对图片实际可见的内容输出观察结论(中文)。"
    "禁止生成代码、HTML、文档、实现方案、修复建议或重写代码;禁止替用户完成任务;禁止输出与图像分析无关的内容。"
    "只描述你在图中真正看到的东西,看到什么说什么,不确定就说明。"
)

_MAX_RETRIES = 5

# Output cap. Compare tasks that drift into codegen hit 4096 and stall for
# 100s+; 1500 is ample for an observation report and truncates drift early.
_MAX_OUTPUT_TOKENS = 1500

# Rich design-spec prompt for replicate mode.  Produces natural-language
# sections (colour, typography, ASCII layout, components) plus CSS variables,
# giving the downstream target model both semantic understanding and
# measurable design tokens.
_REPLICATE_PROMPT: str = (
    "你是视觉设计审计员。从截图中完整提取所有视觉设计信息,用于精确复刻该界面。\n"
    "\n"
    "按以下五部分输出:\n"
    "\n"
    "## 1. 色彩系统\n"
    "- 每个区域/组件的背景色(hex)、文字色(hex)、边框色(hex)\n"
    "- 渐变: 方向、起止色hex、中间色\n"
    "- 透明/半透明: rgba 值和叠加效果\n"
    "\n"
    "## 2. 排版字体\n"
    "- 每个层级的字体族、字号、字重、行高、字间距\n"
    "- 等宽字体区域及其字体\n"
    "- 文字对齐和换行\n"
    "\n"
    "## 3. 布局结构\n"
    "用ASCII框线图画出页面的区域划分和嵌套关系,标注各区域用途。\n"
    "只表达相对位置和包含关系,不需要标注像素尺寸。\n"
    "下面只是格式参考,请严格按截图中实际的布局来画:\n"
    "  +------------------------------------------+\n"
    "  |               TopBar                      |\n"
    "  +----------+-------------------------------+\n"
    "  | Sidebar  |        Main Content           |\n"
    "  | (nav)    |                               |\n"
    "  +----------+-------------------------------+\n"
    "如果截图中没有侧边栏就不要画侧边栏;有三栏就画三栏。如实反映截图。\n"
    "\n"
    "## 4. 组件细节\n"
    "- 每个组件: 圆角、边框(宽度/颜色/样式)、阴影(含offset/blur/spread/color)\n"
    "- hover/active/disabled 交互态变化\n"
    "- 图标(大小/颜色)、头像(形状/边框)\n"
    "- 代码块: 背景色、语法高亮色(关键字/字符串/注释)\n"
    "- 分割线: 方向、颜色、粗细\n"
    "- 列表/树: 缩进量、展开箭头样式、选中高亮\n"
    "\n"
    "## 5. CSS 自定义属性\n"
    "按需增补变量,下面只是最低要求:\n"
    "<style>\n"
    ":root {\n"
    "  --bg-primary: <hex>;\n"
    "  --bg-secondary: <hex>;\n"
    "  --bg-tertiary: <hex>;\n"
    "  --text-primary: <hex>;\n"
    "  --text-secondary: <hex>;\n"
    "  --text-muted: <hex>;\n"
    "  --accent: <hex>;\n"
    "  --accent-hover: <hex>;\n"
    "  --accent-soft: <hex>;\n"
    "  --success: <hex>;\n"
    "  --warning: <hex>;\n"
    "  --danger: <hex>;\n"
    "  --border: <hex>;\n"
    "  --border-hover: <hex>;\n"
    "  --font-sans: <字体>;\n"
    "  --font-mono: <字体>;\n"
    "  --font-size-xs: <含单位>;\n"
    "  --font-size-sm: <含单位>;\n"
    "  --font-size-base: <含单位>;\n"
    "  --font-size-lg: <含单位>;\n"
    "  --font-size-xl: <含单位>;\n"
    "  --font-size-2xl: <含单位>;\n"
    "  --radius-sm: <含单位>;\n"
    "  --radius-md: <含单位>;\n"
    "  --radius-lg: <含单位>;\n"
    "  --radius-full: 9999px;\n"
    "  --shadow-sm: <阴影>;\n"
    "  --shadow-md: <阴影>;\n"
    "  --shadow-lg: <阴影>;\n"
    "  --section-gap: <含单位>;\n"
    "  --card-padding: <含单位>;\n"
    "}\n"
    "</style>\n"
    "\n"
    "规则:\n"
    "- 颜色必须hex或rgba,禁止颜色名称(warm yellow→#f59e0b)\n"
    "- 渐变必须标注方向和每段色值\n"
    "- 禁止输出HTML页面、JavaScript、markdown代码块\n"
    "- 不确定给出最合理估计,禁止说\"我无法确定\""
)

# Images larger than this (bytes) are compressed before sending to vision API.
_COMPRESS_THRESHOLD = 128 * 1024  # 128 KB

# Max dimension (longest side) after resize.
# Vision models bill image tokens per pixel (~0.0013 token/pixel), so
# reducing dimensions is the only lever that actually cuts cost and time —
# PNG→JPEG byte shrinking does not. 1024 keeps text/code detail legible
# while halving image_tokens vs 1600-wide originals.
_MAX_DIMENSION = 1024

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_retryable(status: int) -> bool:
    """429 and 5xx are transient; 4xx (except 429) are not."""
    return status == 429 or status >= 500


def _build_prompt(focus: str) -> str:
    """Return the vision prompt — task constraint + focus (or default)."""
    body = focus or DEFAULT_PROMPT
    return f"{_TASK_CONSTRAINT}\n\n任务：{body}"


def _compress_image(data: bytes, media_type: str) -> tuple[bytes, str]:
    """Compress images over the byte threshold to avoid vision API timeouts.

    Strategy:
    - If dimensions > _MAX_DIMENSION: resize first, then compress
    - If under dimension limit but over byte limit: convert PNG → JPEG
    - Returns (compressed_bytes, media_type)
    """
    if len(data) <= _COMPRESS_THRESHOLD:
        return data, media_type

    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        w, h = img.size

        # Resize if too large in dimensions
        scale = _MAX_DIMENSION / max(w, h)
        if scale < 1.0:
            new_size = (int(w * scale), int(h * scale))
            img = img.resize(new_size, Image.LANCZOS)
        else:
            new_size = (w, h)

        # Convert RGBA/P → RGB for JPEG compatibility
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Use JPEG for RGB, PNG otherwise
        out_fmt = "JPEG" if img.mode == "RGB" else "PNG"
        out_mime = "image/jpeg" if out_fmt == "JPEG" else "image/png"
        buf = io.BytesIO()
        img.save(buf, format=out_fmt, quality=85)
        compressed = buf.getvalue()

        logger.info(
            "Image compressed: %dx%d → %dx%d, %d → %d bytes",
            w, h, new_size[0], new_size[1],
            len(data), len(compressed),
        )
        return compressed, out_mime

    except Exception:
        logger.warning("Image compression failed, sending original", exc_info=True)
        return data, media_type


# ---------------------------------------------------------------------------
# OpenAICompatibleVisionClient
# ---------------------------------------------------------------------------


class OpenAICompatibleVisionClient:
    """Client for OpenAI Chat Completions-compatible vision models.

    Usage::

        config = ProxyConfig()
        client = OpenAICompatibleVisionClient(config)
        description = await client.recognize(image_block)  # single image
        descriptions = await client.recognize_batch(blocks)  # parallel batch
    """

    def __init__(self, config: ProxyConfig) -> None:
        self._api_key: str = config.vision_api_key
        self._base_url: str = config.vision_base_url
        self._model: str = config.vision_model
        self._timeout: int = config.vision_timeout
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(20)  # max concurrent vision calls

    def _get_client(self) -> httpx.AsyncClient:
        """Lazily create a shared httpx.AsyncClient with connection pooling."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def recognize(self, image: ImageBlock, focus_prompt: str = "") -> str:
        """Recognise a single image and return a text description.

        When *focus_prompt* is given, it overrides the default description prompt.
        Retries up to 5 times with exponential backoff on transient failures.
        """
        if image.image_data is None:
            logger.warning("Vision: image_data is None")
            return FALLBACK_TEXT

        url = f"{self._base_url}/v1/chat/completions"
        data, media_type = _compress_image(image.image_data, image.media_type or "image/png")
        b64 = base64.b64encode(data).decode("ascii")
        prompt = _build_prompt(focus_prompt)

        logger.info("Vision request: model=%s size=%d", self._model, len(data))

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{b64}",
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": _MAX_OUTPUT_TOKENS,
        }
        logger.debug("Vision payload: url=%s bytes=%d", url, len(json.dumps(payload)))
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        return await self._call_with_retry(url, payload, headers, "single")

    async def _call_with_retry(self, url: str, payload: dict, headers: dict, label: str) -> str:
        """Post to vision API with exponential backoff retry.

        Retryable: 429, 5xx, TimeoutException, RequestError
        Non-retryable: 4xx (except 429), JSONDecodeError

        Timeouts cap at 2 retries: a timeout usually means the task is too
        heavy for one call, so re-sending the identical heavy payload just
        burns wall-clock repeating the same slow run.  429/5xx are transient
        and keep the full 5-retry budget.
        """
        last_error = ""
        client = self._get_client()
        max_retries = _MAX_RETRIES
        for attempt in range(max_retries + 1):
            try:
                async with self._semaphore:
                    resp = await client.post(url, json=payload, headers=headers)

                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    logger.debug("Vision [%s] OK: tokens=%s\n%s",
                                 label,
                                 data.get("usage", {}).get("total_tokens", "?"),
                                 content)
                    return content

                if not _is_retryable(resp.status_code):
                    if resp.status_code in (401, 403):
                        raise ServiceAuthError(
                            f"视觉模型 API 密钥无效或已过期 (HTTP {resp.status_code})。"
                            f"请检查 .env 中的 VISION_API_KEY。"
                        )
                    logger.warning("Vision [%s]: HTTP %d (non-retryable) — %s",
                                   label, resp.status_code, resp.text[:300])
                    return FALLBACK_TEXT

                last_error = f"HTTP {resp.status_code}"
                logger.warning("Vision [%s]: %s (attempt %d/%d)",
                               label, last_error, attempt + 1, max_retries + 1)

            except httpx.TimeoutException:
                last_error = f"timeout ({self._timeout}s)"
                # Shrink the retry budget for timeouts — re-sending an
                # identical heavy payload only repeats the same slow run.
                max_retries = min(max_retries, 2)
                logger.warning("Vision [%s]: %s (attempt %d/%d, retry cap %d)",
                               label, last_error, attempt + 1, max_retries + 1, max_retries)
            except httpx.RequestError as e:
                last_error = f"request error: {e}"
                logger.warning("Vision [%s]: %s (attempt %d/%d)",
                               label, last_error, attempt + 1, max_retries + 1)
            except json.JSONDecodeError as e:
                logger.warning("Vision [%s]: JSON parse error — %s", label, e)
                return FALLBACK_TEXT

            if attempt < max_retries:
                wait = 2 ** attempt  # 1, 2, 4, 8, 16 seconds
                logger.info("Vision [%s]: retrying in %ds...", label, wait)
                await asyncio.sleep(wait)

        logger.warning("Vision [%s]: exhausted %d retries, last error: %s",
                       label, max_retries, last_error)
        return FALLBACK_TEXT

    async def recognize_batch(self, images: list[ImageBlock]) -> list[str]:
        """Recognise multiple images in parallel.

        Uses ``asyncio.gather(..., return_exceptions=True)`` so that a single
        image failure never blocks the remaining images.  Any Exception
        captured by gather is converted to ``"[图片无法识别]"``.
        """
        tasks = [self.recognize(img, "") for img in images]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out: list[str] = []
        for r in results:
            if isinstance(r, BaseException):
                out.append(FALLBACK_TEXT)
            else:
                out.append(r)
        return out

    async def recognize_compare(
        self, images: list[ImageBlock], focus_prompt: str
    ) -> str:
        """Compare multiple images in a single vision call with cross-image attention.

        Retries up to 5 times with exponential backoff on transient failures.
        """
        if not images:
            return FALLBACK_TEXT

        url = f"{self._base_url}/v1/chat/completions"
        content: list[dict] = []

        for img in images:
            if img.image_data is None:
                continue
            data, media = _compress_image(img.image_data, img.media_type or "image/png")
            b64 = base64.b64encode(data).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media};base64,{b64}"},
            })

        if not content:
            return FALLBACK_TEXT

        content.append({"type": "text", "text": _build_prompt(focus_prompt)})

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": _MAX_OUTPUT_TOKENS,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        return await self._call_with_retry(url, payload, headers, "compare")

    async def recognize_replicate(self, image: ImageBlock) -> str:
        """Extract a rich design spec from a UI screenshot.

        Uses a comprehensive prompt covering colours, typography, ASCII layout,
        component details, and CSS variables — giving the downstream model
        both semantic understanding and measurable design tokens.
        """
        if image.image_data is None:
            logger.warning("Vision replicate: image_data is None")
            return ""

        url = f"{self._base_url}/v1/chat/completions"
        data, media_type = _compress_image(image.image_data, image.media_type or "image/png")
        b64 = base64.b64encode(data).decode("ascii")

        logger.info("Vision replicate: model=%s size=%d", self._model, len(data))

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{b64}"},
                        },
                        {"type": "text", "text": _REPLICATE_PROMPT},
                    ],
                }
            ],
            "max_tokens": 4096,
        }
        logger.debug("Vision replicate payload: url=%s bytes=%d", url, len(json.dumps(payload)))
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        result = await self._call_with_retry(url, payload, headers, "replicate")
        if result == FALLBACK_TEXT:
            return ""
        return result
