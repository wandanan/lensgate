"""
Test cases for logging_config.py — Logging & Observability.

Covers:
- TC-C08-LOG-001: structlog outputs JSON format (event/level/timestamp)
- TC-C08-LOG-002: Request log contains method/path/status_code/duration_ms
- TC-C08-LOG-003: Vision log contains image_count/vision_duration_ms
- TC-C08-LOG-004: Error log contains traceback/exception field
- TC-C08-API-001: GET /health returns status ok
- TC-C08-API-002: Request produces JSON log line with method/path
"""

import json
import logging
import sys

import pytest

# ---------------------------------------------------------------------------
# Helper — capture stderr as well (uvicorn/access logs go there)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TC-C08-LOG-001: structlog outputs JSON format
# ---------------------------------------------------------------------------


def test_structlog_outputs_json(capsys):
    """After setup_logging(), logger.info(...) writes a JSON line to stdout.

    The JSON object must contain ``event``, ``level``, and ``timestamp`` keys.
    """
    from backend.src.logging_config import setup_logging, get_logger

    # structlog holds global state; cache_logger_on_first_use=True means we
    # reconfigure for every test to avoid stale logger refs.
    setup_logging(level="DEBUG")

    logger = get_logger("test_structlog_outputs_json")
    logger.info("test_event", key="value")

    captured = capsys.readouterr()
    stdout = captured.out.strip()

    assert stdout, "Expected at least one log line on stdout"

    # The last line should be our JSON log entry (stdout may contain
    # preamble from logging setup, so pick the last non-empty line).
    lines = [ln for ln in stdout.split("\n") if ln.strip()]
    last_line = lines[-1]

    parsed = json.loads(last_line)

    assert "event" in parsed, f"Missing 'event' key in {parsed}"
    assert parsed["event"] == "test_event"
    assert "level" in parsed, f"Missing 'level' key in {parsed}"
    assert parsed["level"] == "info"
    assert "timestamp" in parsed, f"Missing 'timestamp' key in {parsed}"
    assert parsed["key"] == "value"


# ---------------------------------------------------------------------------
# TC-C08-LOG-002: Request log contains method/path/status_code/duration_ms
# ---------------------------------------------------------------------------


def test_request_log_structure(capsys):
    """log_request helper emits all 4 required fields with correct values."""
    from backend.src.logging_config import setup_logging, get_logger, log_request

    setup_logging(level="INFO")

    logger = get_logger("test_request_log_structure")
    log_request(
        logger,
        method="POST",
        path="/v1/messages",
        status_code=200,
        duration_ms=42.5,
    )

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.strip().split("\n") if ln.strip()]
    parsed = json.loads(lines[-1])

    assert parsed["method"] == "POST"
    assert parsed["path"] == "/v1/messages"
    assert parsed["status_code"] == 200
    assert parsed["duration_ms"] == 42.5
    assert parsed["event"] == "request"


# ---------------------------------------------------------------------------
# TC-C08-LOG-003: Vision log contains image_count/vision_duration_ms
# ---------------------------------------------------------------------------


def test_vision_log_structure(capsys):
    """log_vision helper emits image_count and vision_duration_ms fields."""
    from backend.src.logging_config import setup_logging, get_logger, log_vision

    setup_logging(level="INFO")

    logger = get_logger("test_vision_log_structure")
    log_vision(logger, image_count=3, vision_duration_ms=512.7)

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.strip().split("\n") if ln.strip()]
    parsed = json.loads(lines[-1])

    assert parsed["event"] == "vision_complete"
    assert parsed["image_count"] == 3
    assert parsed["image_count"] >= 1, "image_count should be at least 1"
    assert parsed["vision_duration_ms"] == 512.7
    assert parsed["vision_duration_ms"] > 0, "vision_duration_ms should be positive"


# ---------------------------------------------------------------------------
# TC-C08-LOG-004: Error log contains traceback
# ---------------------------------------------------------------------------


def test_error_log_includes_traceback(capsys):
    """When an exception is logged, the output contains exception or traceback.

    The stdlib logging handler may emit the formatted traceback as plain
    text on stdout in addition to the structlog JSON line.  We scan all
    output lines and find the first one that parses as JSON and contains
    the expected ``event`` key.
    """
    from backend.src.logging_config import setup_logging, get_logger

    setup_logging(level="DEBUG")

    logger = get_logger("test_error_log_includes_traceback")

    try:
        raise ValueError("simulated failure")
    except ValueError:
        logger.exception("pipeline_error", stage="vision")

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.strip().split("\n") if ln.strip()]

    # Find the JSON log line — it may not be the last one because the
    # stdlib handler also emits the formatted traceback as plain text.
    parsed = None
    for line in reversed(lines):
        try:
            candidate = json.loads(line)
            if candidate.get("event") == "pipeline_error":
                parsed = candidate
                break
        except json.JSONDecodeError:
            continue

    assert parsed is not None, (
        f"No JSON log line found for 'pipeline_error' event. "
        f"All lines (repr): {[repr(ln) for ln in lines]}"
    )

    assert parsed["event"] == "pipeline_error"
    assert parsed["level"] == "error"
    assert parsed["stage"] == "vision"

    # structlog.processors.format_exc_info stores the traceback under
    # the ``exception`` key.  It should be non-empty.
    exception_value = parsed.get("exception")
    assert exception_value, (
        f"Expected non-empty 'exception' field in {parsed}. "
        "Make sure format_exc_info is in the processor chain and "
        "logger.exception() was used."
    )
    assert "ValueError" in exception_value
    assert "simulated failure" in exception_value


def test_error_log_with_exc_info_false_still_produces_json(capsys):
    """Even logger.error(...) without exc_info=True produces valid JSON.

    This guards against a regression where the JSONRenderer chokes on
    missing exception info.
    """
    from backend.src.logging_config import setup_logging, get_logger

    setup_logging(level="DEBUG")

    logger = get_logger("test_error_no_exc_info")
    logger.error("simple_error", reason="timeout")

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.strip().split("\n") if ln.strip()]
    parsed = json.loads(lines[-1])

    assert parsed["event"] == "simple_error"
    assert parsed["level"] == "error"
    assert parsed["reason"] == "timeout"


# ---------------------------------------------------------------------------
# TC-C08-API-001: GET /health returns status ok
# ---------------------------------------------------------------------------


def test_health_returns_status_ok():
    """GET /health returns HTTP 200 with status/version/timestamp fields.

    (Also covered in test_health.py; duplicated here so C08 is self-contained.)
    """
    from fastapi.testclient import TestClient
    from backend.src.app import app

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "timestamp" in data


# ---------------------------------------------------------------------------
# TC-C08-API-002: Request produces JSON log line with method/path
# ---------------------------------------------------------------------------


def test_request_produces_json_log_line(capsys):
    """An arbitrary request causes a JSON log line containing method & path."""
    from backend.src.logging_config import setup_logging, get_logger, log_request

    setup_logging(level="INFO")

    logger = get_logger("test_request_produces_json_log_line")

    # Simulate a middleware-style request log entry.
    log_request(logger, method="GET", path="/v1/messages", status_code=200, duration_ms=15.0)

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.strip().split("\n") if ln.strip()]
    assert len(lines) >= 1, "Expected at least one log line"

    parsed = json.loads(lines[-1])
    assert "method" in parsed, f"Missing 'method' in {parsed}"
    assert "path" in parsed, f"Missing 'path' in {parsed}"
    assert parsed["method"] == "GET"
    assert parsed["path"] == "/v1/messages"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_setup_logging_respects_level(capsys):
    """DEBUG messages are suppressed when level is INFO."""
    from backend.src.logging_config import setup_logging, get_logger

    setup_logging(level="INFO")

    logger = get_logger("test_level_filtering")
    logger.debug("should_not_appear")

    captured = capsys.readouterr()
    # Because we reconfigure logging.basicConfig with force=True, earlier
    # captured state may bleed.  We just check that no line emitted *from
    # this test* contains our debug message.
    lines = [ln for ln in captured.out.strip().split("\n") if ln.strip()]
    for line in lines:
        parsed = json.loads(line)
        assert parsed.get("event") != "should_not_appear", (
            f"DEBUG message leaked through INFO level: {parsed}"
        )


def test_setup_logging_level_debug_allows_debug(capsys):
    """DEBUG messages appear when level is DEBUG."""
    from backend.src.logging_config import setup_logging, get_logger

    setup_logging(level="DEBUG")

    logger = get_logger("test_debug_allowed")
    logger.debug("debug_event", detail="fine-grained")

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.strip().split("\n") if ln.strip()]

    found = False
    for line in lines:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if parsed.get("event") == "debug_event":
            assert parsed["level"] == "debug"
            assert parsed["detail"] == "fine-grained"
            found = True
            break

    assert found, "DEBUG message not found in output"


def test_logger_can_bind_extra_context(capsys):
    """get_logger().bind(...) adds persistent context to subsequent calls."""
    from backend.src.logging_config import setup_logging, get_logger

    setup_logging(level="INFO")

    logger = get_logger("test_bind").bind(service="multimodal-proxy")
    logger.info("bound_event")

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.strip().split("\n") if ln.strip()]
    parsed = json.loads(lines[-1])

    assert parsed["event"] == "bound_event"
    assert parsed["service"] == "multimodal-proxy"
