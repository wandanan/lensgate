"""
FastAPI application — TLMA (Text LLM Multimodal Agent).

Path-based target routing + decision-engine attention layer.
"""

import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Request

from backend.src.config import ProxyConfig
from backend.src.decision_engine import DecisionEngine
from backend.src.error_handler import check_config, register_error_handlers
from backend.src.format_detector import detect_format, parse_anthropic_request, parse_openai_request
from backend.src.image_extractor import (
    cache_entries,
    cache_get,
    cache_set,
    extract_file_metadata,
    extract_images,
    has_images,
    image_hash,
)
from backend.src.logging_config import setup_logging
from backend.src.middleware.auth import APIKeyMiddleware
from backend.src.models import TargetModelConfig
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

app = FastAPI(title="TLMA - Text LLM Multimodal Agent")
app.add_middleware(APIKeyMiddleware, api_key=config.proxy_api_key)
register_error_handlers(app)
check_config(config)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "timestamp": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@app.post("/{target:path}/v1/messages")
async def anthropic_messages(request: Request, target: str):
    return await _run_pipeline(request, target)


@app.post("/{target:path}/v1/chat/completions")
async def openai_chat_completions(request: Request, target: str):
    return await _run_pipeline(request, target)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def _run_pipeline(request: Request, target: str):
    from backend.src.error_handler import InvalidRequestError, PayloadTooLargeError, TargetModelTimeoutError, TargetModelUnavailableError
    import httpx

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

    messages = body.get("messages")
    if not messages or (isinstance(messages, list) and len(messages) == 0):
        raise InvalidRequestError("messages field is required and must be non-empty")

    # --- Target config from path + headers ---
    scheme = "https"
    t = target
    if t.startswith("http."):
        scheme = "http"
        t = t[5:]
    target_api_key = getattr(request.state, "target_api_key", "")
    target_config = TargetModelConfig(
        model_id=body.get("model", ""),
        api_base=f"{scheme}://{t}",
        api_key=target_api_key,
    )
    logger.info("Target: %s://%s", scheme, t)

    # --- Stage 1: Format detect ---
    fmt = detect_format(
        "/v1/messages" if "v1/messages" in str(request.url.path) else "/v1/chat/completions",
        body,
    )
    proxy_request = (
        parse_anthropic_request(body) if fmt == "anthropic"
        else parse_openai_request(body)
    )

    # --- Stage 2: Decision engine → image attention routing ---
    new_images = await extract_images(proxy_request, latest_only=True)

    if new_images:
        # PATH A / B: fresh images in latest message — direct vision
        logger.info("New images in latest message: %d", len(new_images))

        vision_results: list = []
        for img in new_images:
            h = image_hash(img)
            cached = cache_get(h) if h else None
            if cached:
                vision_results.append((img, cached))
                logger.info("Image %s: cache hit", h[:12] if h else "?")
            else:
                desc = await vision_client.recognize(img)
                vision_results.append((img, desc))
                if h:
                    fname, pos = extract_file_metadata(proxy_request, img)
                    cache_set(h, desc, "通用描述", fname, pos)
                logger.info("Image %s: vision done", h[:12] if h else "?")

        proxy_request = rewriter.rewrite(proxy_request, vision_results)
        return await _forward(proxy_request.original_body, target_config, proxy_request.stream)

    # No new images — check decision engine for historical re-vision
    cached = cache_entries()
    if not cached:
        logger.info("Pure-text fast path (no cache)")
        return await _forward(proxy_request.original_body, target_config, proxy_request.stream)

    # --- Build decision input ---
    user_msgs = _extract_user_messages(proxy_request, last_n=5)
    last_reply = _extract_last_assistant_reply(proxy_request)

    decision = await decision_engine.decide(user_msgs, cached, last_reply)
    logger.info("Decision: hashes=%s mode=%s focus=%s", decision.image_hashes, decision.mode, decision.focus_prompt[:50])

    if not decision.image_hashes:
        logger.info("Pure-text fast path (decision: no relevant images)")
        return await _forward(proxy_request.original_body, target_config, proxy_request.stream)

    # PATH D: extract historical images by hash
    all_images = await extract_images(proxy_request, latest_only=False)
    target_images: list = []
    for img in all_images:
        h = image_hash(img)
        if h and h in decision.image_hashes:
            target_images.append(img)

    if not target_images:
        logger.warning("Decision requested images not found in body")
        return await _forward(proxy_request.original_body, target_config, proxy_request.stream)

    logger.info("Re-vision: %d images, mode=%s, focus=%s",
                len(target_images), decision.mode, decision.focus_prompt)

    focus = decision.focus_prompt or "请描述这张图片的内容"

    if decision.mode == "compare" and len(target_images) >= 2:
        desc = await vision_client.recognize_compare(target_images, focus)
        vision_results = [(img, desc) for img in target_images]
        # Update cache for each image
        for img in target_images:
            h = image_hash(img)
            if h:
                fname, pos = extract_file_metadata(proxy_request, img)
                cache_set(h, desc, focus, fname, pos)
    else:
        vision_results = []
        for img in target_images:
            h = image_hash(img)
            cached_hit = cache_get(h, focus) if h else None
            if cached_hit:
                vision_results.append((img, cached_hit))
                continue
            desc = await vision_client.recognize(img)
            vision_results.append((img, desc))
            if h:
                fname, pos = extract_file_metadata(proxy_request, img)
                cache_set(h, desc, focus, fname, pos)

    proxy_request = rewriter.rewrite(proxy_request, vision_results)
    return await _forward(proxy_request.original_body, target_config, proxy_request.stream)


async def _forward(body: dict, target_config: TargetModelConfig, stream: bool):
    import httpx
    from backend.src.error_handler import TargetModelTimeoutError, TargetModelUnavailableError

    client = _get_target_client()
    try:
        if stream:
            gen = client.forward_stream(body, target_config)
            return await response_handler.handle_stream(gen, "anthropic")
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
# Decision engine helpers
# ---------------------------------------------------------------------------


def _extract_user_messages(request: "ProxyRequest", last_n: int = 5) -> list[str]:
    """Extract the last N user messages as plain text."""
    from backend.src.models import TextBlock
    result: list[str] = []
    for msg in request.messages:
        if msg.role != "user":
            continue
        text = ""
        for block in msg.content:
            if isinstance(block, TextBlock):
                text += block.text + " "
        text = text.strip()
        if text:
            result.append(text)
    return result[-last_n:]


def _extract_last_assistant_reply(request: "ProxyRequest") -> str:
    """Extract the last assistant message text."""
    from backend.src.models import TextBlock
    for msg in reversed(request.messages):
        if msg.role != "assistant":
            continue
        parts: list[str] = []
        for block in msg.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
        return " ".join(parts)[:500]
    return ""
