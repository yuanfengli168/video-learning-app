"""Tests for the pocket sync service.

Covers all three sync paths per user decision:
- create → appear in next snapshot
- update → overwrite in next snapshot
- delete → propagate to next snapshot (via the phone-side comparison; we
  verify the source state correctly)

We also test incremental sync via the `since` token.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Asset, Course, Section, Video


@pytest.fixture
def auth_client(db_session):
    """TestClient with the pocket router's auth dependency mocked.

    The router module (`app.pocket.router`) imports
    `get_current_user_dev_or_real as get_current_user` at load time. We
    go through sys.modules to grab the actual module and bind the override
    to that exact function reference, so FastAPI's dep override lookup
    matches regardless of test ordering or env var state.
    """
    import sys
    pocket_router_module = sys.modules["app.pocket.router"]
    dep_callable = pocket_router_module.get_current_user

    app.dependency_overrides.clear()
    app.dependency_overrides[dep_callable] = lambda: {"uid": "test-user-pocket-1", "email": "pocket@test.local"}
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _make_course(db, user_id, title="Test Course"):
    c = Course(user_id=user_id, title=title, description="desc")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _make_section(db, course, title="Test Section", idx=0):
    s = Section(course_id=course.id, title=title, order_index=idx)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _make_video(db, section, title="Test Video", idx=0):
    v = Video(section_id=section.id, title=title, order_index=idx,
              filename=f"{title}.mp4", file_path=f"/tmp/{title}.mp4")
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def _make_asset(db, video, asset_type="summary", content="# hello\nworld"):
    a = Asset(video_id=video.id, asset_type=asset_type, content=content)
    db.add(a)
    db.commit()
    return a


# ── Happy path: snapshot returns everything for the user ────────

def test_snapshot_returns_all_user_courses(auth_client, db_session):
    db = db_session
    _make_course(db, "test-user-pocket-1", "Mine")
    _make_course(db, "test-user-pocket-1", "Mine 2")
    _make_course(db, "other-user", "Not mine")

    r = auth_client.get("/m/snapshot")
    assert r.status_code == 200, r.text
    body = r.json()
    titles = sorted(c["title"] for c in body["courses"])
    assert titles == ["Mine", "Mine 2"]
    assert body["sync_token"]  # non-empty


def test_snapshot_includes_sections_and_videos(auth_client, db_session):
    db = db_session
    c = _make_course(db, "test-user-pocket-1")
    s = _make_section(db, c, "S1")
    _make_video(db, s, "V1")
    _make_video(db, s, "V2")

    body = auth_client.get("/m/snapshot").json()
    assert len(body["sections"]) == 1
    assert len(body["videos"]) == 2
    video_titles = sorted(v["title"] for v in body["videos"])
    assert video_titles == ["V1", "V2"]


def test_snapshot_includes_generated_assets(auth_client, db_session):
    db = db_session
    c = _make_course(db, "test-user-pocket-1")
    s = _make_section(db, c)
    v = _make_video(db, s)
    _make_asset(db, v, "summary", "# hello")
    _make_asset(db, v, "quiz", '[{"q":"a?"}]')
    _make_asset(db, v, "flashcards", '[{"term":"x","definition":"y"}]')
    _make_asset(db, v, "mindmap", "# mindmap")
    _make_asset(db, v, "transcript", '[{"start":0,"end":5,"text":"hi"}]')

    body = auth_client.get("/m/snapshot").json()
    v_out = body["videos"][0]
    assert v_out["summary"] == "# hello"
    assert v_out["quiz"] == '[{"q":"a?"}]'
    assert v_out["flashcards"] == '[{"term":"x","definition":"y"}]'
    assert v_out["mindmap"] == "# mindmap"
    # Transcript is flattened to a string in v0.1
    assert "0.0s" in v_out["transcript"] and "hi" in v_out["transcript"]


# ── Sync: update overwrites ─────────────────────────────────────

def test_snapshot_overwrites_on_update(auth_client, db_session):
    db = db_session
    c = _make_course(db, "test-user-pocket-1", "v1 title")
    s = _make_section(db, c)
    v = _make_video(db, s, "V1")
    _make_asset(db, v, "summary", "old summary")

    # First sync: confirm old summary
    body1 = auth_client.get("/m/snapshot").json()
    assert body1["videos"][0]["summary"] == "old summary"
    token = body1["sync_token"]

    # Update summary
    asset = db.query(Asset).filter(Asset.video_id == v.id, Asset.asset_type == "summary").first()
    asset.content = "NEW summary"
    db.commit()

    # Force updated_at to advance (SQLite sometimes keeps them identical on rapid edits)
    import datetime
    asset.updated_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=1)
    db.commit()

    # Incremental sync — should see the update
    body2 = auth_client.get(f"/m/snapshot?since={token}").json()
    assert len(body2["videos"]) == 1
    assert body2["videos"][0]["summary"] == "NEW summary"


def test_snapshot_incremental_returns_unchanged_videos_omitted(auth_client, db_session):
    """If `since` is set and NOTHING changed, videos array is empty.

    Courses/sections are always returned on incremental sync (see v0.1
    design — they're cheap, and the iOS app does client-side diff to know
    which are new). Videos are the only rows that are filtered by the
    effective `updated_at` so an unchanged source returns an empty videos list.
    """
    db = db_session
    c = _make_course(db, "test-user-pocket-1", "Course A")
    s = _make_section(db, c)
    v = _make_video(db, s, "V1")

    # First sync: grab the token
    token = auth_client.get("/m/snapshot").json()["sync_token"]
    assert token

    # No new updates since. Incremental call should return courses/sections
    # (the v0.1 simplification) but no videos.
    body2 = auth_client.get(f"/m/snapshot?since={token}").json()
    assert len(body2["courses"]) == 1
    assert len(body2["sections"]) == 1
    assert body2["videos"] == []


# ── Sync: delete propagates (via DB state) ──────────────────────

def test_delete_does_not_appear_in_next_snapshot(auth_client, db_session):
    """After a video is deleted, the next snapshot should not include it.

    The phone then knows to drop it locally by comparing snapshots.
    """
    db = db_session
    c = _make_course(db, "test-user-pocket-1")
    s = _make_section(db, c)
    v1 = _make_video(db, s, "V1")
    v2 = _make_video(db, s, "V2")

    body1 = auth_client.get("/m/snapshot").json()
    assert {v["title"] for v in body1["videos"]} == {"V1", "V2"}

    # Delete v1
    db.delete(v1)
    db.commit()

    # Next snapshot: only v2 remains
    body2 = auth_client.get("/m/snapshot").json()
    assert {v["title"] for v in body2["videos"]} == {"V2"}


# ── Auth required ───────────────────────────────────────────────

def test_snapshot_requires_auth(db_session):
    """Without auth override, /m/snapshot should 401."""
    # Fresh client WITHOUT the auth override
    with TestClient(app) as client:
        r = client.get("/m/snapshot")
    assert r.status_code == 401


# ── ETag 304 "nothing changed" fast path ────────────────────────

def test_snapshot_returns_etag_header(auth_client, db_session):
    """Every 200 response must include an ETag header so the phone can
    short-circuit the next call with If-None-Match."""
    r = auth_client.get("/m/snapshot")
    assert r.status_code == 200
    etag = r.headers.get("etag")
    assert etag is not None
    # ETag is a quoted hash; sanity-check shape
    assert etag.startswith('"') and etag.endswith('"')
    assert len(etag) > 10


def test_snapshot_returns_304_when_if_none_match_matches(auth_client, db_session):
    """If the phone sends back the same ETag, server responds 304 with no body."""
    r1 = auth_client.get("/m/snapshot")
    etag = r1.headers["etag"]

    r2 = auth_client.get("/m/snapshot", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    # 304 must NOT include a body — the phone skips JSON decode
    assert r2.content == b""
    # And the ETag should echo back so the phone can keep using it
    assert r2.headers.get("etag") == etag


def test_snapshot_returns_200_when_etag_differs(auth_client, db_session):
    """A different ETag means the phone is behind; server returns fresh data."""
    r1 = auth_client.get("/m/snapshot")
    r2 = auth_client.get("/m/snapshot", headers={"If-None-Match": '"stale-tag-12345"'})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.content != b""
