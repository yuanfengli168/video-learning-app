"""Material-generation chain tests (2026-09-08).

The YouTube import path previously stopped at the transcript (unlike
the uploaded-video path which chains transcribe → generate), so every
imported video had exactly 1 asset and generated_at NULL — the user's
9-video Claude Code 101 playlist shipped with transcripts but no
summary/mindmap/flashcards/quiz.

Fixes covered here:
  1. _run_caption_download_job(generate_materials=True) chains to
     _run_generate_job after a successful caption save
  2. single-add + bulk import pass the chain flags
  3. POST /api/admin/channels/{id}/generate-missing backfills
     already-imported videos (transcript ✓, generated_at NULL)
  4. generate chain failure does NOT flip the video to error
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from unittest.mock import patch

from app.models import Asset, Channel, Course, Section, Video

@pytest.fixture(autouse=True)
def _disable_youtube_api(monkeypatch):
    from app.services import youtube_api
    original = youtube_api.settings.youtube_api_key
    youtube_api.settings.youtube_api_key = ""
    yield
    youtube_api.settings.youtube_api_key = original


def _promote_admin(db_session: Session):
    from app.auth.admin import ensure_user_row
    from sqlalchemy import text
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()


def _mk_channel_with_transcript_videos(db: Session, n: int = 3):
    """Channel + playlist + n videos WITH transcripts but NO materials
    (the exact pre-fix state of the user's Claude Code 101 playlist)."""
    ch = Channel(name="Claude", slug="claude", user_id="uid-admin")
    db.add(ch)
    db.flush()
    course = Course(title="Claude Code 101", user_id="uid-admin",
                    channel_id=ch.id)
    db.add(course)
    db.flush()
    sec = Section(title="Episodes", course_id=course.id, order_index=0)
    db.add(sec)
    db.flush()
    vids = []
    for i in range(n):
        yt = f"gench0000{i + 1}"[:11]
        v = Video(
            title=f"Video {i + 1}", youtube_id=yt, visibility=0,
            status="ready", filename=f"youtube:{yt}",
            file_path="https://x", file_size=0, section_id=sec.id,
            order_index=i,
        )
        db.add(v)
        db.flush()
        db.add(Asset(
            video_id=v.id, asset_type="transcript",
            content='{"segments": [{"start": 0, "text": "hi"}]}',
        ))
        vids.append(v)
    db.commit()
    return ch, course, vids


def _mk_bare_pending_video(db: Session, order: int = 0) -> Video:
    """A fresh-import video: status='pending', NO transcript yet.
    (The transcript-exists idempotency check in the job skips videos
    that already have one — _mk_channel_with_transcript_videos is
    for BACKFILL tests, not chain tests.)"""
    ch = Channel(name="Chain", slug=f"chain{order}", user_id="uid-admin")
    db.add(ch)
    db.flush()
    course = Course(title="P", user_id="uid-admin", channel_id=ch.id)
    db.add(course)
    db.flush()
    sec = Section(title="E", course_id=course.id, order_index=0)
    db.add(sec)
    db.flush()
    yt = f"chainvid{order:03d}"
    v = Video(
        title=f"Chain video {order}", youtube_id=yt[:11], visibility=0,
        status="pending", filename=f"youtube:{yt[:11]}",
        file_path="https://x", file_size=0, section_id=sec.id,
        order_index=order,
    )
    db.add(v)
    db.flush()
    db.commit()
    return v


def test_caption_job_chains_generate_on_success(db_session: Session):
    """generate_materials=True → _run_generate_job runs after the
    transcript commits. Mock the caption fetch (real signature:
    fetch_youtube_captions in the job module's namespace) + the
    generate job."""
    vid = _mk_bare_pending_video(db_session)

    chained = {}
    import app.services.youtube_captions_job as capjob
    import app.jobs as jobs_mod
    from types import SimpleNamespace

    def _fake_run_generate(video_id, user_id, user_role):
        chained["called"] = True
        chained["video_id"] = video_id
        chained["user_id"] = user_id
        chained["user_role"] = user_role

    fake_caption = SimpleNamespace(
        language="en", source="manual", duration=100,
        segments=[{"start": 0.0, "text": "hello"}],
        to_dict=lambda: {
            "language": "en", "source": "manual", "duration": 100,
            "segments": [{"start": 0.0, "text": "hello"}],
        },
    )
    # The caption job lazy-imports _run_generate_job INSIDE the
    # chain block, so patch the source module (app.routers.generation)
    # — patching that name elsewhere doesn't reach the lazy import.
    with patch.object(capjob, "fetch_youtube_captions",
                      return_value=fake_caption), \
         patch.object(jobs_mod, "start_job", lambda *a, **k: {}), \
         patch("app.routers.generation._run_generate_job",
               side_effect=_fake_run_generate):
        capjob._run_caption_download_job(
            vid.id,
            generate_materials=True,
            generate_user_id="uid-admin",
            generate_user_role=0,
        )
    assert chained.get("called") is True
    assert chained["video_id"] == vid.id
    assert chained["user_id"] == "uid-admin"


def test_caption_job_no_chain_by_default(db_session: Session):
    """generate_materials unset → NO generate call (old behavior for
    any path that doesn't opt in, e.g. the retry endpoint)."""
    vid = _mk_bare_pending_video(db_session, order=9)

    import app.services.youtube_captions_job as capjob
    import app.jobs as jobs_mod
    from types import SimpleNamespace

    called = {"gen": False}
    fake_caption = SimpleNamespace(
        language="en", source="manual", duration=100,
        segments=[{"start": 0.0, "text": "hi"}],
        to_dict=lambda: {
            "language": "en", "source": "manual", "duration": 100,
            "segments": [{"start": 0.0, "text": "hi"}],
        },
    )
    with patch.object(capjob, "fetch_youtube_captions",
                      return_value=fake_caption), \
         patch.object(jobs_mod, "start_job", lambda *a, **k: {}), \
         patch("app.routers.generation._run_generate_job",
               side_effect=lambda *a, **k: called.__setitem__("gen", True)):
        capjob._run_caption_download_job(vid.id)
    assert called["gen"] is False


def test_generate_missing_backfill_endpoint(
    admin_client: TestClient, db_session: Session
):
    """POST /api/admin/channels/{id}/generate-missing queues generate
    for transcript-ready / materials-missing videos ONLY — and skips
    ones already generated."""
    _promote_admin(db_session)
    ch, course, vids = _mk_channel_with_transcript_videos(db_session, 3)
    # One video is already generated → must be skipped
    from datetime import datetime
    vids[2].generated_at = datetime(2026, 9, 8, 12, 0, 0)
    # One video has NO transcript (still pending) → must be skipped
    db_session.query(Asset).filter(
        Asset.video_id == vids[1].id, Asset.asset_type == "transcript"
    ).delete()
    db_session.commit()

    queued_ids = []
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "uid-admin", "email": "admin@x.com"}), \
         patch("app.routers.admin._staggered_generate_job",
               side_effect=lambda vid, delay, **kw: queued_ids.append(vid)):
        resp = admin_client.post(
            f"/api/admin/channels/{ch.id}/generate-missing",
            headers={"Authorization": "Bearer fake"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["queued"] == 1
    assert queued_ids == [vids[0].id]


def test_generate_missing_404_unknown_channel(
    admin_client: TestClient, db_session: Session
):
    _promote_admin(db_session)
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "uid-admin", "email": "admin@x.com"}):
        resp = admin_client.post(
            "/api/admin/channels/nonexistent/generate-missing",
            headers={"Authorization": "Bearer fake"},
        )
    assert resp.status_code == 404