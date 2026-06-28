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
import json
import logging

import httpx

from backend.src.config import ProxyConfig
from backend.src.models import ImageBlock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FALLBACK_TEXT: str = "[图片无法识别]"

DEFAULT_PROMPT: str = "请描述这张图片的内容。"

_MAX_RETRIES = 5

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_retryable(status: int) -> bool:
    """429 and 5xx are transient; 4xx (except 429) are not."""
    return status == 429 or status >= 500


def _build_prompt(focus: str) -> str:
    """Return the vision prompt — default description, or user's focus if given."""
    return focus or DEFAULT_PROMPT

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
        b64 = base64.b64encode(image.image_data).decode("ascii")
        media_type = image.media_type or "image/png"
        prompt = _build_prompt(focus_prompt)

        logger.info("Vision request: model=%s size=%d media=%s focus=%.60s",
                     self._model, len(image.image_data), media_type, prompt)

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
            "max_tokens": 4096,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        return await self._call_with_retry(url, payload, headers, "single")

    async def _call_with_retry(self, url: str, payload: dict, headers: dict, label: str) -> str:
        """Post to vision API with exponential backoff retry (5 retries max).

        Retryable: 429, 5xx, TimeoutException, RequestError
        Non-retryable: 4xx (except 429), JSONDecodeError
        """
        last_error = ""
        client = self._get_client()
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with self._semaphore:
                    resp = await client.post(url, json=payload, headers=headers)

                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]

                if not _is_retryable(resp.status_code):
                    logger.warning("Vision [%s]: HTTP %d (non-retryable) — %s",
                                   label, resp.status_code, resp.text[:300])
                    return FALLBACK_TEXT

                last_error = f"HTTP {resp.status_code}"
                logger.warning("Vision [%s]: %s (attempt %d/%d)",
                               label, last_error, attempt + 1, _MAX_RETRIES + 1)

            except httpx.TimeoutException:
                last_error = f"timeout ({timeout}s)"
                logger.warning("Vision [%s]: %s (attempt %d/%d)",
                               label, last_error, attempt + 1, _MAX_RETRIES + 1)
            except httpx.RequestError as e:
                last_error = f"request error: {e}"
                logger.warning("Vision [%s]: %s (attempt %d/%d)",
                               label, last_error, attempt + 1, _MAX_RETRIES + 1)
            except json.JSONDecodeError as e:
                logger.warning("Vision [%s]: JSON parse error — %s", label, e)
                return FALLBACK_TEXT

            if attempt < _MAX_RETRIES:
                wait = 2 ** attempt  # 1, 2, 4, 8, 16 seconds
                logger.info("Vision [%s]: retrying in %ds...", label, wait)
                await asyncio.sleep(wait)

        logger.warning("Vision [%s]: exhausted %d retries, last error: %s",
                       label, _MAX_RETRIES, last_error)
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
            b64 = base64.b64encode(img.image_data).decode("ascii")
            media = img.media_type or "image/png"
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
            "max_tokens": 4096,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        return await self._call_with_retry(url, payload, headers, "compare")
