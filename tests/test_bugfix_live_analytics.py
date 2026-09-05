"""Tests for the 2026-09-05 live-testing bugfix batch (3 bugs).

1. Buttons: unauthorized users see a GREYED-OUT Transcribe/Generate
   (disabled <span> + tooltip) instead of a clickable button that
   403s after the click.
   - PAID on their OWN upload → buttons enabled.
   - PAID on an ADMIN-CURATED catalog video → greyed with the
     "admin-curated videos" tooltip.
   - FREE anywhere → greyed with the upgrade tooltip.
2. Usage monitor: ADMIN (role=0) rows must NOT be mislabeled FREE
   (the `0 or 2` falsy bug) and FREE rows show the 15/day Groq
   bar instead of paid bars.
3. Beacon signed-in detection: base.html renders the
   app-signed-in meta only for authenticated users.
"""

import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch(
        "app.auth.dependencies.verify_token",
        return_value=FAKE_USER,
    )


def _make_catalog_video(db_session, *, visibility: int = 0, owner: str = "admin-uid") -> str:
    """An admin-curated catalog video (lives outside any user course)."""
    from app.models import Course, Section, Video

    course = Course(title="catalog", user_id=owner)
    db_session.add(course)
    db_session.flush()
    section = Section(title="S", course_id=course.id, order_index=0)
    db_session.add(section)
    db_session.flush()
    video = Video(
        title="catalog video", filename="c.mp4", file_path="/tmp/c.mp4",
        file_size=1, duration=1.0, order_index=0,
        section_id=section.id, status="ready", visibility=visibility,
        caption_languages="[]",
    )
    db_session.add(video)
    db_session.commit()
    return video.id


def _upload_own_video(client: TestClient) -> str:
    course_resp = client.post(
        "/api/courses", json={"title": "mine"}, headers=_auth_headers()
    )
    section_resp = client.post(
        f"/api/courses/{course_resp.json()['course_id']}/sections",
        json={"title": "S"}, headers=_auth_headers(),
    )
    upload = client.post(
        f"/api/videos/upload/{section_resp.json()['section_id']}",
        files={"file": ("v.mp4", io.BytesIO(b"x"), "video/mp4")},
        headers=_auth_headers(),
    )
    return upload.json()["video_id"]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Button greying
# ─────────────────────────────────────────────────────────────────────────────

def test_paid_own_video_buttons_enabled(paid_client: TestClient):
    """PAID on their own upload → real clickable buttons."""
    with _mock_auth():
        vid = _upload_own_video(paid_client)
        resp = paid_client.get(f"/video/{vid}", headers=_auth_headers())
    assert resp.status_code == 200
    # Enabled = a <button onclick="transcribeVideo()">
    assert '<button onclick="transcribeVideo()"' in resp.text
    assert "cursor-not-allowed" not in resp.text.split("transcribe-btn")[0].split("id=")[0] if "transcribe-btn" in resp.text else True


def test_paid_catalog_video_buttons_greyed(paid_client: TestClient, db_session):
    """PAID on an admin-curated video → greyed + 'admin-curated' tooltip."""
    vid = _make_catalog_video(db_session)
    with _mock_auth():
        resp = paid_client.get(f"/video/{vid}", headers=_auth_headers())
    assert resp.status_code == 200
    # No clickable transcribe button...
    assert '<button onclick="transcribeVideo()"' not in resp.text
    # ...but the greyed span + the specific tooltip is present
    assert "cursor-not-allowed" in resp.text
    assert "admin-curated videos" in resp.text


def test_free_user_buttons_greyed(client: TestClient, db_session):
    """FREE user on a public catalog video → greyed + upgrade tooltip."""
    vid = _make_catalog_video(db_session)
    with _mock_auth():
        resp = client.get(f"/video/{vid}", headers=_auth_headers())
    assert resp.status_code == 200
    assert '<button onclick="transcribeVideo()"' not in resp.text
    assert "cursor-not-allowed" in resp.text
    assert "Upgrade to" in resp.text


def test_admin_catalog_video_buttons_enabled(admin_client: TestClient, db_session):
    """ADMIN can regen anywhere — buttons stay clickable on catalog videos."""
    vid = _make_catalog_video(db_session)
    with _mock_auth():
        resp = admin_client.get(f"/video/{vid}", headers=_auth_headers())
    assert resp.status_code == 200
    assert '<button onclick="transcribeVideo()"' in resp.text


# ─────────────────────────────────────────────────────────────────────────────
# 2. Usage monitor role + tier bars
# ─────────────────────────────────────────────────────────────────────────────

def _seed_user(db_session, uid: str, role: int):
    db_session.execute(
        text("INSERT OR IGNORE INTO users (user_id, email, role) "
             "VALUES (:uid, :email, :role)"),
        {"uid": uid, "email": f"{uid}@test.com", "role": role},
    )
    db_session.commit()


def _seed_llm_ok(db_session, uid: str, n: int):
    for _ in range(n):
        db_session.execute(
            text(
                "INSERT INTO events (id, ts, level, source, message, user_id, context_json) "
                "VALUES (lower(hex(randomblob(16))), datetime('now'), 'INFO', "
                "'services.llm_providers', 'LLM call succeeded via ollama/glm-5.2:cloud', "
                ":uid, '{}')"
            ),
            {"uid": uid},
        )
    db_session.commit()


def test_usage_monitor_admin_not_mislabeled_free(admin_client: TestClient, db_session):
    """The `0 or 2` falsy bug: ADMIN role rows must keep role=0."""
    _seed_llm_ok(db_session, "test-user-uid", 3)  # the admin_client fixture uid
    from app.services.analytics import get_all_users_usage
    rows = get_all_users_usage(db_session)
    me = [r for r in rows if r["user_id"] == "test-user-uid"]
    assert me, "admin user with usage should be listed"
    assert me[0]["role"] == 0  # ADMIN — not the falsy-bug FREE
    assert me[0]["pct_week"] > 0  # admin gets the paid-style bars


def test_usage_monitor_free_shows_daily_bar(admin_client: TestClient, db_session):
    """FREE rows: 15/day Groq bar (pct_day), NOT the paid 7h/week bars."""
    _seed_user(db_session, "free-user-uid", role=2)
    _seed_llm_ok(db_session, "free-user-uid", 2)
    from app.services.analytics import get_all_users_usage
    rows = get_all_users_usage(db_session)
    free_row = [r for r in rows if r["user_id"] == "free-user-uid"]
    assert free_row
    r = free_row[0]
    assert r["role"] == 2
    assert r["used_day"] == 2
    assert r["pct_day"] > 0        # Groq daily bar populated
    assert r["pct_7h"] == 0        # paid bars suppressed for FREE
    assert r["pct_week"] == 0


def test_usage_monitor_free_renders_groq_rule(admin_client: TestClient, db_session):
    """Rendered page: FREE row shows 'x / 15 today' copy."""
    _seed_user(db_session, "free-user-uid", role=2)
    _seed_llm_ok(db_session, "free-user-uid", 1)
    with _mock_auth():
        resp = admin_client.get("/admin/usage", headers=_auth_headers())
    assert resp.status_code == 200
    assert "/ 15 today" in resp.text
    # The tier explanation in the header
    assert "15 requests / day" in resp.text


# ─────────────────────────────────────────────────────────────────────────────
# 3. Beacon signed-in meta tag
# ─────────────────────────────────────────────────────────────────────────────

def test_signed_in_meta_only_for_authed_users(client: TestClient, db_session):
    vid = _make_catalog_video(db_session)
    # Signed out → the RENDERED meta tag absent (login.html's JS
    # mentions the name when *creating* it after sign-in, so we
    # assert on the rendered tag, not the string).
    client.cookies.clear()
    anon = client.get("/login")
    assert '<meta name="app-signed-in" content="true">' not in anon.text
    # Signed in (any page rendering with user context) → meta present
    with _mock_auth():
        resp = client.get(f"/video/{vid}", headers=_auth_headers())
    assert '<meta name="app-signed-in" content="true">' in resp.text