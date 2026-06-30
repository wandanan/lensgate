"""Tests for the in-memory vision cache."""

from backend.src.pipeline.cache_store import CacheStore


def test_cache_get_matches_exact_focus():
    cache = CacheStore()
    h = "a" * 64

    cache.set(h, "whole image", "describe the whole image")
    cache.set(h, "button details", "focus on the button")

    assert cache.get(h, "describe the whole image") == "whole image"
    assert cache.get(h, "focus on the button") == "button details"
    assert cache.get(h, "read the footer") is None


def test_compare_cache_key_does_not_pollute_single_image_focus(monkeypatch):
    monkeypatch.setenv("VISION_API_KEY", "test-vision-key")
    monkeypatch.setenv("DECISION_API_KEY", "test-decision-key")

    from backend.src.app import _compare_cache_focus

    cache = CacheStore()
    h = "b" * 64
    focus = "请描述这张图片的内容"
    compare_focus = _compare_cache_focus(focus, [h])

    cache.set(h, "multi-image comparison", compare_focus)

    assert cache.get(h, focus) is None
    assert cache.get(h, compare_focus) == "multi-image comparison"
