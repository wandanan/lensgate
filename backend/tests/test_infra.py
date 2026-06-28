"""
Test cases for A01 — Project Infrastructure.

Covers:
- TC-A01-BLD-002: FastAPI app creation with correct title
- TC-A01-BLD-005: Directory structure validation
- TC-A01-LOG-001: Route existence check
- TC-A01-API-001: Service startup and HTTP 200/404
"""

import os

import pytest
from fastapi.testclient import TestClient


def test_app_title():
    """TC-A01-BLD-002: FastAPI app 创建无错误，title 非空且正确."""
    from backend.src.app import app
    assert app.title == "多模态代理网关"
    assert app.title != ""


def test_directory_structure():
    """TC-A01-BLD-005: src/ 和 tests/ 目录均存在."""
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert os.path.isdir(os.path.join(backend_dir, "src"))
    assert os.path.isdir(os.path.join(backend_dir, "tests"))


def test_routes_exist():
    """TC-A01-LOG-001: 路由列表包含 /v1/messages、/v1/chat/completions、/health."""
    from backend.src.app import app
    route_paths = [route.path for route in app.routes]
    assert "/v1/messages" in route_paths
    assert "/v1/chat/completions" in route_paths
    assert "/health" in route_paths


def test_root_endpoint():
    """TC-A01-API-001: GET / 返回 200 或 404（非 500）."""
    from backend.src.app import app
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code in (200, 404)
    assert response.status_code != 500


def test_health_endpoint():
    """GET /health 返回 200."""
    from backend.src.app import app
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
