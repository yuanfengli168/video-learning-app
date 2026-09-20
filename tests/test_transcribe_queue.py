"""Transcription mini-queue tests (2026-09-16, launch hardening #4).

The scheduler that replaced in-memory BackgroundTask dispatch for
uploads (the 9/12 outage: N uploads = N simultaneous Whisper
pipelines; restarts evaporated the work). Design all ratified in the
audit decision log (#7/#10/#12):

  - videos.status='queued' rows ARE the queue (restart-safe)
  - 2 global slots
  - two-pass per-user fairness:
      pass 1 — only owners with ZERO in-flight transcriptions
      pass 2 — FIFO backfill (sole user gets both slots)
  - atomic claim = one conditional UPDATE (multi-worker safe)
  - 5s stagger between claims from one process

Test strategy: real DB (scratch), REAL _claim_one/_scheduler_pass SQL,
mocked dispatch target. The pass functions read the same tables the
production code touches, so every test exercises the actual claim
queries. Scheduler thread behavior is tested with a short POLL loop
stub, not wall-clock sleeps.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.models import Course, Section, Video, User


# ── Helpers ───────────────────────────────────────────────────────────────


def _mk_user(db: Session, uid: str, role: int = 1) -> None:
    db.add(User(user_id=uid, email=f"{uid}@x.com", role=role))


def _mk_course_section(db: Session, uid: str, title: str = "C") -> Section:
    course = Course(title=title, description="", user_id=uid)
    db.add(course)
    db.flush()
    section = Section(title="S", course_id=course.id, order_index=0)
    db.add(section)
    db.commit()
    return section


def _mk_queued_video(
    db: Session, section: Section, *, created_at: datetime | None = None,
    status: str = "queued", title: str = "v", youtube_id: str | None = None,
) -> Video:
    v = Video(
        title=title, filename=f"{title}.mp4", file_path=f"/tmp/{title}.mp4",
        file_size=1, duration=1.0, order_index=0,
        section_id=section.id, status=status, visibility=0,
        caption_languages="[]", youtube_id=youtube_id,
        created_at=created_at or datetime.utcnow(),
    )
    db.add(v)
    db.commit()
    return v


# Capture the REAL pass at import time — the conftest autouse stub
# replaces the module attribute for every test, so grabbing it here
# (before any fixture runs) is the only ordering-proof reference.
import app.services.transcribe_queue as _tq_module

_REAL_PASS = _tq_module.__dict__["_scheduler_pass"]


@pytest.fixture()
def clean_queue_state(monkeypatch):
    """Freeze the scheduler stagger for deterministic pass-level
    tests. The conftest autouse `no_auto_pipeline` fixture stubs
    _scheduler_pass for ALL tests (a live scheduler must never claim
    real rows mid-test) — these tests call the REAL pass via
    _REAL_PASS (captured at import, immune to fixture ordering)."""
    monkeypatch.setattr(_tq_module, "_last_claim_monotonic", 0.0)
    monkeypatch.setattr(_tq_module, "STAGGER_SECONDS", 0)
    yield _tq_module


def _pass(db: Session) -> int:
    """Run one REAL scheduler pass (the import-time captured
    reference — immune to the conftest stub) with the dispatch
    target mocked (assert on claims, never run Whisper)."""
    with patch("app.routers.courses._staggered_transcribe_job"):
        return _REAL_PASS(db)


# ── Slot accounting ───────────────────────────────────────────────────────


def test_two_slots_hard_cap(db_session, clean_queue_state):
    """Decision #12: exactly 2 concurrent transcriptions, never 3.
    Two in flight → pass claims nothing even with a full queue."""
    section = _mk_course_section(db_session, "u1")
    _mk_queued_video(db_session, section, status="transcribing", title="t1")
    _mk_queued_video(db_session, section, status="transcribing", title="t2")
    # A third waiting video:
    waiting = _mk_queued_video(db_session, section, title="w1")

    claimed = _pass(db_session)
    assert claimed == 0
    db_session.expire_all()
    assert db_session.get(Video, waiting.id).status == "queued"


def test_slot_frees_when_transcription_completes(db_session, clean_queue_state):
    """Slots are released by pipeline completion (status moves past
    'transcribing') — the DB row IS the slot ledger."""
    section = _mk_course_section(db_session, "u1")
    t1 = _mk_queued_video(db_session, section, status="transcribing", title="t1")
    w1 = _mk_queued_video(db_session, section, title="w1")

    # t1 finishes → flips to generating (generation is cloud-side,
    # doesn't hold a slot)
    t1.status = "generating"
    db_session.commit()

    assert _pass(db_session) == 1
    db_session.expire_all()
    assert db_session.get(Video, w1.id).status == "transcribing"


# ── Claim durability (2026-09-20 — the duplicate-dispatch storm) ─────────


def test_claim_survives_session_close(db_session, clean_queue_state):
    """REGRESSION (2026-09-20 outage root cause): the claim's UPDATE
    must be COMMITTED, not left to the session's close. The scheduler
    loop opens sessions with `with SessionLocal() as db:` — on close
    that ROLLS BACK un-flushed work. The original claim code relied on
    that flush alone, so the status flip reverted to 'queued' and the
    next pass (3s later, 4 workers) re-claimed the same row — stacking
    ~60 concurrent transcribe threads for one video and exhausting the
    30-connection QueuePool (site-wide TimeoutErrors, 9/19 AND 9/20).

    This test reproduces the EXACT bug shape: claim via a FRESH session
    (like the scheduler's own), then verify the flip is visible from a
    THIRD session after the first one closes. Rollback → the test fails.
    """
    from app.database import SessionLocal

    section = _mk_course_section(db_session, "u1")
    queued = _mk_queued_video(db_session, section, title="victim")

    # The scheduler's shape: fresh session, claim, close (no commit in
    # the ORIGINAL code — the fix added db.commit() inside _claim_one).
    with SessionLocal() as sched_db:
        claimed_id = clean_queue_state._claim_one(sched_db, pass_two=True)
        assert claimed_id == queued.id

    # Session is CLOSED here. If the claim rolled back, the row reads
    # 'queued' from a fresh session and would be re-claimable → storm.
    with SessionLocal() as verifier:
        row = verifier.get(Video, queued.id)
        assert row.status == "transcribing", (
            "claim did not survive session close — the duplicate-dispatch "
            "storm bug is back (missing db.commit() in _claim_one)"
        )
        # And a second claim in a fresh session finds nothing to claim
        again = clean_queue_state._claim_one(verifier, pass_two=True)
        assert again is None, "the row is re-claimable → double dispatch"


def test_duplicate_dispatch_refused(db_session, clean_queue_state):
    """Belt-and-braces: even if a claim bug regresses (rollback reverts
    a row to 'queued'), the per-process _dispatched_videos guard makes
    the second dispatch of the same video impossible — the scheduler
    logs ERROR and stops, never stacks another Whisper pipeline."""
    tq = clean_queue_state
    section = _mk_course_section(db_session, "u1")
    v1 = _mk_queued_video(db_session, section, title="d1")

    # Simulate the first claim+dispatch (records v1 in the guard set)
    with patch("app.routers.courses._staggered_transcribe_job") as job:
        assert _REAL_PASS(db_session) == 1
        assert v1.id in tq._dispatched_videos

        # Simulate the regression: the row flips BACK to 'queued'
        db_session.expire_all()
        row = db_session.get(Video, v1.id)
        row.status = "queued"
        db_session.commit()

        # A new pass claims it again (SQL matches 'queued' rows)…
        # …but the guard must refuse the second dispatch: the pass
        # reports 0 claims dispatched (logs ERROR and breaks).
        claimed = _REAL_PASS(db_session)
        assert v1.id in tq._dispatched_videos  # still just one entry
        # The pass returned 0: dispatch was refused, no second thread.
        assert claimed == 0, (
            "duplicate dispatch not refused — the guard regressed"
        )


# ── Two-pass fairness (decision #10) ──────────────────────────────────────


def test_pass1_favors_users_with_no_in_flight(db_session, clean_queue_state):
    """THE 20-user scenario: user A has a video in flight; user B
    uploaded later. Pass 1 must claim B's video (owner idle) and skip
    A's second — 'everyone's first video starts before anyone's
    second'."""
    sa = _mk_course_section(db_session, "userA")
    sb = _mk_course_section(db_session, "userB")
    # A has one RUNNING and one waiting (older than B's):
    _mk_queued_video(db_session, sa, status="transcribing", title="A1")
    a2 = _mk_queued_video(
        db_session, sa, title="A2",
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )
    b1 = _mk_queued_video(db_session, sb, title="B1")

    assert _pass(db_session) == 1
    db_session.expire_all()
    assert db_session.get(Video, b1.id).status == "transcribing", \
        "B's FIRST video must jump the queue over A's second"
    assert db_session.get(Video, a2.id).status == "queued"


def test_pass2_backfills_idle_slots_fifo(db_session, clean_queue_state):
    """Pass 1 finds nothing eligible (all owners busy) → pass 2 hands
    the free slot to the oldest queued video — utilization before
    strict fairness (a sole user gets both slots)."""
    sa = _mk_course_section(db_session, "userA")
    _mk_queued_video(db_session, sa, status="transcribing", title="A1")
    a2 = _mk_queued_video(
        db_session, sa, title="A2",
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )

    assert _pass(db_session) == 1
    db_session.expire_all()
    assert db_session.get(Video, a2.id).status == "transcribing"


def test_completed_video_does_not_block_second(db_session, clean_queue_state):
    """Decision #10 detail: COMPLETED videos don't count as in-flight —
    A1 done means A2 is pass-1 eligible (else the fairness degrades
    into pure serialization)."""
    sa = _mk_course_section(db_session, "userA")
    _mk_queued_video(db_session, sa, status="ready", title="A1")
    a2 = _mk_queued_video(db_session, sa, title="A2")

    # Pass 1 claims a2 — its owner has zero TRANScribing videos.
    assert _pass(db_session) == 1
    db_session.expire_all()
    assert db_session.get(Video, a2.id).status == "transcribing"


def test_generating_does_not_hold_slot_or_block(db_session, clean_queue_state):
    """'generating' = cloud-side LLM work — neither holds a slot nor
    blocks the owner's next pass-1 claim."""
    sa = _mk_course_section(db_session, "userA")
    _mk_queued_video(db_session, sa, status="generating", title="A1")
    a2 = _mk_queued_video(db_session, sa, title="A2")

    assert _pass(db_session) == 1
    db_session.expire_all()
    assert db_session.get(Video, a2.id).status == "transcribing"


def test_fifo_order_within_same_eligibility(db_session, clean_queue_state):
    """Same owner, two waiting videos (one in flight): the OLDER
    queued one is claimed first (created_at ASC, id ASC tiebreak)."""
    sa = _mk_course_section(db_session, "userA")
    _mk_queued_video(db_session, sa, status="transcribing", title="A1")
    a2 = _mk_queued_video(
        db_session, sa, title="A2",
        created_at=datetime.utcnow() - timedelta(minutes=5),
    )
    a3 = _mk_queued_video(
        db_session, sa, title="A3",
        created_at=datetime.utcnow() - timedelta(minutes=1),
    )

    assert _pass(db_session) == 1
    db_session.expire_all()
    assert db_session.get(Video, a2.id).status == "transcribing"
    assert db_session.get(Video, a3.id).status == "queued"


# ── Atomic claim — multi-worker safety ────────────────────────────────────


def test_atomic_claim_two_races_one_wins(db_session, clean_queue_state):
    """The multi-worker core: two 'workers' race _claim_one on the
    same single queued row — exactly one flips it to transcribing,
    the other gets None (never double-dispatch). The conditional
    UPDATE + SQLite write serialization guarantee this."""
    from app.services.transcribe_queue import _claim_one

    section = _mk_course_section(db_session, "u1")
    v = _mk_queued_video(db_session, section)

    # Two sessions = two independent 'workers'.
    from app.database import SessionLocal

    s1 = SessionLocal()
    s2 = SessionLocal()
    try:
        w1 = _claim_one(s1, pass_two=True)
        s1.commit()  # claim visible to other connections
        w2 = _claim_one(s2, pass_two=True)
        s2.commit()
    finally:
        s1.close()
        s2.close()

    winners = [w for w in (w1, w2) if w]
    assert len(winners) == 1, "exactly one worker must win the claim"
    assert winners[0] == v.id
    db_session.expire_all()
    assert db_session.get(Video, v.id).status == "transcribing"


# ── Scope guards ─────────────────────────────────────────────────────────


def test_youtube_rows_are_not_claimed(db_session, clean_queue_state):
    """YouTube videos use the (cloud I/O) caption pipeline — the
    Whisper queue must never claim them; they'd otherwise double-run
    their pipeline."""
    section = _mk_course_section(db_session, "u1")
    yt = _mk_queued_video(db_session, section, youtube_id="dQw4w9WgXcQ")

    assert _pass(db_session) == 0
    db_session.expire_all()
    assert db_session.get(Video, yt.id).status == "queued"


def test_uploads_no_longer_dispatch_background_task(
    paid_client, db_session, clean_queue_state
):
    """The 9/12 fix, integration-level: upload creates the row and
    NOTHING dispatches immediately. With the scheduler frozen (the
    conftest default), the row stays 'queued' — proving dispatch
    ownership moved to the queue."""
    from unittest.mock import patch as _p
    import io

    with _p("app.auth.dependencies.verify_token",
            return_value={"uid": "user-A", "email": "a@x.com", "role": 1}):
        course_resp = paid_client.post(
            "/api/courses", json={"title": "C"},
            headers={"Authorization": "Bearer x"},
        )
        course_id = course_resp.json()["course_id"]
        section_resp = paid_client.post(
            f"/api/courses/{course_id}/sections", json={"title": "S"},
            headers={"Authorization": "Bearer x"},
        )
        section_id = section_resp.json()["section_id"]
        with _p("app.routers.videos._run_auto_pipeline") as ap:
            upload = paid_client.post(
                f"/api/videos/upload/{section_id}",
                files={"file": ("t.mp4", io.BytesIO(b"data"), "video/mp4")},
                headers={"Authorization": "Bearer x"},
            )
            assert upload.status_code == 202, upload.text
            ap.assert_not_called(), \
                "the immediate BackgroundTask dispatch is dead — queue owns it"

    vid = upload.json()["video_id"]
    db_session.expire_all()
    v = db_session.get(Video, vid)
    assert v.status == "queued", "row persists as queued until scheduler claims"


def test_scheduler_pass_dispatches_chained_pipeline(
    paid_client, db_session, clean_queue_state
):
    """The scheduler dispatch = _staggered_transcribe_job (the full
    transcribe→generate chain from 3843fcf). Not a transcribe-only
    call — the 9/13 chain bug must never regress via the new path.
    The pass dispatches via a daemon thread, so we patch the TARGET
    with a synchronous-recording fake and wait on an Event (never
    race thread-start against asserts)."""
    import threading

    section = _mk_course_section(db_session, "user-A")
    _mk_queued_video(db_session, section)

    claimed_calls: list = []
    called = threading.Event()

    def fake_job(video_id, delay):
        claimed_calls.append((video_id, delay))
        called.set()

    with patch("app.routers.courses._staggered_transcribe_job", fake_job):
        n = _REAL_PASS(db_session)
    assert n == 1
    assert called.wait(timeout=5), "dispatch thread never called the job"
    assert len(claimed_calls) == 1
    # delay arg 0 — the stagger belongs to the scheduler cadence
    assert claimed_calls[0][1] == 0


# ── Queue position / ETA (decision #10) ───────────────────────────────────


def test_queue_position_and_eta(db_session, clean_queue_state):
    """queue_position: FIFO rank + measured-average ETA. With 3 rows
    ahead of me (all queued, older) → position 4, ETA based on the
    measured avg (with no completed videos in the scratch DB, the
    90s fallback applies: 3 × 90 / 2 = 135s)."""
    from app.services.transcribe_queue import queue_position

    section = _mk_course_section(db_session, "u1")
    base = datetime.utcnow() - timedelta(minutes=30)
    for i in range(3):
        _mk_queued_video(db_session, section, title=f"o{i}",
                         created_at=base + timedelta(minutes=i))
    mine = _mk_queued_video(db_session, section, title="mine",
                            created_at=base + timedelta(minutes=10))

    info = queue_position(db_session, mine.id)
    assert info["position"] == 4
    assert info["ahead"] == 3
    assert info["eta_seconds"] == round(3 * 90.0 / 2)


def test_queue_position_uses_measured_average(db_session, clean_queue_state):
    """When completed rows exist, the ETA uses their REAL average
    transcribe duration instead of the 90s fallback. The probe row is
    created NEWER than one queued row, so it sits BEHIND it — its
    ETA then reflects the measured average (1 × 150s / 2 slots)."""
    from app.services.transcribe_queue import queue_position

    section = _mk_course_section(db_session, "u1")
    # Two completed videos: transcribe took 100s and 200s → avg 150s.
    start = datetime.utcnow() - timedelta(hours=2)
    for dur in (100, 200):
        v = _mk_queued_video(db_session, section, status="ready", title=f"d{dur}")
        v.transcribe_started_at = start
        v.transcribed_at = start + timedelta(seconds=dur)
    db_session.commit()
    # An older queued row → the probe sits behind it.
    _mk_queued_video(
        db_session, section, title="older",
        created_at=datetime.utcnow() - timedelta(minutes=1),
    )
    mine = _mk_queued_video(db_session, section, title="mine")

    info = queue_position(db_session, mine.id)
    assert info["ahead"] == 1, "the older queued row is ahead of mine"
    # 1 ahead × 150s avg / 2 slots = 75s
    assert info["eta_seconds"] == 75


def _last_id(db: Session) -> str:
    v = db.query(Video).order_by(Video.created_at.desc()).first()
    return v.id


def test_status_endpoint_carries_queue_info(paid_client, db_session):
    """GET /status on a QUEUED video includes the queue block (the
    UI's '第 N 位 · 预计 ~X 分钟' data source)."""
    import io

    section = _mk_course_section(db_session, "user-A")
    v = _mk_queued_video(db_session, section)

    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "user-A", "email": "a@x.com", "role": 1}):
        resp = paid_client.get(
            f"/api/videos/{v.id}/status",
            headers={"Authorization": "Bearer x"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["queue"] is not None
    assert body["queue"]["position"] == 1
    assert "eta_seconds" in body["queue"]


def test_status_endpoint_no_queue_block_when_running(paid_client, db_session):
    """Once claimed (transcribing), the queue block disappears — the
    progress bar + transcribe ETA take over the UI."""
    section = _mk_course_section(db_session, "user-A")
    v = _mk_queued_video(db_session, section, status="transcribing")

    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "user-A", "email": "a@x.com", "role": 1}):
        resp = paid_client.get(
            f"/api/videos/{v.id}/status",
            headers={"Authorization": "Bearer x"},
        )
    body = resp.json()
    assert body["queue"] is None


# ── Stagger ───────────────────────────────────────────────────────────────


def test_stagger_blocks_second_claim_in_same_process(db_session, monkeypatch):
    """5s spacing: a second claim within STAGGER_SECONDS of the first
    is deferred to the next pass — two slots never load Whisper in
    the same instant (the 9/12 resource spike)."""
    import time as _time

    import app.services.transcribe_queue as tq

    monkeypatch.setattr(tq, "STAGGER_SECONDS", 5)
    monkeypatch.setattr(tq, "_last_claim_monotonic", _time.monotonic())

    section = _mk_course_section(db_session, "u1")
    _mk_queued_video(db_session, section, title="v1")
    _mk_queued_video(db_session, section, title="v2")

    # Just claimed (monotonic = now), so this pass must claim NOTHING
    # even though a slot is free — the stagger defers it. NOTE: uses
    # _REAL_PASS (the conftest stubs the module attr).
    with patch("app.routers.courses._staggered_transcribe_job"):
        assert _REAL_PASS(db_session) == 0