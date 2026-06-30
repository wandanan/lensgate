"""
Pipeline orchestration — links format-detection, image-extraction, decision,
vision, rewriting, and target-forwarding into a single ``process_request``
entry-point with built-in trace recording.
"""

from __future__ import annotations

import copy
import logging
import re
import time
from typing import Callable

import httpx
from fastapi.responses import JSONResponse

from backend.src.pipeline.cache_store import cache
from backend.src.core.config import ProxyConfig
from backend.src.pipeline.decision_engine import DecisionEngine, DecisionResult
from backend.src.core.error_handler import (
    InvalidRequestError,
    TargetModelTimeoutError,
    TargetModelUnavailableError,
)
from backend.src.pipeline.format_detector import detect_format, parse_anthropic_request, parse_openai_request
from backend.src.pipeline.image_extractor import (
    extract_file_metadata,
    extract_images,
    has_images,
    image_hash,
)
from backend.src.core.models import ImageBlock, ProxyRequest, TargetModelConfig, TextBlock, ToolResultBlock
from backend.src.pipeline.request_rewriter import RequestRewriter
from backend.src.pipeline.response_handler import ResponseHandler
from backend.src.pipeline.target_client import TargetModelClient
from backend.src.pipeline.vision_client import QwenVisionClient
from backend.src.pipeline.vision_client import FALLBACK_TEXT

# Trace hooks -- imported at call time to avoid circular import issues
# with replay_request which imports back from this module.
from backend.src.dashboard.trace import (
    StageSnapshot,
    buffer,
    finalize_trace,
    record_stage,
    start_trace,
)

# ---------------------------------------------------------------------------
# Module-level components (lazy / singleton)
# ---------------------------------------------------------------------------

_config: ProxyConfig | None = None
_vision_client: QwenVisionClient | None = None
_rewriter: RequestRewriter | None = None
_response_handler: ResponseHandler | None = None
_decision_engine: DecisionEngine | None = None
_target_client: TargetModelClient | None = None

logger = logging.getLogger(__name__)


def _ensure_components() -> None:
    """Lazily initialise pipeline components from environment config."""
    global _config, _vision_client, _rewriter, _response_handler, _decision_engine

    if _config is None:
        _config = ProxyConfig()
        # logging is initialised by app.py at import time

    if _vision_client is None:
        _vision_client = QwenVisionClient(_config)

    if _rewriter is None:
        _rewriter = RequestRewriter()

    if _response_handler is None:
        _response_handler = ResponseHandler()

    if _decision_engine is None:
        _decision_engine = DecisionEngine(
            api_key=_config.decision_api_key,
            base_url=_config.decision_base_url,
            model=_config.decision_model,
            timeout=_config.decision_timeout,
        )


def _get_target_client() -> TargetModelClient:
    """Return the module-level TargetModelClient singleton."""
    global _target_client
    if _target_client is None:
        _target_client = TargetModelClient()
    return _target_client


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


async def process_request(
    body: dict,
    path: str,
    target_config: TargetModelConfig,
    get_target_client_fn: Callable[[], TargetModelClient] | None = None,
):
    """Execute the full proxy pipeline with per-stage tracing.

    Parameters:
        body: Parsed JSON request body.
        path: Request URL path (e.g. ``"/v1/messages"``).
        target_config: Routing information for the target model.
        get_target_client_fn: Optional factory for the target client.
            When ``None`` the module-level singleton is used.  Tests can pass
            a mock factory to intercept target calls without patching module
            internals.

    Returns:
        A FastAPI ``Response`` (``StreamingResponse`` or ``JSONResponse``).
    """
    _ensure_components()

    tc = get_target_client_fn or _get_target_client

    # --- Begin trace ---
    trace_id = start_trace(body, path)
    pipeline_start = time.time()

    try:
        return await _execute_core(body, path, target_config, tc, trace_id, pipeline_start)
    except Exception:
        total_ms = (time.time() - pipeline_start) * 1000
        finalize_trace(trace_id, 500, total_ms)
        raise


# ---------------------------------------------------------------------------
# Core pipeline (called by process_request after trace init)
# ---------------------------------------------------------------------------


async def _execute_core(
    body: dict,
    path: str,
    target_config: TargetModelConfig,
    get_tc: Callable[[], TargetModelClient],
    trace_id: str,
    pipeline_start: float,
):
    messages = body.get("messages")
    if not messages or (isinstance(messages, list) and len(messages) == 0):
        raise InvalidRequestError("messages field is required and must be non-empty")

    # --- Stage 1: Format detect ---
    t0 = time.time()
    fmt = detect_format(path, body)
    logger.debug("Format: %s model=%s msgs=%d stream=%s",
                 fmt, body.get("model", "?"),
                 len(body.get("messages", [])),
                 body.get("stream", False))
    proxy_request = (
        parse_anthropic_request(body) if fmt == "anthropic"
        else parse_openai_request(body)
    )
    record_stage(trace_id, StageSnapshot(
        stage="format_detect",
        input={"path": path, "model": body.get("model", "")},
        output={"detected_format": fmt, "message_count": len(body.get("messages", [])),
                "stream": body.get("stream", False)},
        duration_ms=(time.time() - t0) * 1000,
        status="ok",
    ))

    # --- Stage 2: Image check ---
    t0 = time.time()
    new_images = await extract_images(proxy_request, latest_only=True)
    logger.debug("Image check: new_images=%d has_any_images=%s",
                 len(new_images), has_images(proxy_request))
    has_any_images = bool(new_images) or has_images(proxy_request)
    content_blocks = 0
    text_blocks = 0
    image_blocks = 0
    for msg in proxy_request.messages:
        for block in msg.content:
            content_blocks += 1
            if isinstance(block, TextBlock):
                text_blocks += 1
            elif isinstance(block, ImageBlock):
                image_blocks += 1
    record_stage(trace_id, StageSnapshot(
        stage="image_check",
        input={"message_count": len(proxy_request.messages)},
        output={"content_blocks": content_blocks, "text_blocks": text_blocks,
                "image_blocks": image_blocks,
                "new_images": len(new_images), "has_images": has_any_images,
                "total_images": _count_all_images(proxy_request)},
        duration_ms=(time.time() - t0) * 1000,
        status="ok",
    ))

    # PATH C — pure text (no images anywhere in this request)
    if not new_images and not has_images(proxy_request):
        logger.info("Pure-text fast path (no images in request)")

        # Skipped stages for pure-text fast path
        for stage_name in ("decision", "vision", "rewrite"):
            record_stage(trace_id, StageSnapshot(
                stage=stage_name,
                input={},
                output={"reason": "pure_text_fast_path"},
                duration_ms=0,
                status="skipped",
            ))

        response = await _forward(
            proxy_request.original_body, target_config,
            proxy_request.stream, trace_id, get_tc,
        )
        total_ms = (time.time() - pipeline_start) * 1000
        finalize_trace(trace_id, 200, total_ms)
        return response

    # --- Stage 3: Decision engine ---
    t0 = time.time()
    cached = cache.entries()
    user_msgs = _extract_user_messages(proxy_request, last_n=5)
    decision = await _decision_engine.decide(user_msgs, cached, new_image_count=len(new_images))
    logger.info("Decision: mode=%s images=%d focus=%.40s",
                decision.mode, len(decision.image_hashes),
                decision.focus_prompt[:40])
    decision_input = {
        "image_count": len(new_images),
        "cache_entries": len(cached),
        "user_messages": _extract_user_messages(proxy_request, last_n=5),
        "system_prompt": _decision_engine.SYSTEM_PROMPT,
        "constructed_prompt": _decision_engine._build_prompt(
            _extract_user_messages(proxy_request, last_n=5),
            cached,
            len(new_images),
        ),
        "model": _decision_engine._model,
        "endpoint": _decision_engine._base_url,
        "max_tokens": 400,
        "temperature": 0.1,
        "max_attempts": 2,
    }
    record_stage(trace_id, StageSnapshot(
        stage="decision",
        input=decision_input,
        output={"mode": decision.mode, "hashes": decision.image_hashes,
                "focus_prompt": decision.focus_prompt,
                "reasoning": decision.reasoning,
                "raw_json": decision.raw_output,
                "attempt": decision.attempt},
        duration_ms=(time.time() - t0) * 1000,
        status="ok",
    ))

    # PATH A/B — new images in latest message
    if new_images:
        logger.info("New images in latest message: %d", len(new_images))

        # --- Stage 4: Vision ---
        t0 = time.time()
        all_images = await extract_images(proxy_request, latest_only=False)
        if len(all_images) > len(new_images):
            logger.info("Also processing %d images from earlier messages",
                        len(all_images) - len(new_images))
        vision_results = await _vision_and_cache(all_images, decision, proxy_request)
        _vision_prompt = _compute_vision_prompt(decision.mode, decision.focus_prompt)
        record_stage(trace_id, StageSnapshot(
            stage="vision",
            input=_build_vision_input(all_images, decision.mode, decision.focus_prompt, prompt=_vision_prompt),
            output=_build_vision_output(all_images, vision_results, decision.mode, decision.focus_prompt, prompt=_vision_prompt),
            duration_ms=(time.time() - t0) * 1000,
            status="ok",
        ))

        # --- Stage 5: Rewrite ---
        t0 = time.time()
        _body_before = dict(proxy_request.original_body) if proxy_request.original_body else {}
        proxy_request = _rewriter.rewrite(proxy_request, vision_results)
        _body_after = dict(proxy_request.original_body) if proxy_request.original_body else {}
        record_stage(trace_id, StageSnapshot(
            stage="rewrite",
            input={"original_body": _body_before, "vision_results": len(vision_results),
                   "image_block_count": len(new_images)},
            output={"replaced_images": len(new_images), "rewritten_body": _body_after},
            duration_ms=(time.time() - t0) * 1000,
            status="ok",
        ))

        response = await _forward(
            proxy_request.original_body, target_config,
            proxy_request.stream, trace_id, get_tc,
        )
        total_ms = (time.time() - pipeline_start) * 1000
        finalize_trace(trace_id, 200, total_ms)
        return response

    # --- Historical images only ---

    # PATH E — no cache: extract all, run vision on all
    if not cached:
        logger.info("Images in history (no cache), extracting all")

        t0 = time.time()
        all_images = await extract_images(proxy_request, latest_only=False)
        decision = _default_decision(len(all_images))
        vision_results = await _vision_and_cache(all_images, decision, proxy_request)
        _vision_prompt = _compute_vision_prompt(decision.mode, decision.focus_prompt)
        record_stage(trace_id, StageSnapshot(
            stage="vision",
            input=_build_vision_input(all_images, decision.mode, decision.focus_prompt, prompt=_vision_prompt),
            output=_build_vision_output(all_images, vision_results, decision.mode, decision.focus_prompt, prompt=_vision_prompt),
            duration_ms=(time.time() - t0) * 1000,
            status="ok",
        ))

        t0 = time.time()
        _body_before_v2 = dict(proxy_request.original_body) if proxy_request.original_body else {}
        proxy_request = _rewriter.rewrite(proxy_request, vision_results)
        _body_after_v2 = dict(proxy_request.original_body) if proxy_request.original_body else {}
        record_stage(trace_id, StageSnapshot(
            stage="rewrite",
            input={"original_body": _body_before_v2, "vision_results": len(vision_results),
                   "image_block_count": len(all_images)},
            output={"replaced_images": len(all_images), "rewritten_body": _body_after_v2},
            duration_ms=(time.time() - t0) * 1000,
            status="ok",
        ))

        response = await _forward(
            proxy_request.original_body, target_config,
            proxy_request.stream, trace_id, get_tc,
        )
        total_ms = (time.time() - pipeline_start) * 1000
        finalize_trace(trace_id, 200, total_ms)
        return response

    # PATH D — decision engine selects which historical images to re-vision
    if not decision.image_hashes:
        if "retries exhausted" in decision.reasoning:
            logger.warning("Decision engine failed, processing all images")

            t0 = time.time()
            all_images = await extract_images(proxy_request, latest_only=False)
            fallback = _default_decision(len(all_images))
            vision_results = await _vision_and_cache(all_images, fallback, proxy_request)
            _vision_prompt = _compute_vision_prompt(fallback.mode, fallback.focus_prompt)
            record_stage(trace_id, StageSnapshot(
                stage="vision",
                input=_build_vision_input(all_images, fallback.mode, fallback.focus_prompt, prompt=_vision_prompt, reason="decision_fallback"),
                output=_build_vision_output(all_images, vision_results, fallback.mode, fallback.focus_prompt, prompt=_vision_prompt),
                duration_ms=(time.time() - t0) * 1000,
                status="ok",
            ))

            t0 = time.time()
            _body_before_fb = dict(proxy_request.original_body) if proxy_request.original_body else {}
            proxy_request = _rewriter.rewrite(proxy_request, vision_results)
            _body_after_fb = dict(proxy_request.original_body) if proxy_request.original_body else {}
            record_stage(trace_id, StageSnapshot(
                stage="rewrite",
                input={"original_body": _body_before_fb, "vision_results": len(vision_results),
                       "image_block_count": len(all_images)},
                output={"replaced_images": len(all_images), "rewritten_body": _body_after_fb},
                duration_ms=(time.time() - t0) * 1000,
                status="ok",
            ))

            response = await _forward(
                proxy_request.original_body, target_config,
                proxy_request.stream, trace_id, get_tc,
            )
            total_ms = (time.time() - pipeline_start) * 1000
            finalize_trace(trace_id, 200, total_ms)
            return response

        logger.info("Decision: skip (no relevant images for current question)")

        t0 = time.time()
        all_images = await extract_images(proxy_request, latest_only=False)
        vision_results = _lookup_cached(all_images)
        if len(vision_results) < len(all_images):
            logger.warning(
                "Skip path: %d/%d images not cached, processing all",
                len(all_images) - len(vision_results), len(all_images),
            )
            fallback = _default_decision(len(all_images))
            vision_results = await _vision_and_cache(all_images, fallback, proxy_request)
        _vision_prompt = _compute_vision_prompt("skip")
        record_stage(trace_id, StageSnapshot(
            stage="vision",
            input=_build_vision_input(all_images, "skip", prompt=_vision_prompt),
            output=_build_vision_output(all_images, vision_results, "skip", decision.focus_prompt, prompt=_vision_prompt),
            duration_ms=(time.time() - t0) * 1000,
            status="ok",
        ))

        if vision_results:
            t0 = time.time()
            _body_before_sk = dict(proxy_request.original_body) if proxy_request.original_body else {}
            proxy_request = _rewriter.rewrite(proxy_request, vision_results)
            _body_after_sk = dict(proxy_request.original_body) if proxy_request.original_body else {}
            record_stage(trace_id, StageSnapshot(
                stage="rewrite",
                input={"original_body": _body_before_sk, "vision_results": len(vision_results),
                       "image_block_count": len(vision_results)},
                output={"replaced_images": len(vision_results), "rewritten_body": _body_after_sk},
                duration_ms=(time.time() - t0) * 1000,
                status="ok",
            ))

        response = await _forward(
            proxy_request.original_body, target_config,
            proxy_request.stream, trace_id, get_tc,
        )
        total_ms = (time.time() - pipeline_start) * 1000
        finalize_trace(trace_id, 200, total_ms)
        return response

    # Decision with specific image hashes
    t0 = time.time()
    all_images = await extract_images(proxy_request, latest_only=False)
    target_images, seen_hashes = _filter_images_by_hash(all_images, decision.image_hashes)

    if not target_images:
        logger.warning("Decision requested images not found in body, processing all")
        fallback = _default_decision(len(all_images))
        vision_results = await _vision_and_cache(all_images, fallback, proxy_request)
        _vision_prompt = _compute_vision_prompt(fallback.mode, fallback.focus_prompt)
        record_stage(trace_id, StageSnapshot(
            stage="vision",
            input=_build_vision_input(all_images, fallback.mode, fallback.focus_prompt, prompt=_vision_prompt, reason="images_not_found"),
            output=_build_vision_output(all_images, vision_results, fallback.mode, fallback.focus_prompt, prompt=_vision_prompt),
            duration_ms=(time.time() - t0) * 1000,
            status="ok",
        ))

        t0 = time.time()
        _body_before_nf = dict(proxy_request.original_body) if proxy_request.original_body else {}
        proxy_request = _rewriter.rewrite(proxy_request, vision_results)
        _body_after_nf = dict(proxy_request.original_body) if proxy_request.original_body else {}
        record_stage(trace_id, StageSnapshot(
            stage="rewrite",
            input={"original_body": _body_before_nf, "vision_results": len(vision_results),
                   "image_block_count": len(all_images)},
            output={"replaced_images": len(all_images), "rewritten_body": _body_after_nf},
            duration_ms=(time.time() - t0) * 1000,
            status="ok",
        ))

        response = await _forward(
            proxy_request.original_body, target_config,
            proxy_request.stream, trace_id, get_tc,
        )
        total_ms = (time.time() - pipeline_start) * 1000
        finalize_trace(trace_id, 200, total_ms)
        return response

    logger.info("[RE-VISION] %d images from history, mode=%s", len(target_images), decision.mode)
    vision_results = await _vision_and_cache(target_images, decision, proxy_request)
    _vision_prompt = _compute_vision_prompt(decision.mode, decision.focus_prompt)
    record_stage(trace_id, StageSnapshot(
        stage="vision",
        input=_build_vision_input(target_images, decision.mode, decision.focus_prompt, prompt=_vision_prompt),
        output=_build_vision_output(target_images, vision_results, decision.mode, decision.focus_prompt, prompt=_vision_prompt),
        duration_ms=(time.time() - t0) * 1000,
        status="ok",
    ))

    t0 = time.time()
    _body_before_sh = dict(proxy_request.original_body) if proxy_request.original_body else {}
    proxy_request = _rewriter.rewrite(proxy_request, vision_results)
    _body_after_sh = dict(proxy_request.original_body) if proxy_request.original_body else {}
    record_stage(trace_id, StageSnapshot(
        stage="rewrite",
        input={"original_body": _body_before_sh, "vision_results": len(vision_results),
               "image_block_count": len(target_images)},
        output={"replaced_images": len(target_images), "rewritten_body": _body_after_sh},
        duration_ms=(time.time() - t0) * 1000,
        status="ok",
    ))

    response = await _forward(
        proxy_request.original_body, target_config,
        proxy_request.stream, trace_id, get_tc,
    )
    total_ms = (time.time() - pipeline_start) * 1000
    finalize_trace(trace_id, 200, total_ms)
    return response


# ---------------------------------------------------------------------------
# Vision + cache helper (shared across new_images and historical branches)
# ---------------------------------------------------------------------------


async def _vision_and_cache(
    images: list[ImageBlock],
    decision,
    proxy_request: ProxyRequest,
) -> list[tuple[ImageBlock, str]]:
    _ensure_components()
    focus = decision.focus_prompt or "请描述这张图片的内容"

    if decision.mode == "compare" and len(images) >= 2:
        return await _vision_compare_locked(images, focus, proxy_request)

    # --- Replicate mode: content description + CSS variables ---
    if decision.mode == "replicate":
        return await _vision_replicate(images, focus, proxy_request)

    logger.info("[SINGLE] %d image(s)", len(images))
    results: list[tuple[ImageBlock, str]] = []
    for img in images:
        h = image_hash(img)
        if not h:
            desc = await _vision_client.recognize(img, focus)
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
            desc = await _vision_client.recognize(img, focus)
            logger.debug("  [VISION OUTPUT] %.300s", desc)
            if desc != FALLBACK_TEXT:
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
    """Compare mode with per-hash locking -- acquire all locks before calling vision."""
    _ensure_components()
    logger.info("[COMPARE] %d images in ONE vision call", len(images))

    hashes = [image_hash(img) for img in images]
    locks: list[tuple[str, "asyncio.Lock"]] = []
    for h in sorted(h for h in hashes if h):
        lock = cache.acquire_lock(h)
        await lock.acquire()
        locks.append((h, lock))

    try:
        import asyncio
        desc = await _vision_client.recognize_compare(images, focus)
        logger.debug("[VISION OUTPUT] %s", desc[:500])
        if desc != FALLBACK_TEXT:
            for img in images:
                h = image_hash(img)
                if h:
                    fname, pos = extract_file_metadata(proxy_request, img)
                    cache.set(h, desc, focus, fname, pos, _make_label(desc))
        return [(img, desc) for img in images]
    finally:
        for h, lock in locks:
            cache.release_lock(h)


async def _vision_replicate(
    images: list[ImageBlock],
    focus: str,
    proxy_request: ProxyRequest,
) -> list[tuple[ImageBlock, str]]:
    """Replicate mode: content description + CSS variables per image.

    Produces a combined output so the downstream target model has both
    semantic understanding (what the UI is) and precise measurements.
    The combined result is cached under the replicate-specific focus key.
    """
    _ensure_components()
    logger.info("[REPLICATE] %d image(s)", len(images))
    results: list[tuple[ImageBlock, str]] = []
    cache_focus = "__replicate__"

    for img in images:
        h = image_hash(img)
        if not h:
            desc = await _vision_client.recognize(img, focus)
            css = await _vision_client.recognize_replicate(img) or ""
            results.append((img, _format_replicate(desc, css)))
            continue

        cached_hit = cache.get(h, cache_focus)
        if cached_hit:
            results.append((img, cached_hit))
            logger.info("  [CACHE HIT] %s (replicate)", h[:12])
            continue

        # Content description — separate cache lookup
        content_desc = cache.get(h, focus)
        if not content_desc:
            lock = cache.acquire_lock(h)
            await lock.acquire()
            try:
                content_desc = cache.get(h, focus)
                if not content_desc:
                    content_desc = await _vision_client.recognize(img, focus)
                    if content_desc != FALLBACK_TEXT:
                        fname, pos = extract_file_metadata(proxy_request, img)
                        cache.set(h, content_desc, focus, fname, pos, _make_label(content_desc))
            finally:
                cache.release_lock(h)

        # CSS spec — separate cache lookup under replicate key
        css = cache.get(h, cache_focus)
        if not css:
            lock = cache.acquire_lock(h)
            await lock.acquire()
            try:
                css = cache.get(h, cache_focus)
                if not css:
                    css = await _vision_client.recognize_replicate(img) or ""
                    if not css:
                        css = await _vision_client.recognize(img, "请描述这张图片的视觉设计规范，包括颜色、字体、间距。")
                    logger.debug("  [VI-SPEC OUTPUT] %.200s", css.strip().replace('\n', ' '))
                    if css and css != FALLBACK_TEXT:
                        cache.set(h, css, cache_focus)
            finally:
                cache.release_lock(h)

        combined = _format_replicate(content_desc, css)
        # Only cache if we got real results (not fallback text)
        if content_desc != FALLBACK_TEXT or (css and css != FALLBACK_TEXT):
            fname, pos = extract_file_metadata(proxy_request, img)
            cache.set(h, combined, cache_focus, fname, pos, _make_label(content_desc))
        results.append((img, combined))
        logger.info("  [REPLICATE OK] %s", h[:12])

    return results


def _format_replicate(description: str, css: str) -> str:
    """Combine content description and CSS spec into a single output."""
    parts: list[str] = []
    if description:
        parts.append(f"【内容描述】\n{description}")
    if css:
        parts.append(f"【视觉规范】\n{css}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Forwarding
# ---------------------------------------------------------------------------


async def _forward(
    body: dict,
    target_config: TargetModelConfig,
    stream: bool,
    trace_id: str,
    get_tc: Callable[[], TargetModelClient],
):
    """Forward the (potentially rewritten) body to the target model.

    Records ``target`` and ``response`` stage snapshots.
    """
    import json as _json

    _ensure_components()

    # Strip any raw images that slipped past the rewriter (last-resort safety net).
    body = _strip_images_from_body(body)

    logger.debug(
        "Forward: model=%s stream=%s body_bytes=%d url=%s",
        target_config.model_id, stream,
        len(_json.dumps(body)), target_config.api_base,
    )

    client = get_tc()

    # --- Stage 6: Target ---
    t0 = time.time()
    target_dur_ms = 0.0
    try:
        if stream:
            gen = client.forward_stream(body, target_config)
            response = _response_handler.handle_stream(gen, "anthropic")
        else:
            resp = await client.forward(body, target_config)
            response = await _response_handler.handle_non_stream(resp, "anthropic")

        target_dur_ms = (time.time() - t0) * 1000
        target_status = getattr(response, "status_code", 200)

        record_stage(trace_id, StageSnapshot(
            stage="target",
            input={"model": target_config.model_id, "stream": stream,
                   "endpoint": target_config.api_base,
                   "timeout_s": 120,
                   "headers": {"x-api-key": "***"},
                   "body": body},
            output={"status_code": target_status,
                    "duration_ms": target_dur_ms,
                    "connection_ms": 0,
                    "ttfb_ms": 0,
                    "streaming_ms": 0},
            duration_ms=target_dur_ms,
            status="ok",
        ))

        # --- Stage 7: Response ---
        record_stage(trace_id, StageSnapshot(
            stage="response",
            input={"stream": stream},
            output={"handled": True, "status_code": target_status,
                    "model": target_config.model_id,
                    "response_bytes": len(_json.dumps(body)),
                    "stream_lines": 0,
                    "stop_reason": "",
                    "output_tokens": 0},
            duration_ms=0,
            status="ok",
        ))

        return response
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
        reasoning="no cache -- processing all images",
    )


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
    """Remove all image blocks from the request body (last-resort safety net)."""
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
                    "(source=%s, media=%s, data_len=%d) -- this is a bug, "
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
            "SAFEGUARD: stripped %d raw image block(s) -- pipeline bug, "
            "images should be handled before _forward", stripped,
        )
    return body


_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def _extract_user_messages(request: ProxyRequest, last_n: int = 5) -> list[str]:
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


def _count_all_images(proxy_request: ProxyRequest) -> int:
    """Count all ImageBlock entries across all messages (recursively)."""
    count = 0

    def _walk(blocks) -> None:
        nonlocal count
        for b in blocks:
            if isinstance(b, ImageBlock):
                count += 1
            elif isinstance(b, ToolResultBlock):
                _walk(b.content)

    for msg in proxy_request.messages:
        _walk(msg.content)
    return count


def _count_cache_hits(images: list[ImageBlock]) -> int:
    """Count how many images have cached descriptions."""
    hits = 0
    for img in images:
        h = image_hash(img)
        if h and cache.get(h):
            hits += 1
    return hits


def _compute_vision_prompt(mode: str = "", focus_prompt: str = "") -> str:
    """Compute the actual prompt text sent to the vision model.

    Called at the vision call site so the recorded prompt matches what was
    actually sent, rather than being reconstructed after the fact.
    """
    from backend.src.pipeline.vision_client import _build_prompt as _vp_build_prompt, _REPLICATE_PROMPT

    if mode == "replicate":
        return _REPLICATE_PROMPT
    if focus_prompt:
        return _vp_build_prompt(focus_prompt)
    return _vp_build_prompt("请描述这张图片的内容")


def _build_vision_input(
    images: list[ImageBlock],
    mode: str = "",
    focus_prompt: str = "",
    prompt: str = "",
    **extra,
) -> dict:
    """Build the vision stage input dict."""
    result: dict = {
        "images": len(images),
        "mode": mode or "single",
        "focus": focus_prompt,
        "prompt": prompt,
        "model": _vision_client._model if _vision_client else "",
        "endpoint": _vision_client._base_url if _vision_client else "",
        "max_tokens": 2000,
    }
    result.update(extra)
    return result


def _build_vision_output(
    images: list[ImageBlock],
    vision_results: list[tuple[ImageBlock, str]],
    mode: str = "",
    focus_prompt: str = "",
    prompt: str = "",
) -> dict:
    """Build the vision stage output dict with per-image descriptions."""
    from backend.src.pipeline.vision_client import _build_prompt as _vp_build_prompt
    _vision_prompt = prompt or _compute_vision_prompt(mode, focus_prompt)

    descriptions = []
    for i, (img, desc) in enumerate(vision_results):
        h = image_hash(img)
        descriptions.append({
            "hash": h or "unknown",
            "description": desc,
            "file_name": img.file_name if hasattr(img, "file_name") else "",
            "format": img.media_type if hasattr(img, "media_type") else "image/png",
            "cache_hit": bool(h and cache.get(h)),
            "position": i + 1,
            "prompt": _vision_prompt,
        })
    return {
        "results": len(vision_results),
        "cache_hits": _count_cache_hits(images),
        "descriptions": descriptions,
        "mode": mode,
        "model": _vision_client._model if _vision_client else "",
        "endpoint": (_vision_client._base_url + "/v1/chat/completions") if _vision_client else "",
        "max_tokens": 2000,
    }
