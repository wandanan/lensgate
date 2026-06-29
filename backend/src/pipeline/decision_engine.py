"""
Decision Engine — lightweight intent recognition for image attention routing.

Uses a fast text model (DeepSeek Chat) with native tool-calling to decide:
- Which image(s) to re-vision? (by SHA-256 hash)
- What to focus on? (focus_prompt)
- Is it a comparison? (mode: single vs compare)

Tool-calling guarantees valid JSON output (no parsing heuristics needed).
Every decision is logged to ``valuation/valuation.jsonl`` for quality analysis.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from backend.src.core.error_handler import ServiceAuthError

logger = logging.getLogger(__name__)

_VALUATION_PATH = Path("valuation/valuation.jsonl")

# SHA-256 hex digest: 64 lowercase hex characters.
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")

# focus_prompt limits.
_MAX_FOCUS_LEN = 200
_MIN_FOCUS_LEN = 4


# ---------------------------------------------------------------------------
# Decision result
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
        self.mode = mode
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
    """Lightweight intent recognition for image attention routing."""

    SYSTEM_PROMPT = (
        "你是图片注意力路由器。根据用户消息和缓存图片摘要,"
        "调用 route_decision 函数输出路由决策。禁止直接回答用户问题。\n"
        "缓存图片带有位置标签(第1张/第2张)和文件名,"
        "用户说'图一''第一张'时严格匹配位置标签。\n"
        "如果用户要求'复刻界面''照着做页面''实现这个设计''design to code'等设计稿→代码任务,"
        "使用 replicate 模式。"
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
                        "description": "需要重识的图片SHA-256哈希列表, 无关时为空数组",
                    },
                    "focus_prompt": {
                        "type": "string",
                        "description": "给视觉模型的查看指令(10-150字祈使句), 空字符串表示通用描述",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["single", "compare", "replicate"],
                        "description": "single=单图描述, compare=多图对比, replicate=设计复刻(提取CSS变量)",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "判断理由简述(≤50字)",
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
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def decide(
        self,
        user_messages: list[str],
        cached_images: list[dict[str, str]],
        new_image_count: int = 0,
        max_retries: int = 2,
    ) -> DecisionResult:
        """Analyse user intent and return a routing decision.

        *new_image_count* tells the engine how many uncached images are in
        the latest message, so it can recommend compare mode even without
        cached entries.
        """
        prompt = self._build_prompt(user_messages, cached_images, new_image_count)
        last_error = ""
        raw_output = ""

        for attempt in range(max_retries + 1):
            try:
                full_prompt = prompt
                if attempt > 0 and last_error:
                    full_prompt = (
                        f"上次输出格式错误: {last_error}\n"
                        f"请严格按工具调用格式重新输出。\n\n"
                        f"{prompt}"
                    )

                raw_output = await self._call_model(full_prompt)
                result = self._parse(raw_output)

                logger.info(
                    "Decision OK (attempt %d): mode=%s images=%d",
                    attempt + 1, result.mode, len(result.image_hashes),
                )

                _write_valuation({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "attempt": attempt + 1,
                    "input": {
                        "user_messages": user_messages,
                        "cached_images": cached_images,
                        "new_image_count": new_image_count,
                    },
                    "output": {
                        "raw": raw_output,
                        "parsed": {
                            "image_hashes": result.image_hashes,
                            "focus_prompt": result.focus_prompt,
                            "mode": result.mode,
                            "reasoning": result.reasoning,
                        },
                    },
                    "status": "ok",
                })

                return result

            except ServiceAuthError:
                raise  # 密钥无效，不重试，直接报错

            except _DecisionValidationError as exc:
                last_error = str(exc)
                logger.warning(
                    "Decision validation failed (attempt %d/%d): %s",
                    attempt + 1, max_retries + 1, exc,
                )

            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "Decision call failed (attempt %d/%d): %s",
                    attempt + 1, max_retries + 1, exc,
                )

        # All retries exhausted.
        logger.warning("Decision engine exhausted retries, defaulting to skip")
        _write_valuation({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attempts": max_retries + 1,
            "input": {
                "user_messages": user_messages,
                "cached_images": cached_images,
            },
            "output": {"last_raw": raw_output, "last_error": last_error},
            "status": "failed",
        })
        return DecisionResult(reasoning=f"retries exhausted: {last_error}")

    # ------------------------------------------------------------------
    # Internal: prompt builder
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        user_messages: list[str],
        cached_images: list[dict[str, str]],
        new_image_count: int = 0,
    ) -> str:
        lines: list[str] = []

        if new_image_count > 0:
            lines.append(f"注意: 最新消息包含 {new_image_count} 张新图片(尚未缓存)。")

        lines.append("用户消息 (最新在最后):")
        for i, msg in enumerate(user_messages, 1):
            lines.append(f"  {i}. {msg}")

        if cached_images:
            lines.append("\n已缓存图片:")
            for img in cached_images:
                pos = img.get("position_label", "")
                fname = img.get("file_name", "?")
                label = img.get("label", "")
                tag_parts = []
                if pos:
                    tag_parts.append(pos)
                tag_parts.append(f"file={fname}")
                if label:
                    tag_parts.append(f"[{label}]")
                tag = " ".join(tag_parts)
                lines.append(
                    f"  [{tag} hash={img['hash']}] "
                    f"{img.get('summary', '')[:120]}"
                )
        else:
            lines.append("\n已缓存图片: (无)")

        lines.append("\n请调用 route_decision 函数。")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal: tool-calling API call
    # ------------------------------------------------------------------

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
        client = self._get_client()

        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code in (401, 403):
            raise ServiceAuthError(
                f"决策模型 API 密钥无效或已过期 (HTTP {resp.status_code})。"
                f"请检查 .env 中的 DECISION_API_KEY。"
            )
        resp.raise_for_status()
        data = resp.json()

        tool_calls = data["choices"][0]["message"].get("tool_calls", [])
        if not tool_calls:
            raise _DecisionValidationError("Model did not call route_decision tool")
        return tool_calls[0]["function"]["arguments"]

    # ------------------------------------------------------------------
    # Internal: parse + validate
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(raw: str) -> DecisionResult:
        try:
            obj: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as e:
            raise _DecisionValidationError(f"Tool call arguments not valid JSON: {e}") from e

        if not isinstance(obj, dict):
            raise _DecisionValidationError("Output is not a JSON object")

        hashes = obj.get("image_hashes", [])
        if not isinstance(hashes, list):
            raise _DecisionValidationError("image_hashes must be an array")

        if hashes:
            _validate_hashes(hashes)

        mode = obj.get("mode", "single")
        if mode not in ("single", "compare", "replicate"):
            raise _DecisionValidationError(
                f"mode must be 'single', 'compare' or 'replicate', got: {mode!r}"
            )

        fp = obj.get("focus_prompt", "")
        if not isinstance(fp, str):
            raise _DecisionValidationError("focus_prompt must be a string")
        fp_stripped = fp.strip()
        # replicate mode carries its own self-contained prompt — focus is optional.
        if fp_stripped and hashes and mode != "replicate":
            _validate_focus(fp_stripped)
        fp = fp_stripped[:500]

        reasoning = obj.get("reasoning", "")
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)

        return DecisionResult(
            image_hashes=hashes,
            focus_prompt=fp,
            mode=mode,
            reasoning=reasoning,
        )


# ---------------------------------------------------------------------------
# Field-level validation
# ---------------------------------------------------------------------------


def _validate_hashes(hashes: list) -> None:
    for h in hashes:
        if not isinstance(h, str):
            raise _DecisionValidationError(f"image_hashes element must be string, got {type(h).__name__}")
        if not _HASH_RE.match(h):
            raise _DecisionValidationError(
                f"image_hashes element must be 64-char hex SHA-256, got: {h[:60]}..."
            )


def _validate_focus(fp: str) -> None:
    if len(fp) < _MIN_FOCUS_LEN:
        raise _DecisionValidationError(
            f"focus_prompt too short ({len(fp)} < {_MIN_FOCUS_LEN}), not a valid instruction"
        )
    if len(fp) > _MAX_FOCUS_LEN:
        raise _DecisionValidationError(
            f"focus_prompt too long ({len(fp)} > {_MAX_FOCUS_LEN}), likely an answer or commentary. "
            "Keep it short and imperative, e.g. '重点查看右上角文字'."
        )


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class _DecisionValidationError(ValueError):
    """Raised when the model output fails schema validation."""
    pass


# ---------------------------------------------------------------------------
# Valuation audit log
# ---------------------------------------------------------------------------


def _write_valuation(record: dict) -> None:
    """Append a decision record to the valuation JSONL file."""
    try:
        _VALUATION_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_VALUATION_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.warning("Failed to write valuation record", exc_info=True)
