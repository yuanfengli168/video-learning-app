"""Tests for the FastAPI app and health check endpoint."""

from fastapi.testclient import TestClient


def test_health_check(client: TestClient):
    """Health check endpoint should return 200 and status ok."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "app" in data


def test_app_title():
    """App should have the correct title."""
    from app.main import app

    assert app.title == "Video Learning App"


def test_unknown_route_returns_404(client: TestClient):
    """Unknown routes should return 404."""
    response = client.get("/api/nonexistent")
    assert response.status_code == 404