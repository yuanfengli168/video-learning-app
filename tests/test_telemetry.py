"""Tests for the telemetry ingestion endpoint (2026-09-05).

Covers the security contract from app/routers/telemetry.py:
  1. Auth required — unauthenticated beacons are 401.
  2. Source allowlist — unknown / services.* sources are 400.
  3. Per-source context shape validation (player action, action kind,
     materials tab).
  4. video_id validation — unknown video is 400; tier-inaccessible
     video (FREE user + PAID_ONLY video) is 400 (no probe oracle).
  5. Happy path — valid batch is 202, rows land in `events` with the
     right source / user_id / video_id / context.
  6. Batch size cap — over MAX_EVENTS_PER_BATCH is 422 (pydantic).
"""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.routers.telemetry import MAX_EVENTS_PER_BATCH

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch(
        "app.auth.dependencies.verify_token",
        return_value=FAKE_USER,
    )


def _make_video(db_session, visibility: int = 0) -> str:
    """Insert a course + section + video directly, return video id.

    visibility: 0=PUBLIC, 1=PAID_ONLY, 2=ADMIN_ONLY.
    """
    from app.models import Course, Section, Video

    course = Course(title="telemetry test", user_id="test-user-uid")
    db_session.add(course)
    db_session.flush()
    section = Section(title="S1", course_id=course.id, order_index=0)
    db_session.add(section)
    db_session.flush()
    video = Video(
        title="telemetry test video",
        filename="t.mp4",
        file_path="/tmp/t.mp4",
        file_size=1,
        duration=1.0,
        order_index=0,
        section_id=section.id,
        status="ready",
        visibility=visibility,
        caption_languages="[]",
    )
    db_session.add(video)
    db_session.commit()
    return video.id


# ─────────────────────────────────────────────────────────────────────────────
# 1. Auth
# ─────────────────────────────────────────────────────────────────────────────

def test_telemetry_requires_auth(client: TestClient):
    """No token → 401. Telemetry must never accept anonymous writes.

    (The conftest `client` fixture sets a default valid session cookie +
    mocks verify_token — clear both for this unauthenticated case, per
    the house pattern in tests/test_auth.py.)
    """
    client.cookies.clear()
    with patch("app.auth.dependencies.verify_token", side_effect=ValueError("no token")):
        resp = client.post(
            "/api/telemetry",
            json={"events": [{"source": "ui.login"}]},
        )
    assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 2. Source allowlist
# ─────────────────────────────────────────────────────────────────────────────

def test_telemetry_rejects_unknown_source(client: TestClient):
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [{"source": "ui.bananas"}]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 400
    assert "unknown event source" in resp.json()["detail"]


def test_telemetry_rejects_forged_services_source(client: TestClient):
    """A compromised page must not be able to forge audit-log events."""
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [{"source": "services.llm_providers"}]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 3. Context shape validation
# ─────────────────────────────────────────────────────────────────────────────

def test_telemetry_rejects_bad_player_action(client: TestClient):
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [
                {"source": "ui.player", "context": {"action": "rewind"}},
            ]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 400
    assert "unknown player action" in resp.json()["detail"]


def test_telemetry_rejects_negative_position(client: TestClient):
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [
                {"source": "ui.player",
                 "context": {"action": "pause", "position_ms": -5}},
            ]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 400


def test_telemetry_rejects_bad_action_kind(client: TestClient):
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [
                {"source": "ui.actions", "context": {"action": "delete-all"}},
            ]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 400


def test_telemetry_rejects_materials_without_tab(client: TestClient):
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [{"source": "ui.materials", "context": {}}]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 400


def test_telemetry_rejects_oversized_context(client: TestClient):
    """Context over the size cap → pydantic field_validator 422."""
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [
                {"source": "ui.materials",
                 "context": {"tab": "x", "junk": "y" * 1000}},
            ]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# 4. video_id validation (probe-oracle guard)
# ─────────────────────────────────────────────────────────────────────────────

def test_telemetry_rejects_unknown_video(client: TestClient):
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [
                {"source": "ui.chat", "video_id": "does-not-exist"},
            ]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 400

def test_telemetry_rejects_tier_inaccessible_video(client: TestClient, db_session):
    """FREE user + PAID_ONLY video → 400 (same error as unknown video,
    so the beacon can't distinguish 'exists but locked' from 'absent').

    Uses the default `client` fixture — with no role seeded in the
    users table, the enrichment defaults the user to FREE.
    """
    video_id = _make_video(db_session, visibility=1)  # PAID_ONLY
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [{"source": "ui.chat", "video_id": video_id}]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 400


def test_telemetry_accepts_accessible_video(paid_client: TestClient, db_session):
    """PAID user + PAID_ONLY video → fine."""
    video_id = _make_video(db_session, visibility=1)
    with _mock_auth():
        resp = paid_client.post(
            "/api/telemetry",
            json={"events": [{"source": "ui.chat", "video_id": video_id}]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 202


# ─────────────────────────────────────────────────────────────────────────────
# 5. Happy path — rows land in events
# ─────────────────────────────────────────────────────────────────────────────

def test_telemetry_happy_path_writes_events(client: TestClient, db_session):
    video_id = _make_video(db_session, visibility=0)
    batch = {
        "events": [
            {"source": "ui.login"},
            {"source": "ui.player", "video_id": video_id,
             "context": {"action": "play", "position_ms": 0}},
            {"source": "ui.player", "video_id": video_id,
             "context": {"action": "seek", "from_ms": 1000, "to_ms": 45000}},
            {"source": "ui.materials", "video_id": video_id,
             "context": {"tab": "mindmap"}},
            {"source": "ui.actions", "video_id": video_id,
             "context": {"action": "transcribe", "model": "local-large-turbo"}},
        ]
    }
    with _mock_auth():
        resp = client.post("/api/telemetry", json=batch, headers=_auth_headers())

    assert resp.status_code == 202
    assert resp.json()["accepted"] == 5

    rows = db_session.execute(
        text(
            "SELECT source, message, user_id, video_id, context_json, level "
            "FROM events ORDER BY ts"
        )
    ).fetchall()
    # (Other tests may have written events into this in-memory DB —
    # filter to ours.)
    ours = [r for r in rows if r[2] == "test-user-uid"]
    assert len(ours) == 5
    by_source = {r[0]: r for r in ours}
    assert set(by_source) == {
        "ui.login", "ui.player", "ui.materials", "ui.actions"
    }
    # ui.player appears twice → both rows exist
    player_rows = [r for r in ours if r[0] == "ui.player"]
    assert len(player_rows) == 2
    assert player_rows[0][5] == "INFO"  # level always INFO
    # context serialized through
    ctx = json.loads([r for r in ours if r[0] == "ui.materials"][0][4])
    assert ctx["tab"] == "mindmap"


def test_telemetry_empty_batch_rejected(client: TestClient):
    """min_length=1 — an empty batch is a pydantic 422."""
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": []},
            headers=_auth_headers(),
        )
    assert resp.status_code == 422


def test_telemetry_batch_size_cap(client: TestClient):
    """Over MAX_EVENTS_PER_BATCH → pydantic 422 (bounded write amplification)."""
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [
                {"source": "ui.login"} for _ in range(MAX_EVENTS_PER_BATCH + 1)
            ]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 422