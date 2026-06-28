"""
Test cases for the health-check endpoint.

Covers:
- TC-C01-API-001: GET /health returns 200 with status "ok"
- TC-C01-API-002: GET /health includes version and timestamp fields
"""

from fastapi.testclient import TestClient

from backend.src.app import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# TC-C01-API-001: GET /health returns 200 with status "ok"
# ---------------------------------------------------------------------------


def test_health_returns_200_and_ok():
    """The /health endpoint returns HTTP 200 and status is "ok"."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# TC-C01-API-002: GET /health includes version and timestamp
# ---------------------------------------------------------------------------


def test_health_includes_version_and_timestamp():
    """The /health endpoint body contains "version" and "timestamp" keys."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert "timestamp" in data
    assert data["version"] == "1.0.0"
