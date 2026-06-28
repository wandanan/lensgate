"""
Decision Engine — lightweight intent recognition for image attention routing.

Uses a fast text model (DeepSeek Chat) to decide:
- Should we re-vision an image? (re_vision vs skip)
- Which image(s)? (by hash)
- What to focus on? (focus_prompt)
- Is it a comparison? (mode: single vs compare)

Input is bounded (~500 tokens) to keep latency <0.5s regardless of
conversation length.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class DecisionResult:
    """Parsed decision output."""

    __slots__ = ("image_hashes", "focus_prompt", "mode", "reasoning")

    def __init__(
        self,
        image_hashes: list[str] | None = None,
        focus_prompt: str = "",
        mode: str = "single",
        reasoning: str = "",
    ):
        self.image_hashes = image_hashes or []
        self.focus_prompt = focus_prompt
        self.mode = mode  # "single" | "compare"
        self.reasoning = reasoning

    def __repr__(self) -> str:
        return (
            f"Decision(hashes={self.image_hashes}, "
            f"mode={self.mode}, focus={self.focus_prompt[:50]}...)"
        )


# ---------------------------------------------------------------------------
# Decision Engine
# ---------------------------------------------------------------------------


class DecisionEngine:
    """Lightweight intent recognition for image attention routing.

    Uses a fast text model to decide whether and how to re-vision images.

    Parameters:
        api_key: API key for the decision model provider.
        base_url: Base URL for the chat completions endpoint.
        model: Model identifier (default: "deepseek-chat").
    """

    SYSTEM_PROMPT = (
        "你是图片注意力路由器。用户正在讨论之前对话中的图片,"
        "你需要找出哪些图片与用户当前问题最相关,并给出聚焦指令。\n\n"
        "规则:\n"
        "- 如果用户问题与某张已缓存图片相关 → 返回该图片hash和focus_prompt\n"
        "- 如果用户要对比多张图 → mode=compare, 列出所有相关hash\n"
        "- 如果问题明确与所有图片无关 (如问天气、日期) → image_hashes=[]\n"
        "- focus_prompt: 告诉视觉模型应该重点看什么,用用户的原问题语言\n\n"
        "重要: 宁可多返回一张可能相关的图, 也不要遗漏。\n\n"
        "输出纯 JSON:\n"
        '{"image_hashes":["hash1"],"focus_prompt":"重点查看...","mode":"single|compare","reasoning":"..."}'
    )

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        timeout: int = 5,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def decide(
        self,
        user_messages: list[str],
        cached_images: list[dict[str, str]],
        last_assistant_reply: str = "",
    ) -> DecisionResult:
        """Analyse user intent and return a routing decision.

        Args:
            user_messages: Last N user messages (newest last).
            cached_images: List of {hash, file_name, summary} dicts.
            last_assistant_reply: Most recent assistant response.

        Returns:
            DecisionResult with action, target hashes, focus prompt, etc.
        """
        prompt = self._build_prompt(user_messages, cached_images, last_assistant_reply)

        try:
            raw = await self._call_model(prompt)
            return self._parse(raw)
        except Exception as exc:
            logger.warning("Decision engine failed, defaulting to no images: %s", exc)
            return DecisionResult(reasoning=f"error: {exc}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        user_messages: list[str],
        cached_images: list[dict[str, str]],
        last_assistant_reply: str,
    ) -> str:
        lines: list[str] = []

        lines.append("用户历史消息 (最新在最后):")
        for i, msg in enumerate(user_messages, 1):
            lines.append(f"  {i}. {msg}")

        if cached_images:
            lines.append("\n已缓存图片:")
            for img in cached_images:
                lines.append(
                    f"  [hash={img['hash']} file={img.get('file_name','?')}] "
                    f"{img.get('summary','')[:120]}"
                )
        else:
            lines.append("\n已缓存图片: (无)")

        if last_assistant_reply:
            lines.append(f"\n最近 assistant 回复: {last_assistant_reply[:200]}")

        lines.append("\n请判断: 是否需要重新识图? 输出 JSON。")
        return "\n".join(lines)

    async def _call_model(self, prompt: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 400,
            "temperature": 0.1,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    @staticmethod
    def _parse(raw: str) -> DecisionResult:
        # Strip markdown fences if present.
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        obj: dict[str, Any] = json.loads(text)

        return DecisionResult(
            image_hashes=obj.get("image_hashes", []),
            focus_prompt=obj.get("focus_prompt", ""),
            mode=obj.get("mode", "single"),
            reasoning=obj.get("reasoning", ""),
        )
