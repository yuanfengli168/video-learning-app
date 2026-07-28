"""Tests for the Firebase Bearer token path on pocket endpoints.

v0.1.3-real-teaching v0.2 (Firebase auth on iOS):

The pocket router's auth goes through `get_current_user_dev_or_real`,
which now has two paths (after v0.1.3 v0.2):
  1. POCKET_DEV_AUTH=1 + X-Dev-User-Id header → trust the header
     (dev offline UI development)
  2. Otherwise → fall through to `get_current_user` which verifies
     a real Firebase Bearer token via Firebase Admin SDK.

These tests verify the BEARER PATH (path 2) end-to-end. We mock
`verify_token` (the Firebase Admin SDK call) so we don't need a
live Firebase project to run the test suite. We also use
`dependency_overrides` to bypass `POCKET_DEV_AUTH` so the dev path
doesn't accept the request.

We also verify data is segregated per Firebase UID — user A's
favorites/answers don't leak to user B.
"""

import pytest
from unittest.mock import patch

from app.database import SessionLocal
from app.models import Course, Section, Video
from app.pocket.models import PocketChunk


# Two fake UIDs (matches the user's two real UIDs in Firebase project
# video-learning-app-3cf41).
UID_A = "ZInw0yfPUVUZznDwBDwtUOIOxKa2"
UID_B = "ltLtLQzr3nOr2hQKdeTxYnIOYYN2"


def _make_course(db, title: str = "Test Course", owner_uid: str = UID_A):
    c = Course(title=title, description="", user_id=owner_uid)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _make_section(db, course):
    s = Section(title="S", course_id=course.id, order_index=0)
    db.add(s); db.commit(); db.refresh(s)
    return s


def _make_video(db, section):
    v = Video(
        title="V", filename="v.mp4", file_path="/tmp/v.mp4",
        file_size=100, duration=60, order_index=0,
        section_id=section.id, status="ready",
    )
    db.add(v); db.commit(); db.refresh(v)
    return v


def _make_chunk(db, video, index=0):
    ch = PocketChunk(
        video_id=video.id, index=index, start_ts=0, end_ts=60,
        duration_label="5min", concept_title="Concept",
        transcript_quote="Some quote",
        teach_text="Lesson.", check_question="Q?",
    )
    db.add(ch); db.commit(); db.refresh(ch)
    return ch


def _fake_claims_for_uid(uid):
    """Build claims dict like what Firebase Admin SDK would decode."""
    return {
        "uid": uid,
        "email": f"{uid}@example.com",
        "name": f"Test User {uid[:6]}",
        "picture": "",
        "email_verified": True,
    }


def _bearer_headers(uid: str):
    return {"Authorization": f"Bearer fake-token-for-{uid}"}


# MARK: - Shared fixture: prod-mode client (Bearer-only)

@pytest.fixture
def prod_client(db_session, monkeypatch):
    """TestClient with `POCKET_DEV_AUTH` forced OFF so dev header is rejected,
    and the only auth path is Bearer-token based (real Firebase).

    Implementation: `app.pocket.dev_auth.get_current_user_dev_or_real`
    reads `DEV_AUTH_ENABLED` at call time (not import time). So we just
    flip that module attribute to False via monkeypatch, and the dev
    header will be rejected on this test's requests. We do NOT reload
    the module (that breaks the route's cached `Depends()` reference).

    Backed by the same in-memory db_session fixture so we can poke data.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    # Force DEV_AUTH_ENABLED=False for the duration of this test.
    # monkeypatch restores it after the test, so other tests are unaffected.
    from app.pocket import dev_auth as _dev_module
    monkeypatch.setattr(_dev_module, "DEV_AUTH_ENABLED", False)

    try:
        yield TestClient(app)
    finally:
        # monkeypatch handles restoring DEV_AUTH_ENABLED.
        pass


# MARK: - Bearer token tests

def test_snapshot_works_with_bearer_token(prod_client, db_session):
    """A Bearer token gets the user through."""
    course = _make_course(db_session)

    with patch("app.auth.firebase_admin.verify_token",
               return_value=_fake_claims_for_uid(UID_A)):
        r = prod_client.get("/m/snapshot", headers=_bearer_headers(UID_A))
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(c["title"] == "Test Course" for c in body["courses"])


def test_snapshot_rejects_request_without_bearer(prod_client):
    """No Bearer token + no dev header → 401."""
    r = prod_client.get("/m/snapshot")
    assert r.status_code == 401


def test_snapshot_rejects_x_dev_user_id_header_in_prod_mode(prod_client):
    """X-Dev-User-Id header MUST NOT work in this test setup."""
    r = prod_client.get(
        "/m/snapshot",
        headers={"X-Dev-User-Id": UID_A},
    )
    assert r.status_code == 401, (
        "Dev header must NOT be honored when get_current_user_dev_or_real "
        "is overridden to Bearer-only"
    )


def test_snapshot_rejects_invalid_bearer_token(prod_client):
    """Bearer token that fails verification → 401."""
    with patch("app.auth.firebase_admin.verify_token",
               side_effect=ValueError("Invalid token")):
        r = prod_client.get(
            "/m/snapshot",
            headers={"Authorization": "Bearer bad-token"},
        )
    assert r.status_code == 401


def test_data_is_segregated_per_firebase_uid(prod_client, db_session):
    """User A's favorites / answers must not leak to User B."""
    course = _make_course(db_session, title="UID A Course")
    section = _make_section(db_session, course)
    video = _make_video(db_session, section)
    chunk = _make_chunk(db_session, video, index=0)

    # UID_A marks the chunk done with answer + favorite
    with patch("app.auth.firebase_admin.verify_token",
               return_value=_fake_claims_for_uid(UID_A)):
        r = prod_client.post(
            f"/m/chunk/{chunk.id}/done",
            json={"user_answer": "A's answer", "is_favorite": True},
            headers=_bearer_headers(UID_A),
        )
        assert r.status_code == 200, r.text

    # UID_A detail: should see answer + favorite
    with patch("app.auth.firebase_admin.verify_token",
               return_value=_fake_claims_for_uid(UID_A)):
        r = prod_client.get(f"/m/progress/{video.id}/detail",
                            headers=_bearer_headers(UID_A))
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["user_answer"] == "A's answer"
        assert item["is_favorite"] is True

    # UID_B detail: should see NO items (UID_B never marked this chunk done)
    with patch("app.auth.firebase_admin.verify_token",
               return_value=_fake_claims_for_uid(UID_B)):
        r = prod_client.get(f"/m/progress/{video.id}/detail",
                            headers=_bearer_headers(UID_B))
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == [], (
            "UID B should NOT see UID A's progress"
        )

    # UID_B favorites: should be empty (UID_B has no favorites)
    with patch("app.auth.firebase_admin.verify_token",
               return_value=_fake_claims_for_uid(UID_B)):
        r = prod_client.get(f"/m/favorites/{video.id}",
                            headers=_bearer_headers(UID_B))
        assert r.status_code == 200
        body = r.json()
        assert body["favorites"] == [], (
            "UID B should NOT see UID A's favorites"
        )


def test_uid_b_cannot_modify_uid_a_data(prod_client, db_session):
    """User B cannot mark User A's chunk done or favorite it."""
    course = _make_course(db_session)
    section = _make_section(db_session, course)
    video = _make_video(db_session, section)
    chunk = _make_chunk(db_session, video, index=0)

    # UID_B tries to mark A's chunk done
    with patch("app.auth.firebase_admin.verify_token",
               return_value=_fake_claims_for_uid(UID_B)):
        r = prod_client.post(
            f"/m/chunk/{chunk.id}/done",
            json={"user_answer": "B's malicious answer", "is_favorite": True},
            headers=_bearer_headers(UID_B),
        )
    # 200 OK — but the row is stored under UID_B's namespace, NOT UID_A's
    assert r.status_code == 200

    # UID_A fetches: should still see no answer
    with patch("app.auth.firebase_admin.verify_token",
               return_value=_fake_claims_for_uid(UID_A)):
        r = prod_client.get(f"/m/progress/{video.id}/detail",
                            headers=_bearer_headers(UID_A))
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == [], (
            "UID A should see NO progress — UID B's write should not "
            "have polluted UID A's data"
        )

    # UID_B fetches: sees their own (malicious) write
    with patch("app.auth.firebase_admin.verify_token",
               return_value=_fake_claims_for_uid(UID_B)):
        r = prod_client.get(f"/m/progress/{video.id}/detail",
                            headers=_bearer_headers(UID_B))
        body = r.json()
        items = body["items"]
        assert len(items) == 1
        assert items[0]["user_answer"] == "B's malicious answer"


def test_dev_header_still_works_when_pocket_dev_auth_enabled():
    """Backward compat: when POCKET_DEV_AUTH=1 (dev mode), the X-Dev-User-Id
    header still works (used for offline UI development).

    Already covered by the existing test_pocket_v013.py tests — this is
    just a placeholder for documentation. The prod_client fixture above
    proves the Bearer path works; the dev path is exercised by the
    existing auth_client fixture in tests/conftest.py.
    """
    pass