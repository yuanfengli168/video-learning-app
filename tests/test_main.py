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


# ── Static file mount (added in MVP1.1 — transcript-follow experiment) ──


def test_static_serves_transcript_follow_js(client: TestClient):
    """The static mount must serve the transcript-follow JS file with 200
    and a non-empty body. Guards against accidental removal of the
    `app.mount('/static', ...)` line in app/main.py."""
    response = client.get("/static/js/transcript-follow.js")
    assert response.status_code == 200
    body = response.text
    assert "window.TranscriptFollow" in body
    assert "findActiveSegment" in body
    assert "shouldScroll" in body
    assert len(body) > 200  # actual file is ~3.5 KB


def test_static_serves_transcript_follow_css(client: TestClient):
    """The static mount must serve the transcript-follow CSS file."""
    response = client.get("/static/css/transcript-follow.css")
    assert response.status_code == 200
    body = response.text
    assert ".is-follow-active" in body
    assert len(body) > 50


def test_static_returns_404_for_missing_file(client: TestClient):
    """Unknown static paths should 404 (not 500)."""
    response = client.get("/static/js/does-not-exist.js")
    assert response.status_code == 404