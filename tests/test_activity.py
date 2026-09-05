"""Tests for the personal activity page (2026-09-05, commit 5/6).

Covers:
  1. PAID gate — FREE users get 403; PAID/ADMIN get 200.
  2. Only-own-events scope — another user's events never appear.
  3. Summary correctness — seeded plays/chats/llm counts.
  4. Feed renders — messages + video titles present in HTML.
"""

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


def _seed(db_session, *, source: str, message: str, user_id: str,
          video_id: str | None = None):
    db_session.execute(
        text(
            "INSERT INTO events (id, ts, level, source, message, user_id, video_id, context_json) "
            "VALUES (lower(hex(randomblob(16))), datetime('now'), 'INFO', "
            ":source, :message, :user_id, :video_id, '{}')"
        ),
        {"source": source, "message": message, "user_id": user_id,
         "video_id": video_id},
    )


def _make_video(db_session, title: str = "my watched video") -> str:
    from app.models import Course, Section, Video

    course = Course(title="activity t", user_id="test-user-uid")
    db_session.add(course)
    db_session.flush()
    section = Section(title="S", course_id=course.id, order_index=0)
    db_session.add(section)
    db_session.flush()
    video = Video(
        title=title, filename="m.mp4", file_path="/tmp/m.mp4",
        file_size=1, duration=1.0, order_index=0,
        section_id=section.id, status="ready", visibility=0,
        caption_languages="[]",
    )
    db_session.add(video)
    db_session.commit()
    return video.id


# ─────────────────────────────────────────────────────────────────────────────
# 1. Tier gate
# ─────────────────────────────────────────────────────────────────────────────

def test_activity_denied_for_free(client: TestClient):
    """FREE (default client, no role row) → 403 (CHAT_PAID missing)."""
    with _mock_auth():
        resp = client.get("/activity", headers=_auth_headers())
    assert resp.status_code == 403


def test_activity_ok_for_paid(paid_client: TestClient):
    with _mock_auth():
        resp = paid_client.get("/activity", headers=_auth_headers())
    assert resp.status_code == 200


def test_activity_ok_for_admin(admin_client: TestClient):
    with _mock_auth():
        resp = admin_client.get("/activity", headers=_auth_headers())
    assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 2-3. Scope + summary
# ─────────────────────────────────────────────────────────────────────────────

def test_activity_shows_only_own_events(paid_client: TestClient, db_session):
    vid = _make_video(db_session)
    _seed(db_session, source="ui.player", message="ui player play",
          user_id="test-user-uid", video_id=vid)
    # Someone ELSE's events — must never appear
    _seed(db_session, source="ui.player", message="ui player play",
          user_id="someone-else", video_id=vid)
    _seed(db_session, source="ui.chat", message="ui chat message sent",
          user_id="someone-else", video_id=vid)
    db_session.commit()

    from app.services.activity import get_user_activity
    mine = get_user_activity(db_session, "test-user-uid")
    theirs = get_user_activity(db_session, "someone-else")

    assert mine["summary"]["plays"] == 1
    assert theirs["summary"]["plays"] == 1
    # Scoped by uid — my feed has only my rows
    assert all(r["source"] is not None for r in mine["recent"])
    assert len([r for r in mine["recent"] if r["message"] == "ui chat message sent"]) == 0


def test_activity_summary_counts(paid_client: TestClient, db_session):
    vid = _make_video(db_session, title="summary count video")
    for _ in range(3):
        _seed(db_session, source="ui.player", message="ui player play",
              user_id="test-user-uid", video_id=vid)
    _seed(db_session, source="ui.chat", message="ui chat message sent",
          user_id="test-user-uid", video_id=vid)
    _seed(db_session, source="services.llm_providers",
          message="LLM call succeeded via ollama/glm-5.2:cloud",
          user_id="test-user-uid")
    db_session.commit()

    from app.services.activity import get_user_activity
    act = get_user_activity(db_session, "test-user-uid")
    assert act["summary"]["plays"] == 3
    assert act["summary"]["videos_watched"] == 1
    assert act["summary"]["chats"] == 1
    assert act["summary"]["llm_requests"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# 4. Rendering
# ─────────────────────────────────────────────────────────────────────────────

def test_activity_page_renders_feed(paid_client: TestClient, db_session):
    vid = _make_video(db_session, title="render me title")
    _seed(db_session, source="ui.player", message="ui player play",
          user_id="test-user-uid", video_id=vid)
    db_session.commit()

    with _mock_auth():
        resp = paid_client.get("/activity", headers=_auth_headers())

    assert resp.status_code == 200
    assert "render me title" in resp.text
    assert "Your Activity" in resp.text
    assert "No activity recorded yet" not in resp.text


def test_activity_empty_state(paid_client: TestClient, db_session):
    with _mock_auth():
        resp = paid_client.get("/activity", headers=_auth_headers())
    assert resp.status_code == 200
    assert "No activity recorded yet" in resp.text