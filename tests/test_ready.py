"""Tests for /api/health (liveness) and /api/ready (readiness) — Day 6.

These endpoints are the difference between gunicorn deciding to
kill+restart a worker (liveness fail) and a load balancer deciding
to stop sending traffic to a worker (readiness fail).
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


# ─────────────────────────────────────────────────────────────────────────
# /api/health (liveness) — must always return 200 if the process is up
# ─────────────────────────────────────────────────────────────────────────


def test_health_returns_200():
    """Liveness probe: the process is running and responding.

    Does NOT use the db_session fixture — /api/health must always
    return 200 even when the DB is misbehaving (it's a liveness check,
    not a readiness check)."""
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["app"] == "Video Learning App"
    # 'server' field reports which ASGI server is running (gunicorn
    # in prod, uvicorn in dev). Just check it's a string.
    assert isinstance(data["server"], str)


def test_health_does_not_check_dependencies():
    """Liveness must not depend on DB or Ollama — if it did, an
    unhealthy DB would cause gunicorn to kill workers, which
    doesn't fix the DB. Verify by mocking DB to raise."""
    client = TestClient(app)
    with patch("app.database.SessionLocal", side_effect=Exception("DB down")):
        r = client.get("/api/health")
    # Still 200 — liveness is independent of dependencies
    assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────
# /api/ready (readiness) — must check dependencies
# ─────────────────────────────────────────────────────────────────────────


def test_ready_returns_200_when_all_dependencies_ok(db_session):
    """All checks pass → 200 with 'ready' status."""
    # db_session fixture creates the test DB (including events table)
    client = TestClient(app)
    r = client.get("/api/ready")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ready"
    assert data["db"]["status"] == "ok"
    assert data["ollama_ok"] is True
    assert data["events_table_ok"] is True


def test_ready_returns_503_when_db_unreachable(db_session):
    """If SessionLocal() raises, return 503 so the load balancer stops
    sending traffic to this worker (until DB recovers)."""
    client = TestClient(app)
    with patch(
        "app.database.SessionLocal",
        side_effect=Exception("DB down"),
    ):
        r = client.get("/api/ready")
    assert r.status_code == 503
    data = r.json()
    assert data["status"] == "not_ready"
    assert data["db"]["status"] == "error"
    assert "database_unreachable" in data["reason"]


def test_ready_returns_200_even_when_ollama_down(db_session):
    """Ollama is non-fatal — FREE users on Groq still work without it.
    Report ollama_ok=False in the body but keep 200 status."""
    client = TestClient(app)
    with patch("httpx.get", side_effect=Exception("Ollama down")):
        r = client.get("/api/ready")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ready"  # still ready
    assert data["ollama_ok"] is False
    assert data["db"]["status"] == "ok"  # DB is still fine
