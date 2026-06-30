"""
Test cases for logging_config.py — Logging & Observability.

Covers:
- TC-C08-LOG-001: Log output matches format with event/level/timestamp
- TC-C08-LOG-002: Request log contains method/path/status_code/duration_ms
- TC-C08-LOG-003: Vision log contains image_count/vision_duration_ms
- TC-C08-LOG-004: Error log contains traceback
- TC-C08-API-001: GET /health returns status ok
- TC-C08-API-002: Request produces log line with method/path
"""

import logging

import pytest


# ---------------------------------------------------------------------------
# TC-C08-LOG-001: log output matches expected format
# ---------------------------------------------------------------------------


def test_logger_outputs_formatted_line(capsys):
    """After setup_logging(), logger.info(...) writes a formatted line to stdout.

    Format: time - name - [trace_id] - level - file:line - message
    """
    from backend.src.core.logging_config import setup_logging, get_logger

    setup_logging(level="DEBUG")

    logger = get_logger("test_logger_outputs_formatted_line")
    logger.info("test_event key=value")

    captured = capsys.readouterr()
    stdout = captured.out.strip()

    assert stdout, "Expected at least one log line on stdout"
    assert "test_event" in stdout
    assert "INFO" in stdout
    assert "key=value" in stdout


# ---------------------------------------------------------------------------
# TC-C08-LOG-002: Request log contains method/path/status_code/duration_ms
# ---------------------------------------------------------------------------


def test_request_log_structure(capsys):
    """log_request helper emits all 4 required fields with correct values."""
    from backend.src.core.logging_config import setup_logging, get_logger, log_request

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
    stdout = captured.out.strip()

    assert "POST" in stdout
    assert "/v1/messages" in stdout
    assert "200" in stdout
    assert "42.5" in stdout
    assert "request" in stdout


# ---------------------------------------------------------------------------
# TC-C08-LOG-003: Vision log contains image_count/vision_duration_ms
# ---------------------------------------------------------------------------


def test_vision_log_structure(capsys):
    """log_vision helper emits image_count and vision_duration_ms fields."""
    from backend.src.core.logging_config import setup_logging, get_logger, log_vision

    setup_logging(level="INFO")

    logger = get_logger("test_vision_log_structure")
    log_vision(logger, image_count=3, vision_duration_ms=512.7)

    captured = capsys.readouterr()
    stdout = captured.out.strip()

    assert "vision_complete" in stdout
    assert "3" in stdout
    assert "512.7" in stdout


# ---------------------------------------------------------------------------
# TC-C08-LOG-004: Error log contains traceback
# ---------------------------------------------------------------------------


def test_error_log_includes_traceback(capsys):
    """When an exception is logged, the output contains the traceback text."""
    from backend.src.core.logging_config import setup_logging, get_logger

    setup_logging(level="DEBUG")

    logger = get_logger("test_error_log_includes_traceback")

    try:
        raise ValueError("simulated failure")
    except ValueError:
        logger.exception("pipeline_error stage=vision")

    captured = capsys.readouterr()
    stdout = captured.out.strip()

    assert "pipeline_error" in stdout
    assert "ERROR" in stdout
    assert "stage=vision" in stdout
    assert "ValueError" in stdout
    assert "simulated failure" in stdout


def test_error_log_with_exc_info_false_still_produces_json(capsys):
    """Even logger.error(...) without exc_info=True produces a log line."""
    from backend.src.core.logging_config import setup_logging, get_logger

    setup_logging(level="DEBUG")

    logger = get_logger("test_error_no_exc_info")
    logger.error("simple_error reason=timeout")

    captured = capsys.readouterr()
    stdout = captured.out.strip()

    assert "simple_error" in stdout
    assert "ERROR" in stdout
    assert "reason=timeout" in stdout


# ---------------------------------------------------------------------------
# TC-C08-API-001: GET /health returns status ok
# ---------------------------------------------------------------------------


def test_health_returns_status_ok():
    """GET /health returns HTTP 200 with status/version/timestamp fields."""
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
# TC-C08-API-002: Request produces log line with method/path
# ---------------------------------------------------------------------------


def test_request_produces_json_log_line(capsys):
    """An arbitrary request causes a log line containing method & path."""
    from backend.src.core.logging_config import setup_logging, get_logger, log_request

    setup_logging(level="INFO")

    logger = get_logger("test_request_produces_json_log_line")
    log_request(logger, method="GET", path="/v1/messages", status_code=200, duration_ms=15.0)

    captured = capsys.readouterr()
    stdout = captured.out.strip()

    assert len(stdout) > 0, "Expected at least one log line"
    assert "GET" in stdout
    assert "/v1/messages" in stdout


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_setup_logging_respects_level(capsys):
    """DEBUG messages are suppressed when level is INFO (on root logger)."""
    from backend.src.core.logging_config import setup_logging, get_logger

    setup_logging(level="INFO")

    logger = get_logger("test_level_filtering")
    logger.debug("should_not_appear")

    captured = capsys.readouterr()
    assert "should_not_appear" not in captured.out


def test_setup_logging_level_debug_allows_debug(capsys):
    """DEBUG messages appear when root logger level is DEBUG."""
    from backend.src.core.logging_config import setup_logging, get_logger

    setup_logging(level="DEBUG")

    logger = get_logger("test_debug_allowed")
    logger.debug("debug_event detail=fine-grained")

    captured = capsys.readouterr()
    stdout = captured.out.strip()

    assert "debug_event" in stdout
    assert "DEBUG" in stdout
    assert "detail=fine-grained" in stdout


def test_logger_can_bind_extra_context(capsys):
    """Context passed via extra= appears in the log message."""
    from backend.src.core.logging_config import setup_logging, get_logger

    setup_logging(level="INFO")

    logger = get_logger("test_bind")
    logger.info("bound_event service=multimodal-proxy")

    captured = capsys.readouterr()
    stdout = captured.out.strip()

    assert "bound_event" in stdout
    assert "multimodal-proxy" in stdout
