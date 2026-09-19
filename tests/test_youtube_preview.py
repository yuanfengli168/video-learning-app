"""Tests for GET /api/admin/videos/youtube/preview (2026-09-19 title autofill).

Covers:
- Happy path: valid URL → 200, metadata fetched (1 quota unit, no captions call)
- No API key → 200 with status='no_api_key' (form degrades to manual typing)
- API failure → 200 with status='failed' (best-effort, never blocks the form)
- Video not found → 200 with status='not_found' (distinct UX: dead URL)
- Duplicate youtube_id → 200 with already_in_catalog=True + DB title,
  and the YouTube API client is NEVER called (quota saving)
- Invalid URL → 400 (same validation rules as the POST path)
- FREE user → 403 (CURATE_CATALOG required)
- No token → 401
- get_video_basic_metadata skips captions.list (the 50-quota-unit call)

Conventions mirror tests/test_admin_router.py (autouse fixtures live there;
here we only need our own _disable_youtube_api since this file doesn't share
that module's conftest — we set the key per-test via monkeypatch instead).
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.admin import clear_role_cache, ensure_user_row
from app.main import app


@pytest.fixture(autouse=True)
def _clear_role_cache():
    clear_role_cache()
    yield
    clear_role_cache()


def _seed_admin(db_session):
    """Make uid-admin an ADMIN and clear the role cache."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()


def _admin_token(uid: str = "uid-admin", email: str = "admin@x.com"):
    return {"uid": uid, "email": email}


def _get_preview(client: TestClient, url: str):
    """GET the preview endpoint as a seeded admin."""
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_admin_token(),
    ):
        return client.get(
            "/api/admin/videos/youtube/preview",
            params={"url": url},
            headers={"Authorization": "Bearer fake"},
        )


# ─────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────


def test_preview_happy_path(client: TestClient, db_session, monkeypatch):
    """Valid URL + working API → 200 with real metadata."""
    from app.services import youtube_api
    from app.services.youtube_api import VideoMetadata

    monkeypatch.setattr(youtube_api.settings, "youtube_api_key", "test-fake-key")
    _seed_admin(db_session)

    canned = VideoMetadata(
        youtube_id="preview0001",
        title="Real YouTube Title",
        channel="Real Channel",
        thumbnail_url="https://i.ytimg.com/vi/preview0001/maxresdefault.jpg",
        duration_seconds=600,
    )
    with patch(
        "app.services.youtube_api.YouTubeAPIClient.get_video_basic_metadata",
        return_value=canned,
    ):
        resp = _get_preview(client, "https://www.youtube.com/watch?v=preview0001")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["youtube_id"] == "preview0001"
    assert body["title"] == "Real YouTube Title"
    assert body["channel"] == "Real Channel"
    assert body["duration_seconds"] == 600
    assert body["thumbnail_url"] is not None
    assert body["already_in_catalog"] is False


def test_preview_no_api_key_degrades_gracefully(
    client: TestClient, db_session, monkeypatch
):
    """Empty key → 200 status='no_api_key', title=None — form stays manual."""
    from app.services import youtube_api

    monkeypatch.setattr(youtube_api.settings, "youtube_api_key", "")
    _seed_admin(db_session)

    resp = _get_preview(client, "https://youtu.be/nokey000001")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "no_api_key"
    assert body["title"] is None
    assert body["already_in_catalog"] is False


def test_preview_api_failure_is_best_effort(
    client: TestClient, db_session, monkeypatch
):
    """Any API error → 200 status='failed' — the preview never blocks the add."""
    from app.services import youtube_api

    monkeypatch.setattr(youtube_api.settings, "youtube_api_key", "test-fake-key")
    _seed_admin(db_session)

    with patch(
        "app.services.youtube_api.YouTubeAPIClient.get_video_basic_metadata",
        side_effect=Exception("network down"),
    ):
        resp = _get_preview(client, "https://youtu.be/fail0000001")

    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"


def test_preview_video_not_found(client: TestClient, db_session, monkeypatch):
    """Deleted/private video → 200 status='not_found' (distinct from 'failed')."""
    from app.services import youtube_api
    from app.services.youtube_api import YouTubeVideoNotFound

    monkeypatch.setattr(youtube_api.settings, "youtube_api_key", "test-fake-key")
    _seed_admin(db_session)

    with patch(
        "app.services.youtube_api.YouTubeAPIClient.get_video_basic_metadata",
        side_effect=YouTubeVideoNotFound("gone"),
    ):
        resp = _get_preview(client, "https://youtu.be/dead000000A")

    assert resp.status_code == 200
    assert resp.json()["status"] == "not_found"


# ─────────────────────────────────────────────────────────────────────────
# Duplicate short-circuit (quota saving)
# ─────────────────────────────────────────────────────────────────────────


def test_preview_duplicate_skips_youtube_api(client: TestClient, db_session, monkeypatch):
    """Already-cataloged youtube_id → DB title + ZERO YouTube API calls."""
    from app.services import youtube_api

    monkeypatch.setattr(youtube_api.settings, "youtube_api_key", "test-fake-key")
    _seed_admin(db_session)

    # Seed a course + section + video with youtube_id='preview0001'
    db_session.execute(text(
        "INSERT INTO courses (id, user_id, title, description) "
        "VALUES ('course-1', 'uid-admin', 'Test Course', 'seeded for preview test')"
    ))
    db_session.execute(text(
        "INSERT INTO sections (id, course_id, title, order_index) "
        "VALUES ('section-1', 'course-1', 'Test Section', 0)"
    ))
    db_session.execute(text(
        "INSERT INTO videos (id, title, filename, file_path, file_size, "
        "section_id, status, caption_languages, youtube_id, visibility, duration, "
        "order_index) "
        "VALUES ('vid-1', 'Existing Catalog Title', 'youtube:preview0001', "
        "'https://www.youtube.com/watch?v=preview0001', 0, 'section-1', "
        "'ready', '[]', 'preview0001', 0, 0, 0)"
    ))
    db_session.commit()

    # If the endpoint calls the API client at all, this mock raises.
    with patch(
        "app.services.youtube_api.YouTubeAPIClient.get_video_basic_metadata",
        side_effect=AssertionError("API called for a duplicate — quota wasted!"),
    ), patch(
        "app.services.youtube_api.YouTubeAPIClient.__init__",
        side_effect=AssertionError("client constructed for duplicate"),
    ):
        resp = _get_preview(client, "https://www.youtube.com/watch?v=preview0001")

    assert resp.status_code == 200
    body = resp.json()
    assert body["already_in_catalog"] is True
    assert body["existing_title"] == "Existing Catalog Title"
    assert body["existing_video_id"] == "vid-1"
    # youtube title stays None — the form shows the DB title, not a fetch
    assert body["title"] is None


# ─────────────────────────────────────────────────────────────────────────
# Validation + auth
# ─────────────────────────────────────────────────────────────────────────


def test_preview_invalid_url_returns_400(client: TestClient, db_session):
    """Non-YouTube garbage → 400 (same rules as the POST path)."""
    _seed_admin(db_session)
    resp = _get_preview(client, "https://example.com/not-youtube")
    assert resp.status_code == 400


def test_preview_free_user_403(client: TestClient, db_session):
    """FREE user lacks CURATE_CATALOG → 403."""
    ensure_user_row("uid-free", "free@x.com", db_session)
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_admin_token("uid-free", "free@x.com"),
    ):
        resp = client.get(
            "/api/admin/videos/youtube/preview",
            params={"url": "https://youtu.be/preview0001"},
            headers={"Authorization": "Bearer fake"},
        )
    assert resp.status_code == 403


def test_preview_no_token_401(client: TestClient):
    """No Bearer → 401."""
    client.cookies.clear()
    resp = client.get("/api/admin/videos/youtube/preview")
    assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────
# Service layer: get_video_basic_metadata skips captions.list
# ─────────────────────────────────────────────────────────────────────────


def test_basic_metadata_skips_captions_call():
    """videos.list called once, captions.list NEVER (1 quota unit, not 51)."""
    from app.services.youtube_api import YouTubeAPIClient

    videos_response = MagicMock()
    videos_response.status_code = 200
    videos_response.json.return_value = {
        "items": [{
            "snippet": {
                "title": "Basic Title",
                "channelTitle": "Basic Channel",
                "thumbnails": {"high": {"url": "https://example.com/h.jpg"}},
            },
            "contentDetails": {"duration": "PT1M"},
        }],
    }

    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = videos_response
        client = YouTubeAPIClient(api_key="test")
        meta = client.get_video_basic_metadata("dQw4w9WgXcQ")

    assert mock_get.call_count == 1  # videos.list ONLY
    assert meta.title == "Basic Title"
    assert meta.channel == "Basic Channel"
    assert meta.duration_seconds == 60
    assert meta.caption_tracks == []
    # The URL hit is the videos endpoint, not captions
    called_url = mock_get.call_args[0][0]
    assert "youtube/v3/videos" in called_url
    assert "/captions" not in called_url