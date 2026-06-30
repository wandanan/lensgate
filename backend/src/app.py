"""
FastAPI application — TLMA (Text LLM Multimodal Agent).

Path-based target routing + decision-engine attention layer.
"""

import asyncio
import copy
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.src.pipeline.cache_store import cache
from backend.src.core.config import ProxyConfig
from backend.src.pipeline.decision_engine import DecisionEngine, DecisionResult
from backend.src.core.error_handler import (
    InvalidRequestError,
    PayloadTooLargeError,
    TargetModelTimeoutError,
    TargetModelUnavailableError,
    check_config,
    register_error_handlers,
)
from backend.src.pipeline.format_detector import detect_format, parse_anthropic_request, parse_openai_request
from backend.src.pipeline.image_extractor import (
    extract_file_metadata,
    extract_images,
    has_images,
    image_hash,
)
from backend.src.core.logging_config import setup_logging
from backend.src.middleware.auth import APIKeyMiddleware
from backend.src.core.models import ImageBlock, ProxyRequest, TargetModelConfig
from backend.src.pipeline.request_rewriter import RequestRewriter
from backend.src.pipeline.response_handler import ResponseHandler
from backend.src.pipeline.target_client import TargetModelClient
from backend.src.pipeline.vision_client import OpenAICompatibleVisionClient

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

config = ProxyConfig()
setup_logging()
logger = logging.getLogger(__name__)

vision_client = OpenAICompatibleVisionClient(config)
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
    from backend.src.dashboard.trace import buffer
    restored = buffer.restore()
    if restored:
        logger.info("Restored %d trace(s) from SQLite", restored)
    yield
    if _target_client is not None:
        await _target_client.close()
    await vision_client.close()
    await decision_engine.close()


app = FastAPI(title="TLMA - Text LLM Multimodal Agent", lifespan=_lifespan)
app.add_middleware(APIKeyMiddleware, api_key=config.proxy_api_key)
register_error_handlers(app)
check_config(config)

# Dashboard API (must register before catch-all)
from backend.src.dashboard.api import router as dashboard_router
app.include_router(dashboard_router)

# Dashboard frontend static files
from pathlib import Path as _Path
_dist = _Path(__file__).parent.parent.parent / "dashboard" / "dist"
if _dist.exists():
    app.mount("/dashboard", StaticFiles(directory=str(_dist), html=True), name="dashboard")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "timestamp": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# API — HTTP layer (thin, extracts data from Request)
# ---------------------------------------------------------------------------


@app.api_route("/{target:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
async def proxy_endpoint(request: Request, target: str):
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

    # --- Target URL (uses request path as-is, no suffix appended) ---
    target_url = _resolve_target_url(request, target)
    target_api_key = getattr(request.state, "target_api_key", "")

    # --- Non-POST (GET/HEAD/OPTIONS) pass-through without pipeline ---
    if request.method != "POST":
        return await _forward_raw(request, target_url, target_api_key)

    # --- Non-conversational POST endpoints (count_tokens, etc.) ---
    # These have a different request/response shape than /v1/messages and
    # must bypass the vision/rewrite pipeline. detect_format() only knows
    # /v1/messages and /v1/chat/completions, so any other POST suffix
    # (e.g. /v1/messages/count_tokens) would otherwise raise ValueError → 500.
    if _is_passthrough_path(str(request.url.path)):
        return await _forward_passthrough(request, target_url, target_api_key)

    # --- Parse JSON ---
    try:
        body = await request.json()
    except Exception:
        raise InvalidRequestError("Failed to parse JSON request body")

    target_config = TargetModelConfig(
        model_id=body.get("model", ""),
        api_base=target_url,
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
    logger.debug("Format: %s model=%s msgs=%d stream=%s",
                 fmt, body.get("model", "?"),
                 len(body.get("messages", [])),
                 body.get("stream", False))
    proxy_request = (
        parse_anthropic_request(body) if fmt == "anthropic"
        else parse_openai_request(body)
    )

    # --- Stage 2: Image check ---
    new_images = await extract_images(proxy_request, latest_only=True)
    logger.debug("Image check: new_images=%d has_any_images=%s",
                 len(new_images), has_images(proxy_request))

    # PATH C — pure text (no images anywhere in this request)
    if not new_images and not has_images(proxy_request):
        logger.info("Pure-text fast path (no images in request)")
        return await _forward(proxy_request.original_body, target_config, proxy_request.stream)

    # --- Stage 3: Decision engine (only when cache has entries to select from) ---
    cached = cache.entries()

    if cached:
        user_msgs = _extract_user_messages(proxy_request, last_n=5)
        decision = await decision_engine.decide(user_msgs, cached, new_image_count=len(new_images))
    else:
        decision = _default_decision(len(new_images))

    logger.info("Decision: mode=%s images=%d focus=%.40s",
                decision.mode, len(decision.image_hashes),
                decision.focus_prompt[:40])

    # PATH A/B — new images in latest message
    if new_images:
        logger.info("New images in latest message: %d", len(new_images))
        # Extract ALL images (including earlier messages) — latest_only misses
        # images in tool_result from previous Read tool calls.
        all_images = await extract_images(proxy_request, latest_only=False)
        if len(all_images) > len(new_images):
            logger.info("Also processing %d images from earlier messages",
                        len(all_images) - len(new_images))
        vision_results = await _vision_and_cache(all_images, decision, proxy_request)
        proxy_request = rewriter.rewrite(proxy_request, vision_results)
        return await _forward(proxy_request.original_body, target_config, proxy_request.stream)

    # --- Historical images only ---

    # PATH E — no cache: extract all, run vision on all
    if not cached:
        logger.info("Images in history (no cache), extracting all")
        all_images = await extract_images(proxy_request, latest_only=False)
        decision = _default_decision(len(all_images))
        vision_results = await _vision_and_cache(all_images, decision, proxy_request)
        proxy_request = rewriter.rewrite(proxy_request, vision_results)
        return await _forward(proxy_request.original_body, target_config, proxy_request.stream)

    # PATH D — decision engine selects which historical images to re-vision
    if not decision.image_hashes:
        # Decision engine failed (retries exhausted) — fall back to
        # processing ALL images instead of silently skipping.
        if "retries exhausted" in decision.reasoning:
            logger.warning("Decision engine failed, processing all images")
            all_images = await extract_images(proxy_request, latest_only=False)
            fallback = _default_decision(len(all_images))
            vision_results = await _vision_and_cache(all_images, fallback, proxy_request)
            proxy_request = rewriter.rewrite(proxy_request, vision_results)
            return await _forward(proxy_request.original_body, target_config, proxy_request.stream)

        logger.info("Decision: skip (no relevant images for current question)")
        all_images = await extract_images(proxy_request, latest_only=False)
        vision_results = _lookup_cached(all_images)
        if len(vision_results) < len(all_images):
            logger.warning(
                "Skip path: %d/%d images not cached, processing all",
                len(all_images) - len(vision_results), len(all_images),
            )
            fallback = _default_decision(len(all_images))
            vision_results = await _vision_and_cache(all_images, fallback, proxy_request)
        if vision_results:
            proxy_request = rewriter.rewrite(proxy_request, vision_results)
        return await _forward(proxy_request.original_body, target_config, proxy_request.stream)

    all_images = await extract_images(proxy_request, latest_only=False)
    target_images, seen_hashes = _filter_images_by_hash(all_images, decision.image_hashes)

    if not target_images:
        logger.warning("Decision requested images not found in body, processing all")
        fallback = _default_decision(len(all_images))
        vision_results = await _vision_and_cache(all_images, fallback, proxy_request)
        proxy_request = rewriter.rewrite(proxy_request, vision_results)
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

    # --- Replicate mode: extract CSS variables instead of text description ---
    if decision.mode == "replicate":
        return await _vision_replicate(images, proxy_request)

    logger.info("[SINGLE] %d image(s)", len(images))
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
            logger.debug("  [VISION OUTPUT] %.300s", desc)
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
    logger.info("[COMPARE] %d images in ONE vision call", len(images))

    hashes = [image_hash(img) for img in images]
    image_hash_pairs = list(zip(images, hashes))
    cache_focus = _compare_cache_focus(focus, hashes)
    cache_hits = {
        h: cache.get(h, cache_focus)
        for h in hashes
        if h
    }
    if hashes and all(h and cache_hits.get(h) for h in hashes):
        logger.info("[COMPARE CACHE HIT] %d image(s)", len(images))
        return [(img, cache_hits[h]) for img, h in image_hash_pairs if h]

    locks: list[tuple[str, asyncio.Lock]] = []
    for h in sorted({h for h in hashes if h}):
        lock = cache.acquire_lock(h)
        await lock.acquire()
        locks.append((h, lock))

    try:
        cache_hits = {
            h: cache.get(h, cache_focus)
            for h in hashes
            if h
        }
        if hashes and all(h and cache_hits.get(h) for h in hashes):
            logger.info("[COMPARE CACHE HIT] %d image(s) after lock", len(images))
            return [(img, cache_hits[h]) for img, h in image_hash_pairs if h]

        desc = await vision_client.recognize_compare(images, focus)
        logger.debug("[VISION OUTPUT] %s", desc[:500])
        for img in images:
            h = image_hash(img)
            if h:
                fname, pos = extract_file_metadata(proxy_request, img)
                cache.set(h, desc, cache_focus, fname, pos, _make_label(desc))
        return [(img, desc) for img in images]
    finally:
        for h, lock in locks:
            cache.release_lock(h)


async def _vision_replicate(
    images: list[ImageBlock],
    proxy_request: ProxyRequest,
) -> list[tuple[ImageBlock, str]]:
    """Replicate mode: extract CSS variables per image, with caching.

    Each image is sent individually to the vision model with a specialised
    prompt that outputs ``:root { --bg: #xxx; ... }`` CSS custom properties.
    The CSS block replaces the image in the downstream request so the target
    model receives precise design values instead of vague "warm yellow" text.
    """
    logger.info("[REPLICATE] %d image(s)", len(images))
    results: list[tuple[ImageBlock, str]] = []
    # replicate mode uses a fixed prompt — cache key focus is always ""
    focus = ""

    for img in images:
        h = image_hash(img)
        if not h:
            css = await vision_client.recognize_replicate(img)
            # Degrade to text description if CSS extraction fails.
            if not css:
                css = await vision_client.recognize(img, "请描述这张图片的视觉设计规范，包括颜色、字体、间距。")
            results.append((img, css))
            continue

        cached_hit = cache.get(h, focus)
        if cached_hit:
            results.append((img, cached_hit))
            logger.info("  [CACHE HIT] %s (replicate)", h[:12])
            continue

        lock = cache.acquire_lock(h)
        await lock.acquire()
        try:
            cached_hit = cache.get(h, focus)
            if cached_hit:
                results.append((img, cached_hit))
                logger.info("  [CACHE HIT] %s (replicate, after lock)", h[:12])
                continue
            css = await vision_client.recognize_replicate(img)
            if not css:
                css = await vision_client.recognize(img, "请描述这张图片的视觉设计规范，包括颜色、字体、间距。")
            logger.debug("  [VI-SPEC OUTPUT] %.200s", css.strip().replace('\n', ' '))
            fname, pos = extract_file_metadata(proxy_request, img)
            cache.set(h, css, focus, fname, pos, _make_label(css))
            results.append((img, css))
            logger.info("  [VI-SPEC OK] %s", h[:12])
        finally:
            cache.release_lock(h)
    return results


# ---------------------------------------------------------------------------
# Forwarding
# ---------------------------------------------------------------------------


async def _forward_raw(request: Request, target_url: str, api_key: str):
    """Pass-through GET/HEAD/OPTIONS to target without pipeline processing."""
    client = _get_target_client()
    resp = await client.forward_raw(
        method=request.method,
        url=target_url,
        headers=dict(request.headers),
        api_key=api_key,
    )
    from fastapi.responses import Response
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )


async def _forward_passthrough(request: Request, target_url: str, api_key: str):
    """Pass-through a POST request verbatim (headers + body) to the target.

    Used for non-conversational endpoints (e.g. count_tokens) whose
    request/response shape differs from /v1/messages and must skip the
    vision/rewrite pipeline. The full target URL (with suffix) is preserved.
    """
    body = await request.body()
    client = _get_target_client()
    resp = await client.forward_passthrough(
        method=request.method,
        url=target_url,
        headers=dict(request.headers),
        body=body,
        api_key=api_key,
    )
    from fastapi.responses import Response
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )


async def _forward(body: dict, target_config: TargetModelConfig, stream: bool):
    # Strip any raw images that slipped past the rewriter (last-resort safety net).
    body = _strip_images_from_body(body)

    logger.debug(
        "Forward: model=%s stream=%s body_bytes=%d url=%s",
        target_config.model_id, stream,
        len(json.dumps(body)), target_config.api_base,
    )

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
        logger.warning(
            "Target HTTP %d: %.300s",
            e.response.status_code,
            e.response.text[:300],
        )
        if e.response.status_code >= 500:
            raise TargetModelUnavailableError(str(e)) from e
        # 4xx: pass through the target's own error response
        return JSONResponse(
            status_code=e.response.status_code,
            content=e.response.json() if e.response.text else {"error": "target_error"},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_passthrough_path(path: str) -> bool:
    """Return True for POST endpoints that must bypass the pipeline.

    Conversational endpoints (/v1/messages, /v1/chat/completions) go through
    the vision/rewrite pipeline. Anything else — e.g. Anthropic's
    /v1/messages/count_tokens — has a different request/response shape and
    is forwarded verbatim. detect_format() would otherwise raise ValueError.
    """
    clean = path.split("?")[0].rstrip("/")
    if clean.endswith("/v1/messages"):
        return False
    if clean.endswith("/v1/chat/completions"):
        return False
    return True


def _resolve_target_url(request: Request, target: str) -> str:
    """Resolve the full target URL from x-target-base-url header or path routing.

    The client request path is forwarded as-is — no suffix is appended.

    Header-based routing::
        x-target-base-url: https://api.deepseek.com/anthropic
        Request:           POST /v1/messages
        →                  https://api.deepseek.com/anthropic/v1/messages

    Path-based routing::
        POST /api.deepseek.com/anthropic/v1/messages?beta=true
        →                  https://api.deepseek.com/anthropic/v1/messages?beta=true
    """
    header_url = request.headers.get("x-target-base-url")
    if header_url:
        return header_url.rstrip("/") + "/" + request.url.path.lstrip("/")

    t = target
    scheme = "https"
    if t.startswith("http."):
        scheme = "http"
        t = t[5:]

    url = f"{scheme}://{t}"
    if request.url.query:
        url += "?" + request.url.query

    logger.info("Target: %s", url)
    return url


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


def _default_decision(image_count: int = 0) -> DecisionResult:
    """Build a default decision when there's no cache to consult."""
    mode = "compare" if image_count >= 2 else "single"
    return DecisionResult(
        image_hashes=[],
        focus_prompt="请描述这张图片的内容",
        mode=mode,
        reasoning="no cache — processing all images",
    )


def _compare_cache_focus(focus: str, hashes: list[str | None]) -> str:
    """Build a cache key for a specific compare call.

    Compare output describes the relationship among a set of images, so it must
    not be reused as a single-image description with the same focus prompt.
    """
    joined_hashes = ",".join(h for h in hashes if h)
    return f"compare:{focus}:{joined_hashes}"


def _make_label(desc: str) -> str:
    if not desc:
        return ""
    return desc.strip()[:40]


def _lookup_cached(images: list[ImageBlock]) -> list[tuple[ImageBlock, str]]:
    """Build vision_results from cache for images that have cached descriptions."""
    results: list[tuple[ImageBlock, str]] = []
    for img in images:
        h = image_hash(img)
        if h:
            desc = cache.get(h)
            if desc:
                results.append((img, desc))
    return results


def _strip_images_from_body(body: dict) -> dict:
    """Remove all image blocks from the request body (last-resort safety net).

    Image blocks MUST be replaced by the rewriter before reaching _forward.
    This function catches any that slipped through — raw images must never
    reach the target model.
    """
    stripped = 0

    def _scan(blocks):
        nonlocal stripped
        if not isinstance(blocks, list):
            return
        for i, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            bt = block.get("type", "")
            if bt in ("image", "image_url"):
                src = block.get("source", {})
                logger.error(
                    "SAFEGUARD: stripping raw image before forward "
                    "(source=%s, media=%s, data_len=%d) — this is a bug, "
                    "rewriter should have replaced it",
                    src.get("type"), src.get("media_type", "?"),
                    len(src.get("data", "")),
                )
                blocks[i] = {"type": "text", "text": "[图片]"}
                stripped += 1
            elif bt == "tool_result":
                _scan(block.get("content"))

    body = copy.deepcopy(body)
    for msg in body.get("messages", []):
        _scan(msg.get("content"))

    if stripped:
        logger.error(
            "SAFEGUARD: stripped %d raw image block(s) — pipeline bug, "
            "images should be handled before _forward", stripped,
        )
    return body


_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def _extract_user_messages(request: ProxyRequest, last_n: int = 5) -> list[str]:
    from backend.src.core.models import TextBlock, ToolResultBlock

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
