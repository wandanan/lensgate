"""
Trace Middleware -- link tracing data structures, ring buffer, and hook API.

Used by the pipeline to record stage-level snapshots (input/output/duration/status)
for every request.  Records live in a fixed-size thread-safe ring buffer (max 1000)
and are exposed via the dashboard REST API and the replay mechanism.

All public API functions operate on a module-level singleton ``buffer``.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class StageSnapshot:
    """A single pipeline stage measurement.

    Attributes:
        stage: Stage name (e.g. ``"format_detect"``, ``"vision"``).
        input: JSON-serializable dict representing stage input.
        output: JSON-serializable dict representing stage output.
        duration_ms: Wall-clock duration of the stage in milliseconds.
        status: ``"ok"``, ``"error"``, or ``"skipped"``.
    """

    stage: str
    input: dict
    output: dict
    duration_ms: float = 0.0
    status: str = "ok"


@dataclass
class TraceRecord:
    """Full trace of a single proxy request through the pipeline.

    Attributes:
        id: Short UUID (first 8 chars).
        timestamp: UTC timestamp when the trace was created.
        method: HTTP method (always ``"POST"`` for pipeline requests).
        path: Request path (e.g. ``"/v1/messages"``).
        source_format: ``"anthropic"`` or ``"openai"``.
        target_model: Target text model identifier.
        stream: Whether the client requested streaming.
        status_code: Final HTTP status code sent back to the client.
        total_duration_ms: End-to-end wall-clock duration in milliseconds.
        original_body: Parsed JSON request body (dict, for replay).
        stages: Ordered list of ``StageSnapshot`` for each pipeline stage.
        replay_of: When this trace was created by a replay, the source trace id.
        replays: List of trace ids that were replayed from this trace.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    method: str = "POST"
    path: str = ""
    source_format: str = ""
    target_model: str = ""
    stream: bool = False
    status_code: int = 200
    total_duration_ms: float = 0.0
    original_body: dict = field(default_factory=dict)
    stages: list[StageSnapshot] = field(default_factory=list)
    replay_of: str | None = None
    replays: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Ring buffer
# ---------------------------------------------------------------------------


class TraceBuffer:
    """Thread-safe fixed-size ring buffer for ``TraceRecord`` entries.

    Public API:
        append(record)   -- push a new record (oldest evicted when full).
        get(trace_id)    -- look up a single record by id.
        list(**filters)  -- paginated / filtered listing.
        stats()          -- aggregate statistics.
        restore()        -- load recent records from SQLite on startup.
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._buffer: list[TraceRecord] = []
        self._max_size = max_size
        self._lock = threading.Lock()
        self._store: Any = None  # TraceStore, lazy init

    # -- basic operations ---------------------------------------------------

    def _get_store(self) -> Any:
        if self._store is None:
            from backend.src.dashboard.store import TraceStore
            self._store = TraceStore()
        return self._store

    def append(self, record: TraceRecord) -> None:
        """Append *record*, evicting the oldest entry when the buffer is full.

        Also persists to SQLite (write-through).
        """
        with self._lock:
            if len(self._buffer) >= self._max_size:
                self._buffer.pop(0)
            self._buffer.append(record)

        # Write-through to SQLite (outside lock to avoid I/O contention)
        try:
            self._get_store().save(_serialize_record(record))
        except Exception:
            logger.warning("Failed to persist trace %s to SQLite", record.id, exc_info=True)

    def get(self, trace_id: str) -> TraceRecord | None:
        """Look up a trace record by its id string.  Returns ``None`` if not found."""
        with self._lock:
            for r in self._buffer:
                if r.id == trace_id:
                    return r
        return None

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    # -- filtered listing ---------------------------------------------------

    def list(
        self,
        status: str | None = None,
        path: str | None = None,
        has_images: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> list[TraceRecord]:
        """Return a paginated list of trace records with optional filters.

        Parameters:
            status:     Filter by status code (``"200"``, ``"500"``, ...).
                        When ``None`` no status filter is applied.
            path:       Filter by request path substring match.
            has_images: When ``True``, only include requests that have at least
                        one ``image_check`` stage with a truthy output.
                        When ``False``, only include requests with no images.
                        When ``None``, no image filter is applied.
            page:       1-based page number.
            size:       Number of records per page.
        """
        with self._lock:
            # Work on a snapshot to avoid holding the lock during iteration.
            snapshot = list(self._buffer)

        filtered: list[TraceRecord] = []
        for r in snapshot:
            if status is not None and str(r.status_code) != str(status):
                continue
            if path is not None and path not in r.path:
                continue
            if has_images is not None:
                img_stage = _find_stage(r, "image_check")
                has_img = bool(img_stage and img_stage.output.get("has_images"))
                if has_img != has_images:
                    continue
            filtered.append(r)

        # Paginate
        start = (page - 1) * size
        return filtered[start : start + size]

    # -- aggregate statistics -----------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return aggregate statistics across all buffered traces.

        Returns a dict with keys:
            total, success_rate, avg_duration_ms, p99_duration_ms,
            cache_hit_rate, total_images
        """
        with self._lock:
            snapshot = list(self._buffer)

        total = len(snapshot)
        if total == 0:
            return {
                "total": 0,
                "success_rate": 0.0,
                "avg_duration_ms": 0.0,
                "p99_duration_ms": 0.0,
                "cache_hit_rate": 0.0,
                "total_images": 0,
            }

        success = sum(1 for r in snapshot if 200 <= r.status_code < 300)
        durations = sorted(r.total_duration_ms for r in snapshot)

        success_rate = success / total

        avg_duration_ms = sum(durations) / total

        p99_idx = max(0, int(total * 0.99) - 1)
        p99_duration_ms = durations[p99_idx] if p99_idx < total else durations[-1]

        # Cache hit rate: count records where vision stage found cache hits.
        cache_hits = 0
        total_vision_stages = 0
        for r in snapshot:
            vs = _find_stage(r, "vision")
            if vs:
                total_vision_stages += 1
                if vs.output.get("cache_hits", 0) > 0:
                    cache_hits += 1
        cache_hit_rate = cache_hits / total_vision_stages if total_vision_stages else 0.0

        # Total images processed.
        total_images = 0
        for r in snapshot:
            ic = _find_stage(r, "image_check")
            if ic and ic.output.get("total_images"):
                total_images += int(ic.output["total_images"])

        return {
            "total": total,
            "success_rate": round(success_rate, 4),
            "avg_duration_ms": round(avg_duration_ms, 2),
            "p99_duration_ms": round(p99_duration_ms, 2),
            "cache_hit_rate": round(cache_hit_rate, 4),
            "total_images": total_images,
        }


# ---------------------------------------------------------------------------
    def restore(self) -> int:
        """Load recent records from SQLite into the in-memory buffer.

        Called once at startup.  Returns the number of records restored.
        """
        try:
            rows = self._get_store().load(limit=self._max_size)
        except Exception:
            logger.warning("Failed to restore traces from SQLite", exc_info=True)
            return 0

        restored = 0
        for row in reversed(rows):  # oldest first
            record = _deserialize_record(row)
            if record is not None:
                with self._lock:
                    self._buffer.append(record)
                restored += 1
        if restored:
            logger.info("Restored %d trace(s) from SQLite", restored)
        return restored


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_record(r: TraceRecord) -> dict:
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
        "stages": [
            {
                "stage": s.stage,
                "input": s.input,
                "output": s.output,
                "duration_ms": s.duration_ms,
                "status": s.status,
            }
            for s in r.stages
        ],
        "replay_of": r.replay_of,
        "replays": r.replays,
    }


def _deserialize_record(d: dict) -> TraceRecord | None:
    try:
        return TraceRecord(
            id=d.get("id", ""),
            timestamp=datetime.fromisoformat(d["timestamp"]) if d.get("timestamp") else datetime.now(timezone.utc),
            method=d.get("method", "POST"),
            path=d.get("path", ""),
            source_format=d.get("source_format", ""),
            target_model=d.get("target_model", ""),
            stream=bool(d.get("stream", False)),
            status_code=int(d.get("status_code", 200)),
            total_duration_ms=float(d.get("total_duration_ms", 0.0)),
            original_body=d.get("original_body", {}),
            stages=[StageSnapshot(**s) for s in d.get("stages", [])],
            replay_of=d.get("replay_of"),
            replays=d.get("replays", []),
        )
    except Exception:
        logger.warning("Failed to deserialize trace record", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

buffer = TraceBuffer(max_size=1000)

# In-flight traces -- keyed by trace_id, removed on finalize.
_traces: dict[str, TraceRecord] = {}
_traces_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public trace hook API (called by pipeline)
# ---------------------------------------------------------------------------


def start_trace(body: dict, path: str) -> str:
    """Begin a new trace for an incoming request.

    Returns the trace id (short UUID) that must be passed to
    ``record_stage`` and ``finalize_trace``.
    """
    from backend.src.pipeline.format_detector import detect_format

    # Detect format from path only (body is available but detect_format uses path).
    try:
        fmt = detect_format(path, body)
    except ValueError:
        fmt = "unknown"

    trace = TraceRecord(
        method="POST",
        path=path,
        source_format=fmt,
        target_model=body.get("model", ""),
        stream=bool(body.get("stream", False)),
        original_body=body,
    )

    with _traces_lock:
        _traces[trace.id] = trace

    return trace.id


def record_stage(trace_id: str, snapshot: StageSnapshot) -> None:
    """Record a single pipeline stage measurement.

    Appends *snapshot* to the in-flight trace identified by *trace_id*.
    If the trace_id is unknown the call is silently ignored.
    """
    with _traces_lock:
        trace = _traces.get(trace_id)
    if trace is None:
        return
    trace.stages.append(snapshot)


def finalize_trace(trace_id: str, status_code: int, total_duration_ms: float) -> None:
    """Finalise a trace, set end-of-request fields, and push it into the ring buffer.

    Once finalised the trace is removed from the in-flight map and can be
    retrieved through ``buffer.get(trace_id)``.
    """
    with _traces_lock:
        trace = _traces.pop(trace_id, None)

    if trace is None:
        return

    trace.status_code = status_code
    trace.total_duration_ms = total_duration_ms
    buffer.append(trace)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def replay_request(
    trace_id: str,
    config: Any,  # ProxyConfig -- lazy import to avoid coupling
    target_url: str = "",
    target_api_key: str = "",
) -> str:
    """Re-execute the pipeline using the original request body.

    Looks up *trace_id* in the buffer, extracts ``original_body``, and
    calls ``pipeline.process_request()`` to re-run the full pipeline.
    The new request gets a fresh ``TraceRecord`` whose ``replay_of``
    field points back to *trace_id*.

    Returns the new trace id.
    """
    from backend.src.core.models import TargetModelConfig

    # Avoid circular import at module level.
    from backend.src.pipeline.pipeline import process_request

    record = buffer.get(trace_id)
    if record is None:
        raise ValueError(f"Trace not found: {trace_id}")

    target_config = TargetModelConfig(
        model_id=record.original_body.get("model", ""),
        api_base=target_url,
        api_key=target_api_key,
    )

    # process_request returns a FastAPI Response; we only need the side effect
    # (trace creation).  The last trace id is the one we just created.
    # We call process_request which internally uses start_trace / finalize_trace.

    # We need a TargetModelClient-like function.  For replay we create a
    # real one via the pipeline module's internals.
    from backend.src.pipeline.pipeline import _get_target_client as _pipeline_get_target_client

    awaitable = process_request(
        body=record.original_body,
        path=record.path,
        target_config=target_config,
        get_target_client_fn=_pipeline_get_target_client,
    )

    # process_request is async -- we need to run it.  The caller is expected
    # to be in an async context (e.g. FastAPI endpoint).  For the module-level
    # API we return a coroutine.
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop -- run synchronously (testing / scripting).
        response = asyncio.run(awaitable)
    else:
        # We are inside an async context; the caller must await this.
        # Since replay_request is synchronous, we cannot await here.
        # Instead we signal to the caller that this needs async handling.
        # For now, return a sentinel and let the dashboard API layer handle it.
        raise RuntimeError(
            "replay_request must be called from a synchronous context "
            "(no running event loop).  Use `await replay_request_async()` instead."
        )

    _ = response  # response is not needed for replay -- trace is already recorded.

    # The trace created by process_request has already been pushed to buffer.
    # Find it by looking for the latest trace whose replay_of matches us.
    with buffer._lock:
        for r in reversed(buffer._buffer):
            if r.replay_of is None and r.original_body == record.original_body:
                # This is a heuristic -- the real one was just created.
                new_id = r.id
                break
        else:
            new_id = "unknown"

    # Link records.
    record.replays.append(new_id)
    new_record = buffer.get(new_id)
    if new_record:
        new_record.replay_of = trace_id

    # Persist replay link updates to SQLite.
    try:
        store = buffer._get_store()
        store.save(_serialize_record(record))
        if new_record:
            store.save(_serialize_record(new_record))
    except Exception:
        logger.warning("Failed to persist replay links to SQLite", exc_info=True)

    return new_id


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_stage(trace: TraceRecord, stage_name: str) -> StageSnapshot | None:
    """Return the first stage snapshot matching *stage_name*, or ``None``."""
    for s in trace.stages:
        if s.stage == stage_name:
            return s
    return None
