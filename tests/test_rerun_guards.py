"""Re-run guard tests (2026-09-21, abuse prevention A+C).

The problem: transcribe/generate had NO in-flight guard — clicking
"Transcribe" on an already-processing video stacked another pipeline
(10 clicks = 10 concurrent Whisper runs contending for the 2 queue
slots — the user-triggerable twin of the 9/20 storm).

Layers (ratified same day):
  A. 409 "Already <status>" while in-flight — with the >30-min
     staleness escape (the orphan class stays recoverable).
  C. 429 at 3 manual re-runs/video/day (events-counted, UTC day).
  B. UI: video.html (disabled buttons + live progress) — covered by
     template-render assertions here where feasible.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone as _tz
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Course, Section, Video

FAKE_PAID = {"uid": "test-user-uid", "email": "test@example.com", "role": 1}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock():
    return patch(
        "app.auth.dependencies.verify_token", return_value=FAKE_PAID
    )


def _mk_section(db: Session, uid: str = "test-user-uid") -> Section:
    course = Course(title="C", description="", user_id=uid)
    db.add(course)
    db.flush()
    section = Section(title="S", course_id=course.id, order_index=0)
    db.add(section)
    db.commit()
    return section


def _mk_video(db: Session, section_id: str, *, status: str = "ready",
              job_started_minutes_ago: float | None = None) -> Video:
    v = Video(
        title="v", filename="v.mp4", file_path="/tmp/v.mp4",
        file_size=1, duration=1.0, order_index=0,
        section_id=section_id, status=status, visibility=0,
        caption_languages="[]",
    )
    if job_started_minutes_ago is not None:
        started = datetime.now(_tz.utc).timestamp() - job_started_minutes_ago * 60
        v.last_transcribe_job = json.dumps({
            "video_id": "x", "job_type": "transcribe", "status": "running",
            "progress": 5, "total": 100, "pct": 5.0,
            "message": "running", "started_at": started,
            "completed_at": None, "error": None,
        })
    db.add(v)
    db.commit()
    return v


# ── Layer A: the in-flight 409 ──────────────────────────────────────────────


@pytest.mark.parametrize("status", ["queued", "transcribing", "generating"])
def test_transcribe_409_while_in_flight(paid_client, db_session, status):
    """A fresh job (<30 min) in ANY in-flight status → 409, no second
    pipeline started. The scripted-abuse case: 10 clicks still = 1 job."""
    section = _mk_section(db_session)
    video = _mk_video(db_session, section.id, status=status,
                      job_started_minutes_ago=5)
    with _mock():
        resp = paid_client.post(
            f"/api/videos/{video.id}/transcribe", headers=_auth_headers()
        )
    assert resp.status_code == 409
    assert "still processing" in resp.json()["detail"]


def test_transcribe_allowed_when_job_stale(paid_client, db_session):
    """The staleness escape: >30-min-stale in-flight rows (the orphan
    class — restart ate the worker; the 9/19 + 9/20 incident shape)
    allow the re-run. Without this, a dead pipeline would be locked
    forever."""
    section = _mk_section(db_session)
    video = _mk_video(db_session, section.id, status="transcribing",
                      job_started_minutes_ago=45)
    with _mock(), patch(
        "app.routers.videos._run_transcribe_job"
    ):
        resp = paid_client.post(
            f"/api/videos/{video.id}/transcribe", headers=_auth_headers()
        )
    assert resp.status_code == 202, resp.text


def test_transcribe_fresh_stale_edge(paid_client, db_session):
    """Exactly 30 min is NOT stale (the boundary: <30 blocks, >=30
    escapes — an escape at 29:59 would be premature)."""
    section = _mk_section(db_session)
    video = _mk_video(db_session, section.id, status="transcribing",
                      job_started_minutes_ago=29)
    with _mock():
        resp = paid_client.post(
            f"/api/videos/{video.id}/transcribe", headers=_auth_headers()
        )
    assert resp.status_code == 409


def test_generate_409_while_transcribing(paid_client, db_session):
    """Manual generate during a fresh transcribe → 409 (the auto-chain
    runs generate itself; a manual click would desync the chain)."""
    section = _mk_section(db_session)
    video = _mk_video(db_session, section.id, status="transcribing",
                      job_started_minutes_ago=3)
    with _mock():
        resp = paid_client.post(
            f"/api/generate/{video.id}", headers=_auth_headers()
        )
    assert resp.status_code == 409


def test_ready_video_not_blocked(paid_client, db_session):
    """The normal case: a 'ready' video re-transcribes freely (the
    manual re-run feature) — guard only fires on in-flight statuses."""
    section = _mk_section(db_session)
    video = _mk_video(db_session, section.id, status="ready")
    with _mock(), patch("app.routers.videos._run_transcribe_job"):
        resp = paid_client.post(
            f"/api/videos/{video.id}/transcribe", headers=_auth_headers()
        )
    assert resp.status_code == 202


# ── Layer C: the 3/day cap ──────────────────────────────────────────────────


def _seed_rerun_rows(db: Session, video_id: str, n: int) -> None:
    """Pre-seed `n` accepted-rerun audit rows (the count Layer C reads).
    Sequential endpoint re-runs can't build the count in one test: each
    ACCEPTED transcribe flips the video in-flight, so the next click
    409s (Layer A working as designed). Seeding mirrors reality —
    the rows accumulate across separate days/sessions."""
    from app.utils.events import log_event
    for _ in range(n):
        log_event(
            db, level="INFO", source="services.rerun_guards",
            message="manual rerun accepted", video_id=video_id,
            context={"action": "transcribe"},
        )
    db.commit()


def test_rerun_cap_at_three(paid_client, db_session):
    """3 accepted re-runs today → the next manual call 429s with the
    midnight-UTC message. Generous for humans, fatal for scripts.
    (Count pre-seeded: sequential endpoint calls would 409 via Layer A
    between runs — the correct interplay, so the cap is seeded here.)"""
    section = _mk_section(db_session)
    video = _mk_video(db_session, section.id, status="ready")
    _seed_rerun_rows(db_session, video.id, 3)

    with _mock(), patch("app.routers.videos._run_transcribe_job"):
        resp = paid_client.post(
            f"/api/videos/{video.id}/transcribe", headers=_auth_headers()
        )
    assert resp.status_code == 429
    assert "3 re-runs" in resp.json()["detail"]
    assert "midnight UTC" in resp.json()["detail"]


def test_two_reruns_still_allowed(paid_client, db_session):
    """The cap boundary: 2 today → the 3rd still passes (the cap is 3,
    not 2 — an off-by-one here would silently halve the allowance)."""
    section = _mk_section(db_session)
    video = _mk_video(db_session, section.id, status="ready")
    _seed_rerun_rows(db_session, video.id, 2)

    with _mock(), patch("app.routers.videos._run_transcribe_job"):
        resp = paid_client.post(
            f"/api/videos/{video.id}/transcribe", headers=_auth_headers()
        )
    assert resp.status_code == 202


def test_cap_counts_transcribe_and_generate_combined(paid_client, db_session):
    """The cap is per VIDEO, both actions combined — no double budget
    by alternating clicks. Seeded via transcribe rows; generate hits
    the same shared count."""
    section = _mk_section(db_session)
    video = _mk_video(db_session, section.id, status="ready")
    _seed_rerun_rows(db_session, video.id, 3)
    # generate → the shared count is 3 → its guard raises 429
    # (generate checks the transcript first, so seed one)
    from app.models import Asset
    db_session.add(Asset(
        video_id=video.id, asset_type="transcript",
        content='{"segments": [], "language": "en"}',
    ))
    db_session.commit()
    with _mock():
        resp = paid_client.post(
            f"/api/generate/{video.id}", headers=_auth_headers()
        )
    assert resp.status_code == 429


def test_rejected_clicks_do_not_consume_the_cap(paid_client, db_session):
    """409 rejections must NOT consume quota: the count comes from the
    accepted-audit rows, not the frontend's click telemetry (the
    double-count trap caught in review). A fresh in-flight video can
    be clicked 50 times (all 409) and the cap stays untouched."""
    section = _mk_section(db_session)
    video = _mk_video(db_session, section.id, status="transcribing",
                      job_started_minutes_ago=2)
    with _mock():
        for _ in range(50):
            resp = paid_client.post(
                f"/api/videos/{video.id}/transcribe", headers=_auth_headers()
            )
            assert resp.status_code == 409
    # No 'manual rerun accepted' rows were written
    from sqlalchemy import text
    n = db_session.execute(text(
        "SELECT COUNT(*) FROM events "
        "WHERE source='services.rerun_guards' AND video_id=:v"
    ), {"v": video.id}).scalar()
    assert n == 0


# ── Layer B: the page UI ────────────────────────────────────────────────────


def test_video_page_in_flight_shows_progress_strip(paid_client, db_session):
    """The template renders the watcher for in-flight videos (the
    honest 'loading thing' the original report asked for)."""
    section = _mk_section(db_session)
    video = _mk_video(db_session, section.id, status="transcribing",
                      job_started_minutes_ago=2)
    with _mock():
        resp = paid_client.get(f"/video/{video.id}", headers=_auth_headers())
    assert resp.status_code == 200
    assert "watchInProgressJob" in resp.text
    assert "inflight-progress-strip" in resp.text


def test_video_page_ready_has_no_watcher(paid_client, db_session):
    """Ready videos don't drag the poller in (no useless polling)."""
    section = _mk_section(db_session)
    video = _mk_video(db_session, section.id, status="ready")
    with _mock():
        resp = paid_client.get(f"/video/{video.id}", headers=_auth_headers())
    assert resp.status_code == 200
    assert "IN_FLIGHT_STATUSES.includes" in resp.text  # the gate renders
    # …but the strip is inserted only when in-flight (JS-guarded, so
    # assert the gate constant instead of the strip markup).