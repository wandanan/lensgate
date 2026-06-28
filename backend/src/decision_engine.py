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
        "你是图片注意力路由器。根据用户消息和缓存图片摘要,"
        "调用 route_decision 函数输出路由决策。"
        "禁止直接回答用户问题。"
    )

    TOOL_DEFINITION = {
        "type": "function",
        "function": {
            "name": "route_decision",
            "description": "路由决策: 判断哪些图片需要重新识别,以及聚焦指令",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_hashes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "需要重识的图片SHA-256哈希列表, 无关时为空数组"
                    },
                    "focus_prompt": {
                        "type": "string",
                        "description": "给视觉模型的查看指令(10-150字祈使句), 空字符串表示通用描述"
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["single", "compare"],
                        "description": "单图识别还是多图对比"
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "判断理由简述(≤50字)"
                    },
                },
                "required": ["image_hashes", "focus_prompt", "mode", "reasoning"],
            },
        },
    }

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
        max_retries: int = 2,
    ) -> DecisionResult:
        """Analyse user intent and return a routing decision.

        Invalid outputs are retried with an error hint.  After *max_retries*
        failures the engine returns an empty decision (skip).

        Args:
            user_messages: Last N user messages (newest last).
            cached_images: List of {hash, file_name, summary} dicts.
            last_assistant_reply: Most recent assistant response.
            max_retries: Maximum retry attempts on malformed output.

        Returns:
            DecisionResult with target hashes, focus prompt, etc.
        """
        prompt = self._build_prompt(user_messages, cached_images, last_assistant_reply)
        last_error = ""

        for attempt in range(max_retries + 1):
            try:
                # On retry, append the previous error so the model can correct.
                full_prompt = prompt
                if attempt > 0 and last_error:
                    full_prompt = (
                        f"上次输出格式错误: {last_error}\n"
                        f"请严格按 JSON 格式重新输出。\n\n"
                        f"{prompt}"
                    )

                raw = await self._call_model(full_prompt)
                result = self._parse(raw)
                # Validation passed — return immediately.
                logger.debug("Decision OK (attempt %d): %s", attempt + 1, result)
                return result

            except _DecisionValidationError as exc:
                last_error = str(exc)
                logger.warning("Decision validation failed (attempt %d/%d): %s",
                               attempt + 1, max_retries + 1, exc)

            except Exception as exc:
                # Network / API error — retryable.
                last_error = str(exc)
                logger.warning("Decision call failed (attempt %d/%d): %s",
                               attempt + 1, max_retries + 1, exc)

        # All retries exhausted.
        logger.warning("Decision engine exhausted retries, defaulting to skip")
        return DecisionResult(reasoning=f"retries exhausted: {last_error}")

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

        lines.append("\n请输出路由决策 JSON。")
        return "\n".join(lines)

    async def _call_model(self, prompt: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "tools": [self.TOOL_DEFINITION],
            "tool_choice": {
                "type": "function",
                "function": {"name": "route_decision"},
            },
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

            # Extract tool_call arguments — guaranteed valid JSON by the API.
            tool_calls = data["choices"][0]["message"].get("tool_calls", [])
            if not tool_calls:
                raise _DecisionValidationError("Model did not call route_decision tool")
            return tool_calls[0]["function"]["arguments"]

    @staticmethod
    def _parse(raw: str) -> DecisionResult:
        # Tool-calling guarantees valid JSON — just decode.
        try:
            obj: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as e:
            raise _DecisionValidationError(f"Tool call arguments not valid JSON: {e}") from e

        if not isinstance(obj, dict):
            raise _DecisionValidationError("Output is not a JSON object")

        hashes = obj.get("image_hashes", [])
        if not isinstance(hashes, list):
            raise _DecisionValidationError("image_hashes must be an array")
        for h in hashes:
            if not isinstance(h, str):
                raise _DecisionValidationError(f"image_hashes contains non-string: {h!r}")

        mode = obj.get("mode", "single")
        if mode not in ("single", "compare"):
            raise _DecisionValidationError(f"mode must be 'single' or 'compare', got: {mode!r}")

        fp = obj.get("focus_prompt", "")
        if not isinstance(fp, str):
            raise _DecisionValidationError("focus_prompt must be a string")

        reasoning = obj.get("reasoning", "")
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)

        # --- Field-level validation (no heuristics, just format contracts) ---
        if hashes:
            _validate_hashes(hashes)
        if fp:
            _validate_focus(fp)
        fp = fp.strip()[:500]

        return DecisionResult(
            image_hashes=hashes,
            focus_prompt=fp,
            mode=mode,
            reasoning=reasoning,
        )


# ---------------------------------------------------------------------------
# Field-level validation
# ---------------------------------------------------------------------------

import re

# SHA-256 hex digest: 64 lowercase hex characters.
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")

# focus_prompt limits: must be short, an instruction (not an answer).
_MAX_FOCUS_LEN = 200
_MIN_FOCUS_LEN = 4  # shorter than this is likely garbage, not an instruction


def _validate_hashes(hashes: list) -> None:
    """Every entry must be a 64-char hex string (SHA-256)."""
    for h in hashes:
        if not isinstance(h, str):
            raise _DecisionValidationError(f"image_hashes element must be string, got {type(h).__name__}")
        if not _HASH_RE.match(h):
            raise _DecisionValidationError(
                f"image_hashes element must be a 64-char hex SHA-256 hash, got: {h[:60]}..."
            )


def _validate_focus(fp: str) -> None:
    """focus_prompt must be a short routing instruction, not an answer.

    Answers tend to be long and descriptive; instructions are short and imperative.
    """
    if not isinstance(fp, str):
        raise _DecisionValidationError("focus_prompt must be a string")
    fp = fp.strip()
    if not fp:
        return  # empty is allowed (no specific focus)
    if len(fp) < _MIN_FOCUS_LEN:
        raise _DecisionValidationError(
            f"focus_prompt too short ({len(fp)} < {_MIN_FOCUS_LEN}), not a valid instruction"
        )
    if len(fp) > _MAX_FOCUS_LEN:
        raise _DecisionValidationError(
            f"focus_prompt too long ({len(fp)} > {_MAX_FOCUS_LEN}), likely an answer or commentary. "
            "Keep it short and imperative, e.g. '重点查看右上角文字'."
        )


class _DecisionValidationError(ValueError):
    """Raised when the model output fails schema validation."""
    pass
