"""
Vision cache store — in-memory cache for image recognition results.

Decouples caching from image extraction so both app.py and decision_engine.py
can depend on a single cache layer without importing image_extractor internals.

Per-hash asyncio locks prevent cache stampede: concurrent requests for the
same image hash serialize at the lock, so only the first calls the vision API.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Warn when cache exceeds this many entries (per-hash).
_MAX_ENTRIES_WARN = 10_000


class CacheStore:
    """In-memory store for vision recognition results.

    Keyed by SHA-256 hash of image data.  Each entry tracks which prompt
    produced which description, plus file metadata for the decision engine.

    ``acquire_lock(h)`` / ``release_lock(h)`` provide per-hash mutual exclusion
    for the cache-stampede pattern::

        cached = cache.get(h)
        if cached:
            return cached
        await cache.acquire_lock(h)
        try:
            cached = cache.get(h)       # double-check
            if cached:
                return cached
            desc = await vision(...)    # only one caller reaches here
            cache.set(h, desc, ...)
            return desc
        finally:
            cache.release_lock(h)
    """

    def __init__(self) -> None:
        self._data: dict[str, dict] = {}
        self._counter: int = 0
        self._locks: dict[str, asyncio.Lock] = {}
        self._warned: bool = False

    def get(self, h: str, focus: str = "") -> str | None:
        """Return the cached description for this hash and focus, or None."""
        entry = self._data.get(h)
        if entry is None:
            return None
        results = entry.get("focus_results", {})
        if not results:
            return None
        return results.get(focus)

    def set(self, h: str, description: str, focus: str = "通用描述",
            file_name: str = "", position: int = 0, label: str = "") -> None:
        """Store a vision description with metadata."""
        if h not in self._data:
            self._data[h] = {
                "file_name": file_name,
                "position": position,
                "label": label,
                "focus_results": {},
            }
        entry = self._data[h]
        entry["focus_results"][focus] = description
        if file_name and not entry.get("file_name"):
            entry["file_name"] = file_name
        if position and not entry.get("position"):
            entry["position"] = position
        if label and not entry.get("label"):
            entry["label"] = label

        if len(self._data) > _MAX_ENTRIES_WARN and not self._warned:
            self._warned = True
            logger.warning(
                "Cache exceeds %d entries (%d) — consider restarting to free memory",
                _MAX_ENTRIES_WARN, len(self._data),
            )

    def entries(self) -> list[dict[str, str]]:
        """Return all cache entries for the decision engine."""
        result: list[dict[str, str]] = []
        for h, entry in self._data.items():
            focus_results = entry.get("focus_results", {})
            summary = focus_results.get("通用描述", "")
            if not summary and focus_results:
                summary = next(iter(focus_results.values()))
            pos = entry.get("position", 0)
            pos_label = f"第{pos}张" if pos else ""
            result.append({
                "hash": h,
                "file_name": entry.get("file_name", ""),
                "position": str(pos),
                "position_label": pos_label,
                "label": entry.get("label", ""),
                "summary": summary,
            })
        return result

    def next_position(self) -> int:
        """Increment and return the global image counter."""
        self._counter += 1
        return self._counter

    # ------------------------------------------------------------------
    # Per-hash locking for cache-stampede prevention
    # ------------------------------------------------------------------

    def acquire_lock(self, h: str) -> asyncio.Lock:
        """Get or create a per-hash asyncio.Lock (without acquiring it).

        Caller must ``await lock.acquire()``, do the vision call + ``set()``,
        then call ``release_lock(h)`` in a ``finally`` block.

        Usage::

            lock = cache.acquire_lock(h)
            await lock.acquire()
            try:
                cached = cache.get(h)   # double-check
                if cached:
                    return cached
                desc = await vision(...)
                cache.set(h, desc, ...)
                return desc
            finally:
                cache.release_lock(h)
        """
        if h not in self._locks:
            self._locks[h] = asyncio.Lock()
        return self._locks[h]

    def release_lock(self, h: str) -> None:
        """Release the per-hash lock if it is currently held.

        Safe to call when the lock doesn't exist or isn't locked.
        """
        lock = self._locks.get(h)
        if lock is not None and lock.locked():
            lock.release()


# Module-level singleton — shared across app.py and image_extractor.py.
cache = CacheStore()
