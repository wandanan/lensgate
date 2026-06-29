"""
Vision Client — Qwen 3.7 Plus 识图服务封装.

Encapsulates the Aliyun Bailian Coding Plan Qwen 3.7 Plus API.
Sends images to the vision model and returns text descriptions.

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

# Specialised prompt for design→code replication.  The vision model is told
# to act as a measurement tool that extracts exact CSS values from a UI
# screenshot.  This avoids the precision loss of natural-language colour
# descriptions ("warm yellow" → #f59e0b).
_REPLICATE_PROMPT: str = (
    "你是设计测量工具。从截图中精确提取视觉规范,只输出CSS自定义属性。\n"
    "\n"
    "严格按以下格式输出,禁止任何解释、描述或额外文字:\n"
    "\n"
    "<style>\n"
    ":root {\n"
    "  --bg-primary: <页面背景色hex>;\n"
    "  --bg-secondary: <次要背景色hex,如卡片/区块>;\n"
    "  --text-primary: <主文字色hex>;\n"
    "  --text-secondary: <次要文字色hex>;\n"
    "  --accent: <强调色/品牌色hex>;\n"
    "  --accent-hover: <强调色hover态hex,比accent深10-15%>;\n"
    "  --font-family: <字体栈,优先系统字体>;\n"
    "  --font-size-title: <标题字号,含单位>;\n"
    "  --font-size-body: <正文字号,含单位>;\n"
    "  --radius-sm: <小圆角,含单位>;\n"
    "  --radius-md: <中圆角,含单位>;\n"
    "  --radius-lg: <大圆角,含单位>;\n"
    "  --radius-full: 9999px;\n"
    "  --shadow-card: <卡片阴影,如 0 4px 20px rgba(0,0,0,0.06)>;\n"
    "  --container-max: <内容区最大宽度,含单位>;\n"
    "  --nav-height: <导航栏高度或padding,含单位>;\n"
    "  --section-gap: <区块间距,含单位>;\n"
    "  --card-padding: <卡片内边距,含单位>;\n"
    "}\n"
    "</style>\n"
    "\n"
    "规则:\n"
    "- 颜色必须精确到hex值(如 #f8f7f4)。不确定时给出最佳估计,不要写颜色名称。\n"
    "- 尺寸精确到像素。从图片比例推算,不求精确到1px但求比例正确。\n"
    "- 如界面有多套配色(深色/浅色),只提取当前显示的那套。\n"
    "- 最多输出上面列出的变量。不要追加额外的CSS规则、布局代码或注释。\n"
    "- 禁止输出HTML标签(除<style>包裹外)。禁止输出完整HTML页面。禁止输出JavaScript。\n"
    "- 禁止markdown代码块包裹。禁止解释。只输出<style>:root{...}</style>。"
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
# QwenVisionClient
# ---------------------------------------------------------------------------


class QwenVisionClient:
    """Client for the Qwen 3.7 Plus vision model via Aliyun Bailian Coding Plan.

    Usage::

        config = ProxyConfig()
        client = QwenVisionClient(config)
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
                    return data["choices"][0]["message"]["content"]

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
        """Extract precise CSS variables from a UI screenshot.

        Uses a specialised prompt that instructs the vision model to output
        ``:root { ... }`` CSS custom properties instead of natural-language
        description.  This eliminates the precision loss of "warm yellow"
        → #f59e0b guessing by the downstream target model.

        Returns the CSS block as a string, or ``""`` on failure so the
        pipeline can fall back to a text description.
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
            "max_tokens": 1024,  # CSS variables are small, cap prevents drift
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        result = await self._call_with_retry(url, payload, headers, "replicate")
        if result == FALLBACK_TEXT:
            return ""
        return result
