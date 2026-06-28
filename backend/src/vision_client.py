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

import httpx

from backend.src.config import ProxyConfig
from backend.src.models import ImageBlock

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FALLBACK_TEXT: str = "[图片无法识别]"

RECOGNIZE_PROMPT: str = "请用简洁的语言描述这张图片的内容，重点说明图中有什么信息。"

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def recognize(self, image: ImageBlock) -> str:
        """Recognise a single image and return a text description.

        On any failure — non-200 status, timeout, JSON parse error, missing
        image data — the method returns ``"[图片无法识别]"`` instead of raising
        an exception.  This guarantees that a vision failure never blocks the
        proxy pipeline.

        On HTTP 429 (rate-limit): waits 1 s and retries once.  If the retry
        also returns 429 (or any other error) the fallback text is returned.
        """
        # Guard: no image data to send.
        if image.image_data is None:
            return FALLBACK_TEXT

        url = f"{self._base_url}/v1/chat/completions"
        b64 = base64.b64encode(image.image_data).decode("ascii")
        media_type = image.media_type or "image/png"

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
                        {"type": "text", "text": RECOGNIZE_PROMPT},
                    ],
                }
            ],
            "max_tokens": 2000,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        # At most 2 attempts (initial + 1 retry on 429).
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)

                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]

                # 429 rate-limit → retry after 1 s (only on the first attempt).
                if resp.status_code == 429 and attempt == 0:
                    await asyncio.sleep(1.0)
                    continue

                return FALLBACK_TEXT

            except (httpx.TimeoutException, httpx.RequestError, json.JSONDecodeError):
                return FALLBACK_TEXT

        return FALLBACK_TEXT

    async def recognize_batch(self, images: list[ImageBlock]) -> list[str]:
        """Recognise multiple images in parallel.

        Uses ``asyncio.gather(..., return_exceptions=True)`` so that a single
        image failure never blocks the remaining images.  Any Exception
        captured by gather is converted to ``"[图片无法识别]"``.
        """
        tasks = [self.recognize(img) for img in images]
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
        """Compare multiple images in a single vision call.

        All images are placed in one messages[0].content array so the
        vision model can attend across images — like native multimodal.

        Returns FALLBACK_TEXT on any failure.
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

        content.append({"type": "text", "text": focus_prompt})

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 2000,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout * 2) as client:
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            return FALLBACK_TEXT
        except (httpx.TimeoutException, httpx.RequestError, json.JSONDecodeError):
            return FALLBACK_TEXT
