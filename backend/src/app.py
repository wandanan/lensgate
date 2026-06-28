"""
FastAPI application — TLMA (Text LLM Multimodal Agent).

Path-based target routing + decision-engine attention layer.
"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Request

from backend.src.cache_store import cache
from backend.src.config import ProxyConfig
from backend.src.decision_engine import DecisionEngine
from backend.src.error_handler import (
    InvalidRequestError,
    PayloadTooLargeError,
    TargetModelTimeoutError,
    TargetModelUnavailableError,
    check_config,
    register_error_handlers,
)
from backend.src.format_detector import detect_format, parse_anthropic_request, parse_openai_request
from backend.src.image_extractor import (
    extract_file_metadata,
    extract_images,
    image_hash,
)
from backend.src.logging_config import setup_logging
from backend.src.middleware.auth import APIKeyMiddleware
from backend.src.models import ImageBlock, ProxyRequest, TargetModelConfig
from backend.src.request_rewriter import RequestRewriter
from backend.src.response_handler import ResponseHandler
from backend.src.target_client import TargetModelClient
from backend.src.vision_client import QwenVisionClient

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

config = ProxyConfig()
setup_logging()
logger = logging.getLogger(__name__)

vision_client = QwenVisionClient(config)
rewriter = RequestRewriter()
response_handler = ResponseHandler()
decision_engine = DecisionEngine(
    api_key=config.decision_api_key,
    base_url=config.decision_base_url,
    model=config.decision_model,
    timeout=config.decision_timeout,
)

_target_client: TargetModelClient | None = None


def _get_target_client() -> TargetModelClient:
    global _target_client
    if _target_client is None:
        _target_client = TargetModelClient()
    return _target_client


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    if _target_client is not None:
        await _target_client.close()
    await vision_client.close()
    await decision_engine.close()


app = FastAPI(title="TLMA - Text LLM Multimodal Agent", lifespan=_lifespan)
app.add_middleware(APIKeyMiddleware, api_key=config.proxy_api_key)
register_error_handlers(app)
check_config(config)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "timestamp": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# API — HTTP layer (thin, extracts data from Request)
# ---------------------------------------------------------------------------


@app.post("/{target:path}/v1/messages")
async def anthropic_messages(request: Request, target: str):
    return await _run_pipeline(request, target)


@app.post("/{target:path}/v1/chat/completions")
async def openai_chat_completions(request: Request, target: str):
    return await _run_pipeline(request, target)


async def _run_pipeline(request: Request, target: str):
    # --- Content-Length ---
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > 10 * 1024 * 1024:
                raise PayloadTooLargeError("Request body exceeds 10 MB limit")
        except ValueError:
            pass

    # --- Parse JSON ---
    try:
        body = await request.json()
    except Exception:
        raise InvalidRequestError("Failed to parse JSON request body")

    # --- Target config ---
    target_base_url = _resolve_target_base_url(request, target)
    target_api_key = getattr(request.state, "target_api_key", "")
    target_config = TargetModelConfig(
        model_id=body.get("model", ""),
        api_base=target_base_url,
        api_key=target_api_key,
    )

    return await _execute_pipeline(
        body=body,
        path=str(request.url.path),
        target_config=target_config,
    )


# ---------------------------------------------------------------------------
# Pipeline — business logic (testable, no HTTP dependency)
# ---------------------------------------------------------------------------


async def _execute_pipeline(body: dict, path: str, target_config: TargetModelConfig):
    messages = body.get("messages")
    if not messages or (isinstance(messages, list) and len(messages) == 0):
        raise InvalidRequestError("messages field is required and must be non-empty")

    # --- Stage 1: Format detect ---
    fmt = detect_format(path, body)
    proxy_request = (
        parse_anthropic_request(body) if fmt == "anthropic"
        else parse_openai_request(body)
    )

    # --- Stage 2: Image check + short-circuit ---
    new_images = await extract_images(proxy_request, latest_only=True)

    if not new_images and not cache.entries():
        logger.info("Pure-text fast path (no images, no cache)")
        return await _forward(proxy_request.original_body, target_config, proxy_request.stream)

    # --- Stage 3: Decision engine (only when images or cache exist) ---
    user_msgs = _extract_user_messages(proxy_request, last_n=5)
    cached = cache.entries()

    decision = await decision_engine.decide(user_msgs, cached, new_image_count=len(new_images))
    logger.info("Decision: mode=%s hashes=%s focus=%.80s reasoning=%s",
                decision.mode, decision.image_hashes, decision.focus_prompt, decision.reasoning)

    if new_images:
        logger.info("New images in latest message: %d", len(new_images))
        vision_results = await _vision_and_cache(new_images, decision, proxy_request)
        proxy_request = rewriter.rewrite(proxy_request, vision_results)
        return await _forward(proxy_request.original_body, target_config, proxy_request.stream)

    # No new images — use decision result.
    if not decision.image_hashes:
        logger.info("Pure-text fast path (decision: no relevant images)")
        return await _forward(proxy_request.original_body, target_config, proxy_request.stream)

    # Historical images: extract by hash
    all_images = await extract_images(proxy_request, latest_only=False)
    target_images, seen_hashes = _filter_images_by_hash(all_images, decision.image_hashes)

    if not target_images:
        logger.warning("Decision requested images not found in body")
        return await _forward(proxy_request.original_body, target_config, proxy_request.stream)

    logger.info("[RE-VISION] %d images from history, mode=%s", len(target_images), decision.mode)
    vision_results = await _vision_and_cache(target_images, decision, proxy_request)
    proxy_request = rewriter.rewrite(proxy_request, vision_results)
    return await _forward(proxy_request.original_body, target_config, proxy_request.stream)


# ---------------------------------------------------------------------------
# Vision + cache helper (shared across new_images and historical branches)
# ---------------------------------------------------------------------------


async def _vision_and_cache(
    images: list[ImageBlock],
    decision,
    proxy_request: ProxyRequest,
) -> list[tuple[ImageBlock, str]]:
    focus = decision.focus_prompt or "请描述这张图片的内容"

    if decision.mode == "compare" and len(images) >= 2:
        return await _vision_compare_locked(images, focus, proxy_request)

    logger.info("[SINGLE] %d image(s) — individual vision calls", len(images))
    results: list[tuple[ImageBlock, str]] = []
    for img in images:
        h = image_hash(img)
        if not h:
            desc = await vision_client.recognize(img, focus)
            results.append((img, desc))
            continue

        cached_hit = cache.get(h, focus)
        if cached_hit:
            results.append((img, cached_hit))
            logger.info("  [CACHE HIT] %s", h[:12])
            continue

        lock = cache.acquire_lock(h)
        await lock.acquire()
        try:
            cached_hit = cache.get(h, focus)
            if cached_hit:
                results.append((img, cached_hit))
                logger.info("  [CACHE HIT] %s (after lock)", h[:12])
                continue
            desc = await vision_client.recognize(img, focus)
            logger.info("  [VISION OUTPUT] %.300s", desc)
            fname, pos = extract_file_metadata(proxy_request, img)
            cache.set(h, desc, focus, fname, pos, _make_label(desc))
            results.append((img, desc))
            logger.info("  [VISION OK] %s", h[:12])
        finally:
            cache.release_lock(h)
    return results


async def _vision_compare_locked(
    images: list[ImageBlock],
    focus: str,
    proxy_request: ProxyRequest,
) -> list[tuple[ImageBlock, str]]:
    """Compare mode with per-hash locking — acquire all locks before calling vision."""
    logger.info("[COMPARE] %d images in ONE vision call | focus=%s", len(images), focus[:100])

    hashes = [image_hash(img) for img in images]
    locks: list[tuple[str, asyncio.Lock]] = []
    for h in sorted(h for h in hashes if h):
        lock = cache.acquire_lock(h)
        await lock.acquire()
        locks.append((h, lock))

    try:
        desc = await vision_client.recognize_compare(images, focus)
        logger.info("[VISION OUTPUT] %s", desc[:500])
        for img in images:
            h = image_hash(img)
            if h:
                fname, pos = extract_file_metadata(proxy_request, img)
                cache.set(h, desc, focus, fname, pos, _make_label(desc))
        return [(img, desc) for img in images]
    finally:
        for h, lock in locks:
            cache.release_lock(h)


# ---------------------------------------------------------------------------
# Forwarding
# ---------------------------------------------------------------------------


async def _forward(body: dict, target_config: TargetModelConfig, stream: bool):
    client = _get_target_client()
    try:
        if stream:
            gen = client.forward_stream(body, target_config)
            return response_handler.handle_stream(gen, "anthropic")
        else:
            resp = await client.forward(body, target_config)
            return await response_handler.handle_non_stream(resp, "anthropic")
    except httpx.TimeoutException as e:
        raise TargetModelTimeoutError(str(e)) from e
    except httpx.HTTPStatusError as e:
        if e.response.status_code >= 500:
            raise TargetModelUnavailableError(str(e)) from e
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_target_base_url(request: Request, target: str) -> str:
    """Resolve target API base URL from x-target-base-url header, or path routing.

    Header-based routing (preferred)::
        x-target-base-url: https://ark.cn-beijing.volces.com/api/coding

    Path-based routing (fallback)::
        POST /{host}/v1/messages  →  https://{host}
    """
    header_url = request.headers.get("x-target-base-url")
    if header_url:
        return header_url.rstrip("/")

    scheme = "https"
    t = target
    if t.startswith("http."):
        scheme = "http"
        t = t[5:]

    logger.info("Target: %s://%s", scheme, t)
    return f"{scheme}://{t}"


def _filter_images_by_hash(
    images: list[ImageBlock],
    requested_hashes: list[str],
) -> tuple[list[ImageBlock], set[str]]:
    target: list[ImageBlock] = []
    seen: set[str] = set()
    for img in images:
        h = image_hash(img)
        if h and h in requested_hashes and h not in seen:
            seen.add(h)
            target.append(img)
    return target, seen


def _make_label(desc: str) -> str:
    if not desc:
        return ""
    return desc.strip()[:40]


_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def _extract_user_messages(request: ProxyRequest, last_n: int = 5) -> list[str]:
    from backend.src.models import TextBlock, ToolResultBlock

    result: list[str] = []
    for msg in request.messages:
        if msg.role != "user":
            continue
        if all(isinstance(b, ToolResultBlock) for b in msg.content):
            continue
        text = ""
        for block in msg.content:
            if isinstance(block, TextBlock):
                text += block.text + " "
        text = _SYSTEM_REMINDER_RE.sub("", text).strip()
        if text:
            result.append(text)
    return result[-last_n:]
