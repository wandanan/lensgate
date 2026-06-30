"""
Dashboard REST API — monitoring data endpoints.

Exposes trace records, decision snapshots, cache state, and aggregate
statistics from the in-memory TraceBuffer and CacheStore singletons.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from backend.src.core.config import ProxyConfig
from backend.src.dashboard.trace import (
    StageSnapshot,
    TraceRecord,
    buffer,
    replay_request,
    _find_stage,
)
from backend.src.pipeline.cache_store import cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _match_decision_snippet(snippet: dict | None, q_lower: str) -> bool:
    """Check if *q_lower* matches any field in the decision snippet."""
    if not snippet:
        return False
    for field in ("mode", "focus", "reasoning"):
        val = snippet.get(field, "")
        if isinstance(val, str) and q_lower in val.lower():
            return True
    hashes = snippet.get("hashes", [])
    if isinstance(hashes, list):
        for h in hashes:
            if isinstance(h, str) and q_lower in h.lower():
                return True
    return False


def _match_vision_snippets(snippets: list | None, q_lower: str) -> bool:
    """Check if *q_lower* matches any hash or description in vision snippets."""
    if not snippets:
        return False
    for v in snippets:
        for field in ("hash", "description"):
            val = v.get(field, "")
            if isinstance(val, str) and q_lower in val.lower():
                return True
    return False


def _extract_user_input(body: dict) -> str:
    """Extract the last user text message from an Anthropic or OpenAI request body."""
    messages = body.get("messages", [])
    if not messages:
        return ""

    # Walk backwards to find the last user message.
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        # content may be a string (OpenAI text) or a list (Anthropic / vision).
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return " ".join(text_parts).strip()
    return ""


def _extract_target_preview(stages: list) -> str:
    """Extract a preview of the target model's response from stages."""
    for name in ("response", "target"):
        s = _find_stage_by_list(stages, name)
        if s and s.output:
            body = s.output.get("body", "")
            if isinstance(body, str) and body.strip():
                return body[:300]
            # Try content / choices fields.
            content = s.output.get("content", "")
            if isinstance(content, str) and content.strip():
                return content[:300]
            choices = s.output.get("choices", [])
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message", {})
                text = msg.get("content", "")
                if isinstance(text, str) and text.strip():
                    return text[:300]
    return ""


def _find_stage_by_list(stages: list, stage_name: str):
    """Return the first stage snapshot matching *stage_name*, or ``None``."""
    for s in stages:
        if s.stage == stage_name:
            return s
    return None


def _extract_images_from_body(body: dict) -> list[tuple[bytes, str, str]]:
    """Extract raw image bytes, media types, and SHA-256 hashes from a request body.

    Walks ``messages[].content[]`` recursively (including ``tool_result`` blocks)
    looking for image blocks (base64, data_uri).
    Returns a list of ``(image_bytes, media_type, sha256_hash)`` tuples in order.
    """
    images: list[tuple[bytes, str, str]] = []
    messages = body.get("messages", [])
    if not isinstance(messages, list):
        return images

    for msg in messages:
        _walk_content_blocks(msg.get("content"), images)
    return images


def _walk_content_blocks(blocks: object, images: list[tuple[bytes, str, str]]) -> None:
    """Recursively walk content blocks, extracting images from any nesting level."""
    if not isinstance(blocks, list):
        return
    for block in blocks:
        if not isinstance(block, dict):
            continue
        # Try direct image decode first
        img_bytes, media_type = _decode_image_block(block) or (None, None)
        if img_bytes is not None:
            h = hashlib.sha256(img_bytes).hexdigest()
            images.append((img_bytes, media_type, h))
        # Recurse into tool_result content
        if block.get("type") == "tool_result":
            _walk_content_blocks(block.get("content"), images)


def _decode_image_block(block: dict) -> tuple[bytes, str] | None:
    """Try to decode a single content block into (image_bytes, media_type).

    Supports Anthropic-style ``source`` blocks and OpenAI-style ``image_url`` blocks.
    """
    source = block.get("source")
    media_type = "image/png"

    if isinstance(source, dict):
        media_type = source.get("media_type", "image/png")
        data = source.get("data", "")
        if data:
            return base64.b64decode(data), media_type

    image_url = block.get("image_url")
    if isinstance(image_url, dict):
        url = image_url.get("url", "")
        if url.startswith("data:"):
            # data:image/png;base64,xxxxx
            import re
            m = re.match(r"^data:(image/[\w.+-]+);base64,(.+)$", url, re.ASCII)
            if m:
                return base64.b64decode(m.group(2)), m.group(1)

    return None


def _serialize_summary(r: TraceRecord) -> dict:
    """Convert a TraceRecord to a TraceSummary for the list endpoint.

    Includes minimal I/O snippets so the frontend request-card view can
    render pipeline stages inline without fetching each detail individually.
    """
    img_stage = _find_stage(r, "image_check")
    has_img = bool(img_stage and img_stage.output.get("has_images"))
    img_count = int(img_stage.output.get("total_images", 0)) if img_stage else 0

    user_input = _extract_user_input(r.original_body)

    # decision snippet
    dec_stage = _find_stage(r, "decision")
    decision_snippet = None
    if dec_stage and dec_stage.output:
        decision_snippet = {
            "mode": dec_stage.output.get("mode", ""),
            "focus": dec_stage.output.get("focus_prompt", ""),
            "hashes": dec_stage.output.get("hashes", []),
            "reasoning": dec_stage.output.get("reasoning", ""),
            "status": dec_stage.status,
        }

    # vision snippets
    vis_stage = _find_stage(r, "vision")
    vision_snippets = None
    if vis_stage and vis_stage.output:
        descriptions = vis_stage.output.get("descriptions", [])
        if descriptions:
            vision_snippets = [
                {"hash": d.get("hash", ""), "description": (d.get("description", "") or d.get("summary", ""))[:300]}
                for d in descriptions
            ]

    # target response preview
    target_response_preview = _extract_target_preview(r.stages)

    return {
        "id": r.id,
        "timestamp": r.timestamp.isoformat(),
        "method": r.method,
        "path": r.path,
        "source_format": r.source_format,
        "target_model": r.target_model,
        "has_images": has_img,
        "image_count": img_count,
        "status_code": r.status_code,
        "total_duration_ms": r.total_duration_ms,
        "stream": r.stream,
        "user_input": user_input,
        "decision_snippet": decision_snippet,
        "vision_snippets": vision_snippets,
        "target_response_preview": target_response_preview,
    }


def _serialize_stage(s: StageSnapshot) -> dict:
    """Serialize a single StageSnapshot."""
    return {
        "stage": s.stage,
        "input": s.input,
        "output": s.output,
        "duration_ms": s.duration_ms,
        "status": s.status,
    }


def _serialize_detail(r: TraceRecord) -> dict:
    """Serialize a full TraceRecord including original_body and all stages."""
    return {
        "id": r.id,
        "timestamp": r.timestamp.isoformat(),
        "method": r.method,
        "path": r.path,
        "source_format": r.source_format,
        "target_model": r.target_model,
        "stream": r.stream,
        "status_code": r.status_code,
        "total_duration_ms": r.total_duration_ms,
        "original_body": r.original_body,
        "stages": [_serialize_stage(s) for s in r.stages],
        "replay_of": r.replay_of,
        "replays": r.replays,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/requests")
async def list_requests(
    status: Optional[int] = Query(None, description="Filter by HTTP status code"),
    path: Optional[str] = Query(None, description="Filter by request path substring"),
    has_images: Optional[bool] = Query(None, description="Filter by image presence"),
    q: Optional[str] = Query(None, description="Search keyword in user input, decisions, vision, or response"),
    page: int = Query(1, ge=1, description="1-based page number"),
    size: int = Query(20, ge=1, le=100, description="Records per page"),
) -> dict:
    """Paginated request list with optional filters and full-text search.

    Returns a page of ``TraceSummary`` objects enriched with stage I/O
    snippets so the frontend can render request cards inline.
    Use ``GET /api/dashboard/requests/{id}`` for the complete trace detail.
    """
    status_str = str(status) if status is not None else None

    # Fetch all matching records first so we can report an accurate total.
    # The buffer holds at most 1000 entries, so this is cheap.
    # Records are stored oldest-first; reverse so newest appear on page 1.
    all_matches = buffer.list(
        status=status_str,
        path=path,
        has_images=has_images,
        page=1,
        size=len(buffer) + 1,
    )[::-1]

    # Serialize all summaries first (needed for q search in I/O fields).
    summaries = [_serialize_summary(r) for r in all_matches]

    # Apply full-text search filter across I/O snippet fields.
    if q:
        q_lower = q.lower()
        summaries = [
            s for s in summaries
            if (q_lower in (s.get("user_input") or "").lower())
            or _match_decision_snippet(s.get("decision_snippet"), q_lower)
            or _match_vision_snippets(s.get("vision_snippets"), q_lower)
            or (q_lower in (s.get("target_response_preview") or "").lower())
        ]

    total = len(summaries)
    start = (page - 1) * size

    return {
        "items": summaries[start : start + size],
        "total": total,
        "page": page,
    }


@router.get("/requests/{trace_id}")
async def get_request(trace_id: str) -> dict:
    """Full trace detail including ``original_body`` and all pipeline stages."""
    record = buffer.get(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Trace not found: {trace_id}")
    return _serialize_detail(record)


@router.get("/requests/{trace_id}/images/{image_hash}")
async def get_request_image(trace_id: str, image_hash: str) -> Response:
    """Return the image matching *image_hash* from the trace's original body.

    Extracts image content blocks, computes SHA-256 of each, and matches by hash
    so the lookup works regardless of filtering/reordering by the decision engine.
    """
    record = buffer.get(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Trace not found: {trace_id}")

    for img_data, media_type, h in _extract_images_from_body(record.original_body):
        if h == image_hash:
            return Response(content=img_data, media_type=media_type)

    raise HTTPException(status_code=404, detail=f"Image hash {image_hash} not found in trace {trace_id}")


@router.post("/requests/{trace_id}/replay")
def replay_request_endpoint(trace_id: str) -> dict:
    """Replay a previous request using its original body.

    The endpoint is synchronous so that ``replay_request`` can use
    ``asyncio.run()`` without encountering a running event loop.
    """
    config = ProxyConfig()
    try:
        new_id = replay_request(trace_id, config)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "replay_id": new_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/decisions")
async def list_decisions(
    status: Optional[str] = Query(None, description="Filter by decision status (ok/error/skipped)"),
    page: int = Query(1, ge=1, description="1-based page number"),
    size: int = Query(20, ge=1, le=100, description="Records per page"),
) -> dict:
    """Paginated decision audit log.

    Each entry is a flattened snapshot of a ``decision`` pipeline stage
    enriched with its parent trace metadata.
    """
    decisions: list[dict] = []

    with buffer._lock:
        snapshot = list(buffer._buffer)

    for trace in snapshot:
        for stage in trace.stages:
            if stage.stage != "decision":
                continue
            if status is not None and stage.status != status:
                continue

            output = stage.output or {}
            decisions.append({
                "timestamp": trace.timestamp.isoformat(),
                "trace_id": trace.id,
                "user_messages": output.get("user_messages", []),
                "cached_images_count": output.get("cached_images_count", 0),
                "new_image_count": output.get("new_image_count", 0),
                "mode": output.get("mode", ""),
                "hashes": output.get("hashes", []),
                "focus_prompt": output.get("focus_prompt", ""),
                "reasoning": output.get("reasoning", ""),
                "attempt": output.get("attempt", 1),
                "status": stage.status,
            })

    total = len(decisions)
    start = (page - 1) * size

    return {
        "items": decisions[start : start + size],
        "total": total,
        "page": page,
    }


@router.get("/cache")
async def list_cache(
    q: Optional[str] = Query(None, description="Search by hash or file name substring"),
) -> dict:
    """Current CacheStore snapshot, optionally filtered by a search query."""
    entries = cache.entries()

    if q:
        q_lower = q.lower()
        entries = [
            e for e in entries
            if q_lower in e.get("hash", "").lower()
            or q_lower in e.get("file_name", "").lower()
        ]

    return {
        "items": entries,
        "total": len(entries),
    }


@router.get("/stats")
async def get_stats() -> dict:
    """Aggregate dashboard statistics from the trace buffer."""
    return buffer.stats()
