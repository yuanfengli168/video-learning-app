"""Upload caps + congestion notice tests (2026-09-17, hardening #5).

Audit decision #9 — the two gates + soft guidance:

  DAILY 15/day/user (created_at-based, NO cross-day rollover — a
         public queue delay must not eat the user's quota)
  IN-FLIGHT 6 unfinished (any age — the anti-stockpile anchor)
  Congestion notice: based on PUBLIC queue wait, never on the
         user's own usage; quiet under 30min; the 30min-2h 'busy'
         tier (beta excludes the >2h red tier).

These tests pin: the cap arithmetic (both single + bulk, including
the bulk projection), the cross-day semantics, the 429 responses
with actionable detail, the congestion tiering, and that a FREE user
never reaches the caps (403 from the capability gate comes first).
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.models import Course, Section, Video


def _mk_section(db: Session, uid: str) -> Section:
    c = Course(title="C", user_id=uid)
    db.add(c)
    db.flush()
    s = Section(title="S", course_id=c.id, order_index=0)
    db.add(s)
    db.commit()
    return s


def _mk_video(
    db: Session, section: Section, *, status: str = "ready",
    created_at: datetime | None = None,
) -> Video:
    v = Video(
        title="v", filename="v.mp4", file_path="/tmp/v.mp4", file_size=1,
        duration=1.0, order_index=0, section_id=section.id, status=status,
        visibility=0, caption_languages="[]",
        created_at=created_at or datetime.utcnow(),
    )
    db.add(v)
    db.commit()
    return v


def _auth():
    return patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "user-A", "email": "a@x.com", "role": 1},
    )


def _hdr():
    return {"Authorization": "Bearer x"}


def _upload(paid_client, section_id, name="t.mp4"):
    return paid_client.post(
        f"/api/videos/upload/{section_id}",
        files={"file": (name, io.BytesIO(b"data"), "video/mp4")},
        headers=_hdr(),
    )


def _bulk(paid_client, section_id, n):
    files = [
        ("files", (f"f{i}.mp4", io.BytesIO(b"data"), "video/mp4"))
        for i in range(n)
    ]
    return paid_client.post(
        f"/api/videos/upload-bulk/{section_id}", files=files, headers=_hdr()
    )


# ── Daily cap ─────────────────────────────────────────────────────────────


def test_daily_cap_blocks_16th_upload(paid_client, db_session):
    """15 uploads today, seeded as READY rows (the two caps are
    independent gates — seeding queued rows would trip the 6-in-
    flight cap first, which is correct but not what this test pins)
    → the 16th upload gets 429 with the reset hint."""
    section = _mk_section(db_session, "user-A")
    for _ in range(15):
        _mk_video(db_session, section, status="ready")
    with _auth():
        r16 = _upload(paid_client, section.id, "over.mp4")
    assert r16.status_code == 429
    assert "Daily upload limit" in r16.json()["detail"]
    assert "midnight UTC" in r16.json()["detail"]


def test_daily_cap_created_at_semantics_no_rollover(db_session):
    """Decision #9's quota rule: counts rows created TODAY. Yesterday's
    uploads must not eat today's quota. Seeded READY so the in-flight
    cap (a separate gate correctly counting unfinished work of ANY
    age) doesn't interfere — this test isolates the daily semantics."""
    from app.services.transcribe_queue import upload_caps_check

    section = _mk_section(db_session, "user-A")
    # 15 uploads YESTERDAY, all finished:
    for _ in range(15):
        _mk_video(
            db_session, section, status="ready",
            created_at=datetime.utcnow() - timedelta(days=1),
        )
    caps = upload_caps_check(db_session, "user-A")
    assert caps["allowed"] is True, "yesterday's finished rows must not block today"
    assert caps["used_today"] == 0


def test_daily_cap_counts_today_all_statuses(db_session):
    """Finished or not, everything created today counts toward 15
    (the cap is on ADMISSION rate, not processing rate)."""
    from app.services.transcribe_queue import upload_caps_check

    section = _mk_section(db_session, "user-A")
    for i in range(15):
        _mk_video(db_session, section, status="ready" if i % 2 else "queued")
    caps = upload_caps_check(db_session, "user-A")
    assert caps["allowed"] is False
    assert caps["cap"] == "daily"
    assert caps["used_today"] == 15


# ── In-flight cap ─────────────────────────────────────────────────────────


def test_in_flight_cap_blocks_7th(db_session):
    """6 unfinished videos (any age) → next upload refused with the
    actionable 'wait or delete queued' message."""
    from app.services.transcribe_queue import upload_caps_check

    section = _mk_section(db_session, "user-A")
    # 3 from yesterday (queued), 3 from today — mixed ages, all unfinished.
    for i in range(3):
        _mk_video(db_session, section, status="queued",
                  created_at=datetime.utcnow() - timedelta(days=1))
    for i in range(3):
        _mk_video(db_session, section, status="generating")
    caps = upload_caps_check(db_session, "user-A")
    assert caps["allowed"] is False
    assert caps["cap"] == "in_flight"
    assert "still processing" in caps["reason"]
    assert caps["in_flight"] == 6


def test_ready_videos_do_not_count_in_flight(db_session):
    """Completed videos free the in-flight budget — the cap is a
    stockpile bound, not a lifetime total (finished work doesn't
    block new uploads)."""
    from app.services.transcribe_queue import upload_caps_check

    section = _mk_section(db_session, "user-A")
    for _ in range(10):
        _mk_video(db_session, section, status="ready")
    caps = upload_caps_check(db_session, "user-A")
    assert caps["allowed"] is True
    assert caps["in_flight"] == 0


# ── Bulk projection ───────────────────────────────────────────────────────


def test_bulk_batch_partially_accepted_at_in_flight_cap(paid_client, db_session):
    """A 10-file batch with 3 already in flight: 3 fit the 6-cap, the
    rest are per-file skipped with the exact reason — no disk writes
    for the skipped tail."""
    section = _mk_section(db_session, "user-A")
    for _ in range(3):
        _mk_video(db_session, section, status="queued")

    with _auth():
        resp = _bulk(paid_client, section.id, 10)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    statuses = [r["status"] for r in body["results"]]
    assert statuses.count("queued") == 3, "3 fit the in-flight cap"
    assert statuses.count("skipped") == 7
    # Skipped ones carry the actionable reason:
    skipped_msgs = [r.get("error", "") for r in body["results"] if r["status"] == "skipped"]
    assert any("still processing" in m for m in skipped_msgs)


def test_bulk_16_files_never_exceeds_daily_cap(paid_client, db_session):
    """With 15 READY uploads already today (daily cap FULL, in-flight
    empty): a bulk batch gets EVERY file skipped with the daily-cap
    reason — the batch-level 429 is not raised because the per-file
    projection reports each file individually (bulk UX contract)."""
    section = _mk_section(db_session, "user-A")
    for _ in range(15):
        _mk_video(db_session, section, status="ready")

    with _auth():
        resp = _bulk(paid_client, section.id, 3)
    body = resp.json()
    statuses = [r["status"] for r in body["results"]]
    assert statuses.count("skipped") == 3, (
        "daily cap full → every file in the batch is skipped"
    )
    for r in body["results"]:
        assert "Daily upload limit" in r.get("error", "")


# ── Response shape ─────────────────────────────────────────────────────────


def test_upload_response_carries_caps_and_congestion(paid_client, db_session):
    """The 202 body includes caps_left_today + congestion (None when
    quiet) — the UI's proactive-warning data source."""
    section = _mk_section(db_session, "user-A")
    with _auth():
        r = _upload(paid_client, section.id)
    assert r.status_code == 202
    body = r.json()
    assert body["caps_left_today"] == 14, "first upload of the day → 14 left"
    assert body["congestion"] is None, "empty queue → no notice"


# ── Congestion notice tiering ─────────────────────────────────────────────


def test_congestion_quiet_under_30min(db_session):
    """Waiting × measured-avg ÷ 2 slots < 30min → None (no notice)."""
    from app.services.transcribe_queue import congestion_notice

    section = _mk_section(db_session, "u1")
    # 5 waiting, measured avg ~90s (fallback, no completed rows):
    for _ in range(5):
        _mk_video(db_session, section, status="queued")
    assert congestion_notice(db_session) is None, "5×90s/2 ≈ 3.75min — quiet"


def test_congestion_busy_tier_30min_2h(db_session):
    """≥30min estimated wait → the 'busy' notice with the honest
    message (decision #9's beta tier; the >2h red tier excluded)."""
    from app.services.transcribe_queue import congestion_notice

    section = _mk_section(db_session, "u1")
    # Measured average: completed rows at 300s each → 100 waiting
    # rows ≈ 100×300/2 = 2.5h → busy tier (beta shows it; message
    # says the wait, advises uploading the wanted ones first).
    start = datetime.utcnow() - timedelta(hours=2)
    for dur in (300, 300):
        v = _mk_video(db_session, section, status="ready")
        v.transcribe_started_at = start
        v.transcribed_at = start + timedelta(seconds=dur)
    db_session.commit()
    for _ in range(100):
        _mk_video(db_session, section, status="queued")

    notice = congestion_notice(db_session)
    assert notice is not None
    assert notice["level"] == "busy"
    assert "先传" in notice["message"], "the advice: upload the wanted ones first"


# ── Capability gate comes first ───────────────────────────────────────────


def test_free_user_never_reaches_caps(client, db_session):
    """FREE users hit the UPLOAD_VIDEO capability 403 long before the
    caps logic — the caps only apply to PAID/ADMIN."""
    # client fixture = FREE role by default in conftest.
    section = _mk_section(db_session, "someone")
    with patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "free-user", "email": "f@x.com", "role": 2},
    ):
        r = _upload(client, section.id)
    assert r.status_code == 403
    assert "capability" in r.json()["detail"].lower() or "upgrade" in r.json()["detail"].lower()