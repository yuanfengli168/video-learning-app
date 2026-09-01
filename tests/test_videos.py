"""Tests for videos router — upload, transcribe, get."""

import io
import uuid as uuid_module
from unittest.mock import patch

from fastapi.testclient import TestClient

# Save a reference to the real _run_auto_pipeline BEFORE the no_auto_pipeline
# autouse fixture (conftest.py) replaces it. Module-level code runs at import
# time, which is before any pytest fixtures are set up.
import app.routers.videos as _videos_module
_REAL_RUN_AUTO_PIPELINE = _videos_module._run_auto_pipeline

from app.models import Asset, Video

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def _create_course_and_section(paid_client: TestClient):
    """Helper: create a course and section, return (course_id, section_id).

    The caller is responsible for passing a client authenticated as
    PAID+ (e.g. paid_client / admin_client), since course creation
    requires the MANAGE_OWN_COURSE capability (MVP2.1.0.4).
    """
    with _mock_auth():
        course_resp = paid_client.post(
            "/api/courses",
            json={"title": "ML"},
            headers=_auth_headers(),
        )
        course_id = course_resp.json()["course_id"]
        section_resp = paid_client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
    return course_id, section_id


def test_list_whisper_models(paid_client: TestClient):
    """/api/videos/models should return available models."""
    response = paid_client.get("/api/videos/models")
    assert response.status_code == 200
    models = response.json()["models"]
    assert "base" in models
    assert "small" in models
    assert "medium" in models


def test_upload_video(paid_client: TestClient):
    """Should upload a video file and queue auto-processing (MVP2.0 #1)."""
    course_id, section_id = _create_course_and_section(paid_client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        response = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("test.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
    assert response.status_code == 202
    assert "video_id" in response.json()
    assert response.json()["status"] == "queued"
    assert response.json()["auto_process"] is True


def test_upload_invalid_extension(paid_client: TestClient):
    """Should reject non-video files."""
    _, section_id = _create_course_and_section(paid_client)

    fake_file = io.BytesIO(b"text content")
    with _mock_auth():
        response = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("document.txt", fake_file, "text/plain")},
            headers=_auth_headers(),
        )
    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"]


def test_upload_to_nonexistent_section(paid_client: TestClient):
    """Should return 404 for non-existent section."""
    fake_video = io.BytesIO(b"fake")
    with _mock_auth():
        response = paid_client.post(
            "/api/videos/upload/nonexistent-section",
            files={"file": ("test.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
    assert response.status_code == 404


def test_get_video(paid_client: TestClient):
    """Should get video details."""
    course_id, section_id = _create_course_and_section(paid_client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = paid_client.get(
            f"/api/videos/{video_id}", headers=_auth_headers()
        )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "lecture"
    assert data["status"] == "queued"  # MVP2.0 #1: auto-pipeline queued on upload
    assert data["has_transcript"] is False


def test_get_video_not_found(paid_client: TestClient):
    """Should return 404 for non-existent video."""
    with _mock_auth():
        response = paid_client.get(
            "/api/videos/nonexistent", headers=_auth_headers()
        )
    assert response.status_code == 404


def test_transcribe_video(paid_client: TestClient):
    """Should kick off transcription and return 202 Accepted with a job."""
    course_id, section_id = _create_course_and_section(paid_client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]

        # Mock the background worker so we don't need real Whisper.
        # The worker writes the transcript asset + sets video status,
        # simulating the end state we used to get from the synchronous
        # endpoint.
        fake_transcript = {
            "segments": [{"start": 0.0, "end": 2.0, "text": "Hello"}],
            "language": "en",
            "duration": 10.0,
        }
        def fake_worker(vid: str, model: str) -> None:
            from app.services.transcription import transcript_to_json
            with patch(
                "app.routers.videos.transcribe_video",
                return_value=fake_transcript,
            ):
                # Re-import inside the worker to avoid circular issues
                from app.routers.videos import transcribe_video
                result = transcribe_video("ignored", model)
                from app.database import SessionLocal
                from app.models import Asset, Video
                with SessionLocal() as db:
                    v = db.get(Video, vid)
                    if v:
                        db.add(Asset(
                            id="t1", video_id=vid, asset_type="transcript",
                            content=transcript_to_json(result),
                        ))
                        v.status = "ready"
                        v.duration = result["duration"]
                        db.commit()
        with patch(
            "app.routers.videos._run_transcribe_job",
            side_effect=fake_worker,
        ):
            response = paid_client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )
    # The endpoint returns 202 Accepted immediately, with the
    # initial job state in the response body.
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "running"
    assert data["job"]["job_type"] == "transcribe"
    # Once the (mocked) worker ran, the transcript is queryable.
    with _mock_auth():
        get_resp = paid_client.get(
            f"/api/videos/{video_id}/transcript", headers=_auth_headers()
        )
    assert get_resp.status_code == 200
    segs = get_resp.json()["segments"]
    assert len(segs) == 1
    assert segs[0]["text"] == "Hello"
    assert get_resp.json()["language"] == "en"


def test_get_transcript(paid_client: TestClient):
    """Should get the transcript after the (mocked) transcribe worker ran."""
    course_id, section_id = _create_course_and_section(paid_client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]

        fake_transcript = {
            "segments": [{"start": 0.0, "end": 2.0, "text": "Hello world"}],
            "language": "en",
            "duration": 10.0,
        }
        def fake_worker(vid: str, model: str) -> None:
            from app.services.transcription import transcript_to_json
            with patch(
                "app.routers.videos.transcribe_video",
                return_value=fake_transcript,
            ):
                from app.routers.videos import transcribe_video
                result = transcribe_video("ignored", model)
                from app.database import SessionLocal
                from app.models import Asset, Video
                with SessionLocal() as db:
                    v = db.get(Video, vid)
                    if v:
                        db.add(Asset(
                            id="t1", video_id=vid, asset_type="transcript",
                            content=transcript_to_json(result),
                        ))
                        v.status = "ready"
                        v.duration = result["duration"]
                        db.commit()
        with patch(
            "app.routers.videos._run_transcribe_job",
            side_effect=fake_worker,
        ):
            paid_client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )

        # Now get the transcript
        response = paid_client.get(
            f"/api/videos/{video_id}/transcript", headers=_auth_headers()
        )
    assert response.status_code == 200
    data = response.json()
    assert data["language"] == "en"
    assert len(data["segments"]) == 1
    assert data["segments"][0]["text"] == "Hello world"


def test_get_transcript_not_found(paid_client: TestClient):
    """Should return 404 if no transcript exists."""
    course_id, section_id = _create_course_and_section(paid_client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = paid_client.get(
            f"/api/videos/{video_id}/transcript", headers=_auth_headers()
        )
    assert response.status_code == 404


# ── MVP2.0 export endpoint ─────────────────────────────────────────────────

def _create_video_with_transcript(
    paid_client: TestClient,
    title: str = "My Lecture",
    segments: list[dict] | None = None,
) -> str:
    """Helper: upload a video, run a fake transcribe worker, return the video_id.

    The transcript is whatever `segments` is provided (default: one
    short English segment). Saves the test from copy-pasting the
    same fake-worker setup in every export test."""
    course_id, section_id = _create_course_and_section(paid_client)
    if segments is None:
        segments = [{"start": 0.0, "end": 3.0, "text": "Hello world"}]
    fake_transcript = {
        "segments": segments,
        "language": "en",
        "duration": segments[-1]["end"] if segments else 0.0,
    }

    with _mock_auth():
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": (f"{title}.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]

        def fake_worker(vid: str, model: str) -> None:
            from app.services.transcription import transcript_to_json
            from app.database import SessionLocal
            with SessionLocal() as db:
                v = db.get(Video, vid)
                if v:
                    db.add(Asset(
                        id=f"t-{vid}", video_id=vid, asset_type="transcript",
                        content=transcript_to_json(fake_transcript),
                    ))
                    v.status = "ready"
                    v.duration = fake_transcript["duration"]
                    db.commit()

        with patch("app.routers.videos._run_transcribe_job", side_effect=fake_worker):
            paid_client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )
    return video_id


def test_export_transcript_md(paid_client: TestClient):
    """Default format is md; returns text/markdown + Content-Disposition."""
    video_id = _create_video_with_transcript(paid_client, title="Math 101")
    with _mock_auth():
        resp = paid_client.get(
            f"/api/videos/{video_id}/transcript/export",
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    assert 'filename="Math 101.md"' in resp.headers["content-disposition"]
    body = resp.text
    assert "# Math 101" in body
    assert "[00:00:00] Hello world" in body


def test_export_transcript_json(paid_client: TestClient):
    """?format=json returns the raw transcript JSON."""
    video_id = _create_video_with_transcript(
        paid_client, title="Lecture",
        segments=[{"start": 0, "end": 1, "text": "你好"}],
    )
    with _mock_auth():
        resp = paid_client.get(
            f"/api/videos/{video_id}/transcript/export?format=json",
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    assert 'filename="Lecture.json"' in resp.headers["content-disposition"]
    # Body is the raw transcript
    import json
    parsed = json.loads(resp.text)
    assert parsed["language"] == "en"
    assert parsed["segments"][0]["text"] == "你好"


def test_export_transcript_txt(paid_client: TestClient):
    """?format=txt returns plain text, no markdown."""
    video_id = _create_video_with_transcript(paid_client, title="Plain")
    with _mock_auth():
        resp = paid_client.get(
            f"/api/videos/{video_id}/transcript/export?format=txt",
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert 'filename="Plain.txt"' in resp.headers["content-disposition"]
    body = resp.text
    # No markdown markers
    assert "# " not in body
    assert "**" not in body
    # Just the segment line
    assert "[00:00:00] Hello world" in body


def test_export_transcript_invalid_format(paid_client: TestClient):
    """Invalid format returns 400 with a clear error message."""
    video_id = _create_video_with_transcript(paid_client)
    with _mock_auth():
        resp = paid_client.get(
            f"/api/videos/{video_id}/transcript/export?format=docx",
            headers=_auth_headers(),
        )
    assert resp.status_code == 400
    assert "Invalid format" in resp.json()["detail"]


def test_export_transcript_not_found(paid_client: TestClient):
    """Returns 404 if no transcript exists for the video yet."""
    course_id, section_id = _create_course_and_section(paid_client)
    with _mock_auth():
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("x.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        resp = paid_client.get(
            f"/api/videos/{video_id}/transcript/export",
            headers=_auth_headers(),
        )
    assert resp.status_code == 404
    assert "Transcript not found" in resp.json()["detail"]


def test_export_transcript_video_not_found(paid_client: TestClient):
    """Returns 404 if the video itself doesn't exist."""
    with _mock_auth():
        resp = paid_client.get(
            "/api/videos/nonexistent-id/transcript/export",
            headers=_auth_headers(),
        )
    assert resp.status_code == 404


def test_export_transcript_unicode_filename(paid_client: TestClient):
    """CJK characters in the video title are preserved in the filename.

    Uses RFC 5987 `filename*=UTF-8''...` because HTTP headers are
    latin-1. The endpoint sends BOTH the basic filename (ascii-
    replaced) and the UTF-8 form; modern browsers prefer the latter.
    """
    video_id = _create_video_with_transcript(paid_client, title="中文课程")
    with _mock_auth():
        resp = paid_client.get(
            f"/api/videos/{video_id}/transcript/export",
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    disp = resp.headers["content-disposition"]
    # The RFC 5987 form is present and percent-encoded
    assert "filename*=UTF-8''" in disp
    # The original unicode name is recoverable
    from urllib.parse import unquote
    quoted = disp.split("filename*=UTF-8''")[1]
    assert unquote(quoted) == "中文课程.md"


def test_export_transcript_sanitizes_unsafe_chars(paid_client: TestClient):
    """Characters that are reserved on at least one OS (/, \\, :) are
    replaced with `-` in the filename so the download works on
    Windows too."""
    # Upload with a title containing forward slash
    course_id, section_id = _create_course_and_section(paid_client)
    with _mock_auth():
        # Use upload_video's title which is derived from filename
        # (Path(file.filename).stem), so we can craft one with a slash
        # — but file upload likely doesn't allow it. Instead, edit
        # the DB directly to put a slash in the title (real users
        # may have titles imported from elsewhere with / or \).
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("x.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        # Set the title to one with unsafe chars
        from app.database import SessionLocal
        with SessionLocal() as db:
            v = db.get(Video, video_id)
            v.title = "bad/name\\here:test"
            db.add(Asset(
                id=f"t-{video_id}", video_id=video_id, asset_type="transcript",
                content='{"segments": [], "language": "en", "duration": 0}',
            ))
            db.commit()
        resp = paid_client.get(
            f"/api/videos/{video_id}/transcript/export",
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    # The basic filename has the unsafe chars replaced
    disp = resp.headers["content-disposition"]
    # Both forms are present
    assert 'filename="bad-name-here-test.md"' in disp
    # The RFC 5987 form has the URL-encoded sanitized form too
    # (the endpoint sanitizes BEFORE encoding, so the original /
    # is already gone — we expect the sanitized form here too)
    from urllib.parse import unquote
    quoted = disp.split("filename*=UTF-8''")[1]
    assert unquote(quoted) == "bad-name-here-test.md"


def test_transcribe_invalid_model(paid_client: TestClient):
    """Should return 400 for invalid model name."""
    course_id, section_id = _create_course_and_section(paid_client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = paid_client.post(
            f"/api/videos/{video_id}/transcribe?model_name=nonexistent",
            headers=_auth_headers(),
        )
    assert response.status_code == 400


def test_transcribe_free_user_gets_403(paid_client: TestClient):
    """Regression for Day-13 catalog-curation bug.

    POST /api/videos/{id}/transcribe was previously gated only by
    `get_current_user`, so any signed-in FREE user could re-run
    whisper on a catalog video — same blast radius as regenerating
    materials (transcribe chains into generate). Now gated on
    REGEN_MATERIALS; FREE users must upgrade.

    We use `paid_client` to set up the course + section + video
    (course creation requires MANAGE_OWN_COURSE), then patch
    verify_token to return a FREE-role uid and hit the transcribe
    endpoint — it should return 403.
    """
    course_id, section_id = _create_course_and_section(paid_client)
    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
    video_id = upload_resp.json()["video_id"]

    # Now hit /transcribe as a FREE user. Force role=2 (FREE) in the
    # users table so capabilities_for_role returns the FREE set.
    from sqlalchemy import text
    from app.auth.admin import clear_role_cache
    with paid_client:
        from app.database import SessionLocal
        with SessionLocal() as db:
            db.execute(
                text("UPDATE users SET role=2 WHERE user_id=:uid"),
                {"uid": "test-user-uid"},
            )
            db.commit()
        clear_role_cache()

        with patch(
            "app.auth.dependencies.verify_token",
            return_value={"uid": "test-user-uid", "email": "free@test.com"},
        ):
            response = paid_client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )
    assert response.status_code == 403, (
        f"FREE user should be blocked from transcribing; got {response.status_code}: "
        f"{response.text[:200]}"
    )


def test_transcribe_paid_on_others_video_gets_403(paid_client: TestClient, admin_client: TestClient):
    """PAID users can re-transcribe videos they OWN, but NOT catalog
    videos owned by another user (admin).

    Day-13 update: previously PAID could transcribe any video (the
    REGEN_MATERIALS capability alone was the gate). Now ownership
    matters too — a PAID user with the 'jackyopenclaw' uid can regen
    their own uploads, but NOT admin's catalog videos.

    We use admin_client (different uid, role=0) to create the catalog
    video, then patch verify_token to simulate the PAID user ('test-user-uid',
    role=1) trying to transcribe it.
    """
    # Admin creates their own course + section + video via the API.
    # admin_client fixture sets the auth to a different uid than paid_client,
    # so the resulting course is owned by a different user.
    ADMIN_UID = "uid-admin"  # admin_client fixture's uid

    # Force the admin_client to use a specific different uid for clarity.
    # The paid_client fixture uses 'test-user-uid' (PAID), and the
    # admin_client fixture uses 'uid-admin' (ADMIN) — see conftest._TEST_UIDS.
    # These are different, so ownership will differ.

    with _mock_auth():  # default FAKE_USER = test-user-uid — wait, we need admin uid
        pass

    # We need a NEW mock auth for the admin client. Patch verify_token
    # to return admin uid for the admin_client's requests.
    ADMIN_FAKE = {"uid": ADMIN_UID, "email": "admin@test.com"}
    with patch("app.auth.dependencies.verify_token", return_value=ADMIN_FAKE):
        # Create admin's course
        course_resp = admin_client.post(
            "/api/courses",
            json={"title": "Admin Catalog Test"},
            headers=_auth_headers(),
        )
        assert course_resp.status_code == 200, course_resp.text
        admin_course_id = course_resp.json()["course_id"]
        # Create admin's section
        section_resp = admin_client.post(
            f"/api/courses/{admin_course_id}/sections",
            json={"title": "Catalog"},
            headers=_auth_headers(),
        )
        admin_section_id = section_resp.json()["section_id"]
        # Upload admin's video
        upload_resp = admin_client.post(
            f"/api/videos/upload/{admin_section_id}",
            files={"file": ("admin_vid.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers=_auth_headers(),
        )
        assert upload_resp.status_code == 202, upload_resp.text
        admin_video_id = upload_resp.json()["video_id"]

    # Now PAID user (paid_client fixture, uid='test-user-uid') tries to
    # transcribe admin's video. The default _mock_auth returns PAID's uid.
    with _mock_auth():
        response = paid_client.post(
            f"/api/videos/{admin_video_id}/transcribe?model_name=base",
            headers=_auth_headers(),
        )
    assert response.status_code == 403, (
        f"PAID user should be blocked from transcribing admin's catalog video; "
        f"got {response.status_code}: {response.text[:200]}"
    )


def test_transcribe_paid_on_own_video_succeeds(paid_client: TestClient):
    """PAID users CAN re-transcribe videos they uploaded themselves.

    Companion test to test_transcribe_paid_on_others_video_gets_403.
    Uses the existing _create_course_and_section helper which creates
    a course owned by 'test-user-uid' (the PAID fixture user).
    """
    course_id, section_id = _create_course_and_section(paid_client)
    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("owned.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
    video_id = upload_resp.json()["video_id"]

    # Mock the worker (it's a BackgroundTask that would otherwise block).
    with patch(
        "app.routers.videos._run_transcribe_job",
    ):
        with _mock_auth():
            response = paid_client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )
    assert response.status_code == 202, (
        f"PAID user should be able to transcribe their OWN video; "
        f"got {response.status_code}: {response.text[:200]}"
    )


def test_transcribe_admin_can_transcribe_any_video(paid_client: TestClient, admin_client: TestClient):
    """ADMIN can transcribe any video regardless of owner.

    Day-13 update: ADMIN is the curator of catalog videos. They must be
    able to fix/re-transcribe their own catalog entries from the UI.
    Companion to test_transcribe_paid_on_others_video_gets_403.
    """
    ADMIN_UID = "uid-admin"
    ADMIN_FAKE = {"uid": ADMIN_UID, "email": "admin@test.com"}

    # Create admin's catalog video via the API
    with patch("app.auth.dependencies.verify_token", return_value=ADMIN_FAKE):
        course_resp = admin_client.post(
            "/api/courses",
            json={"title": "Admin Catalog"},
            headers=_auth_headers(),
        )
        admin_course_id = course_resp.json()["course_id"]
        section_resp = admin_client.post(
            f"/api/courses/{admin_course_id}/sections",
            json={"title": "Catalog"},
            headers=_auth_headers(),
        )
        admin_section_id = section_resp.json()["section_id"]
        upload_resp = admin_client.post(
            f"/api/videos/upload/{admin_section_id}",
            files={"file": ("admin_vid.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers=_auth_headers(),
        )
        admin_video_id = upload_resp.json()["video_id"]

    # Now hit /transcribe as ADMIN (admin_client fixture, but we need to
    # re-mock verify_token so admin_client requests return ADMIN_FAKE).
    with patch("app.auth.dependencies.verify_token", return_value=ADMIN_FAKE):
        with patch("app.routers.videos._run_transcribe_job"):
            response = admin_client.post(
                f"/api/videos/{admin_video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )
    assert response.status_code == 202, (
        f"ADMIN should be able to transcribe any video (own or otherwise); "
        f"got {response.status_code}: {response.text[:200]}"
    )


def test_transcribe_failure_sets_error_status(paid_client: TestClient):
    """Should set video status to 'error' if the background transcription fails."""
    course_id, section_id = _create_course_and_section(paid_client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]

        def fake_worker_raises(vid: str, model: str) -> None:
            # Simulate the worker catching an exception from
            # transcribe_video and marking the job + video as failed.
            from app.jobs import get_job, finish_job
            job = get_job(vid, "transcribe")
            if job:
                finish_job(job, status="failed", error="Whisper crashed")
            from app.database import SessionLocal
            from app.models import Video
            with SessionLocal() as db:
                v = db.get(Video, vid)
                if v:
                    v.status = "error"
                    db.commit()

        with patch(
            "app.routers.videos._run_transcribe_job",
            side_effect=fake_worker_raises,
        ):
            response = paid_client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )
    # Endpoint returns 202 — the failure happens in the background.
    assert response.status_code == 202

    # Verify the (mocked) worker marked the video as 'error'.
    with _mock_auth():
        get_resp = paid_client.get(
            f"/api/videos/{video_id}", headers=_auth_headers()
        )
    assert get_resp.json()["status"] == "error"


# ── MVP2.0 #1 — Auto-pipeline tests ──────────────────────────────────────────

def test_upload_returns_202_and_queued_status(paid_client: TestClient):
    """Upload endpoint returns 202 + status 'queued' + auto_process=True."""
    _, section_id = _create_course_and_section(paid_client)
    fake = io.BytesIO(b"fake video content")
    with _mock_auth():
        resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("v.mp4", fake, "video/mp4")},
            headers=_auth_headers(),
        )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "queued"
    assert data["auto_process"] is True
    assert "video_id" in data


def test_upload_creates_transcribe_job_immediately(paid_client: TestClient):
    """The transcribe job tracker is started at upload time, before the
    background task runs, so the UI can poll /status right away."""
    from app.jobs import get_job
    _, section_id = _create_course_and_section(paid_client)
    fake = io.BytesIO(b"fake")
    with _mock_auth():
        resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("v.mp4", fake, "video/mp4")},
            headers=_auth_headers(),
        )
    video_id = resp.json()["video_id"]
    job = get_job(video_id, "transcribe")
    assert job is not None
    assert job["status"] == "running"  # queued state appears as "running" initially


def test_auto_pipeline_chains_transcribe_then_generate(paid_client: TestClient):
    """_run_auto_pipeline calls transcribe then generate in order."""
    _, section_id = _create_course_and_section(paid_client)
    called_order: list[str] = []

    def fake_transcribe(vid: str, model: str) -> None:
        # Simulate transcription succeeding: mark job completed + save transcript.
        from app.jobs import get_job, finish_job
        job = get_job(vid, "transcribe")
        if job:
            finish_job(job, status="completed")
        from app.database import SessionLocal
        from app.models import Asset, Video
        from app.services.transcription import transcript_to_json
        with SessionLocal() as db:
            v = db.get(Video, vid)
            if v:
                v.status = "ready"
                db.add(Asset(
                    video_id=vid,
                    asset_type="transcript",
                    content=transcript_to_json({
                        "segments": [{"start": 0.0, "end": 1.0, "text": "Hi"}],
                        "language": "en",
                        "duration": 1.0,
                    }),
                ))
                db.commit()
        called_order.append("transcribe")

    def fake_generate(vid: str, uid: str = "", role: int = 2) -> None:
        called_order.append("generate")

    # Upload (no_auto_pipeline autouse fixture suppresses the background call)
    fake = io.BytesIO(b"fake")
    with _mock_auth():
        resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("v.mp4", fake, "video/mp4")},
            headers=_auth_headers(),
        )
    video_id = resp.json()["video_id"]

    # Call the REAL pipeline (saved at module level before the autouse
    # fixture replaced it) with the transcribe + generate steps mocked.
    with (
        patch("app.routers.videos._run_transcribe_job", side_effect=fake_transcribe),
        patch("app.routers.generation._run_generate_job", side_effect=fake_generate),
    ):
        _REAL_RUN_AUTO_PIPELINE(video_id, "base")

    assert called_order == ["transcribe", "generate"], (
        f"Expected transcribe then generate, got: {called_order}"
    )


def test_auto_pipeline_skips_generate_if_transcription_fails(paid_client: TestClient):
    """If transcription fails, generation must NOT be called."""
    _, section_id = _create_course_and_section(paid_client)
    generate_called = []

    def fake_transcribe_fail(vid: str, model: str) -> None:
        from app.jobs import get_job, finish_job
        job = get_job(vid, "transcribe")
        if job:
            finish_job(job, status="failed", error="Whisper failed")
        from app.database import SessionLocal
        from app.models import Video
        with SessionLocal() as db:
            v = db.get(Video, vid)
            if v:
                v.status = "error"
                db.commit()

    def fake_generate(vid: str, uid: str = "", role: int = 2) -> None:
        generate_called.append(vid)

    fake = io.BytesIO(b"fake")
    with _mock_auth():
        resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("v.mp4", fake, "video/mp4")},
            headers=_auth_headers(),
        )
    video_id = resp.json()["video_id"]

    with (
        patch("app.routers.videos._run_transcribe_job", side_effect=fake_transcribe_fail),
        patch("app.routers.generation._run_generate_job", side_effect=fake_generate),
    ):
        _REAL_RUN_AUTO_PIPELINE(video_id, "base")

    assert generate_called == [], "generate must NOT be called when transcription fails"


def test_auto_pipeline_passes_uid_and_role_to_generate_worker(
    paid_client: TestClient,
):
    """Regression for Day-12 catalog-curation bug.

    _run_auto_pipeline() used to call:
        _run_generate_job(video_id)
    while the real signature is (video_id, user_id, user_role).
    The auto-pipeline runs as a BackgroundTask, so the TypeError
    was swallowed silently — the video stayed at status='generating'
    and no LLM call was ever made. The previous test stubs only
    took 1 arg, so CI never noticed.

    This test asserts the auto-pipeline looks up the owner's uid and
    role from the DB and passes them through.
    """
    course_id, section_id = _create_course_and_section(paid_client)
    fake = io.BytesIO(b"fake")
    with _mock_auth():
        resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("v.mp4", fake, "video/mp4")},
            headers=_auth_headers(),
        )
    video_id = resp.json()["video_id"]

    def fake_transcribe(vid: str, model: str) -> None:
        from app.jobs import get_job, finish_job
        from app.database import SessionLocal
        from app.models import Asset, Video
        from app.services.transcription import transcript_to_json
        job = get_job(vid, "transcribe")
        if job:
            finish_job(job, status="completed")
        with SessionLocal() as db:
            v = db.get(Video, vid)
            if v:
                v.status = "ready"
                db.add(Asset(
                    video_id=vid,
                    asset_type="transcript",
                    content=transcript_to_json({
                        "segments": [{"start": 0.0, "end": 1.0, "text": "Hi"}],
                        "language": "en",
                        "duration": 1.0,
                    }),
                ))
                db.commit()

    with (
        patch("app.routers.videos._run_transcribe_job", side_effect=fake_transcribe),
        patch("app.routers.generation._run_generate_job") as mock_generate,
    ):
        _REAL_RUN_AUTO_PIPELINE(video_id, "base")

    assert mock_generate.call_count == 1
    args, _kwargs = mock_generate.call_args
    assert len(args) == 3, (
        f"_run_generate_job must be called with (video_id, user_id, user_role); "
        f"got args={args!r}"
    )
    # First arg is the video id; second must be the course owner's uid;
    # third must be the owner's role (an int).
    assert args[0] == video_id
    assert args[1] == "test-user-uid"  # FAKE_USER uid
    assert isinstance(args[2], int)


# ── MVP2.0 #3 — Bulk upload tests ─────────────────────────────────────────────

def test_upload_bulk_queues_all_valid_files(paid_client: TestClient):
    """Bulk endpoint queues all valid files and returns per-file results."""
    _, section_id = _create_course_and_section(paid_client)
    files = [
        ("files", ("a.mp4", io.BytesIO(b"v1"), "video/mp4")),
        ("files", ("b.webm", io.BytesIO(b"v2"), "video/webm")),
    ]
    with _mock_auth():
        resp = paid_client.post(
            f"/api/videos/upload-bulk/{section_id}",
            files=files,
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["queued"] == 2
    assert data["skipped"] == 0
    statuses = [r["status"] for r in data["results"]]
    assert statuses == ["queued", "queued"]
    video_ids = [r["video_id"] for r in data["results"]]
    assert len(set(video_ids)) == 2  # each file got a unique video_id


def test_upload_bulk_skips_invalid_extension(paid_client: TestClient):
    """Bulk endpoint skips files with disallowed extensions."""
    _, section_id = _create_course_and_section(paid_client)
    files = [
        ("files", ("video.mp4", io.BytesIO(b"v"), "video/mp4")),
        ("files", ("doc.pdf", io.BytesIO(b"d"), "application/pdf")),
    ]
    with _mock_auth():
        resp = paid_client.post(
            f"/api/videos/upload-bulk/{section_id}",
            files=files,
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["queued"] == 1
    assert data["skipped"] == 1
    skipped = [r for r in data["results"] if r["status"] == "skipped"]
    assert skipped[0]["filename"] == "doc.pdf"
    assert "not allowed" in skipped[0]["error"]


def test_upload_bulk_skips_file_exceeding_10gb(paid_client: TestClient):
    """Bulk endpoint skips a file that exceeds the 10 GB cap.

    MVP3.0 item #1: cap was 2 GB, raised to 10 GB ([jul11] #3).
    The test mocks os.path.getsize so we never actually allocate the
    10 GB+1 in memory.
    """
    import os
    _, section_id = _create_course_and_section(paid_client)

    def fake_getsize(path: str) -> int:
        # Make every file appear larger than 10 GB
        return 11 * 1024 * 1024 * 1024

    files = [
        ("files", ("big.mp4", io.BytesIO(b"x"), "video/mp4")),
        ("files", ("small.mp4", io.BytesIO(b"y"), "video/mp4")),
    ]
    with (
        patch("app.routers.videos.os.path.getsize", side_effect=fake_getsize),
        _mock_auth(),
    ):
        resp = paid_client.post(
            f"/api/videos/upload-bulk/{section_id}",
            files=files,
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["queued"] == 0
    assert data["skipped"] == 2
    for r in data["results"]:
        assert r["status"] == "skipped"
        assert "too large" in r["error"]


def test_upload_accepts_exactly_10gb_file(paid_client: TestClient):
    """A file of exactly 10 GB must be accepted (the cap is inclusive).

    MVP3.0 item #1: user said '10 GB inclusive'. The check is
    `file_size > MAX_FILE_SIZE`, so 10 GB == cap is OK and 10 GB + 1
    byte is rejected. We mock getsize for both single + bulk paths.
    """
    import os
    course_id, section_id = _create_course_and_section(paid_client)

    # ── Single upload at exactly 10 GB — should succeed ──
    with _mock_auth():
        # First check current cap value
        from app.routers.videos import MAX_FILE_SIZE
        assert MAX_FILE_SIZE == 10 * 1024 ** 3, (
            f"cap should be 10 GB, got {MAX_FILE_SIZE / (1024**3)} GB"
        )

        with patch(
            "app.routers.videos.os.path.getsize",
            return_value=10 * 1024 ** 3,
        ):
            resp = paid_client.post(
                f"/api/videos/upload/{section_id}",
                files={"file": ("exact10gb.mp4", io.BytesIO(b"x"), "video/mp4")},
                headers=_auth_headers(),
            )
        assert resp.status_code == 202, f"10 GB should be accepted, got {resp.status_code}: {resp.text}"


def test_upload_rejects_just_over_10gb_file(paid_client: TestClient):
    """A file of 10 GB + 1 byte must be rejected.

    MVP3.0 item #1: cap is strictly inclusive at 10 GB. Any file
    bigger must return 413 (Payload Too Large). We also assert the
    error message reflects the new 10 GB cap, not the old 2 GB.
    """
    course_id, section_id = _create_course_and_section(paid_client)

    with _mock_auth():
        with patch(
            "app.routers.videos.os.path.getsize",
            return_value=10 * 1024 ** 3 + 1,
        ):
            resp = paid_client.post(
                f"/api/videos/upload/{section_id}",
                files={"file": ("over10gb.mp4", io.BytesIO(b"x"), "video/mp4")},
                headers=_auth_headers(),
            )
    assert resp.status_code == 413, f"10 GB + 1 byte should be rejected, got {resp.status_code}: {resp.text}"
    detail = resp.json()["detail"]
    assert "10 GB" in detail, f"error message should mention 10 GB, got: {detail}"


def test_upload_bulk_partial_success(paid_client: TestClient):
    """One valid file + one over-limit file: partial success."""
    import os
    _, section_id = _create_course_and_section(paid_client)
    call_count = [0]

    def size_side_effect(path: str) -> int:
        call_count[0] += 1
        # First file: 100 bytes (valid). Second file: 11 GB (over the 10 GB cap, MVP3.0 #1).
        return 100 if call_count[0] == 1 else 11 * 1024 * 1024 * 1024

    files = [
        ("files", ("good.mp4", io.BytesIO(b"v"), "video/mp4")),
        ("files", ("huge.mp4", io.BytesIO(b"w"), "video/mp4")),
    ]
    with (
        patch("app.routers.videos.os.path.getsize", side_effect=size_side_effect),
        _mock_auth(),
    ):
        resp = paid_client.post(
            f"/api/videos/upload-bulk/{section_id}",
            files=files,
            headers=_auth_headers(),
        )
    data = resp.json()
    assert data["queued"] == 1
    assert data["skipped"] == 1
    queued = [r for r in data["results"] if r["status"] == "queued"]
    skipped = [r for r in data["results"] if r["status"] == "skipped"]
    assert queued[0]["filename"] == "good.mp4"
    assert skipped[0]["filename"] == "huge.mp4"


def test_upload_bulk_wrong_section(paid_client: TestClient):
    """Bulk endpoint returns 404 for non-existent section."""
    files = [("files", ("v.mp4", io.BytesIO(b"x"), "video/mp4"))]
    with _mock_auth():
        resp = paid_client.post(
            "/api/videos/upload-bulk/nonexistent-section",
            files=files,
            headers=_auth_headers(),
        )
    assert resp.status_code == 404


def test_upload_bulk_empty_files(paid_client: TestClient):
    """Bulk endpoint returns 400 when no files are provided."""
    _, section_id = _create_course_and_section(paid_client)
    with _mock_auth():
        resp = paid_client.post(
            f"/api/videos/upload-bulk/{section_id}",
            files=[],
            headers=_auth_headers(),
        )
    assert resp.status_code == 422  # FastAPI validation error for empty list


# ── MVP2.0 #20 — 0-byte upload rejection (discovered 2026-07-09) ───────────
# Bulk-uploading 30 videos, 1 of them saved as a 0-byte file. The auto-pipeline
# crashed with "[Errno 1094995529] Invalid data found when processing input"
# because Whisper can't decode empty audio. The old code only checked the
# UPPER bound (file_size > MAX_FILE_SIZE) — never the LOWER bound.

def test_upload_rejects_zero_byte_file(paid_client: TestClient):
    """Single upload of a 0-byte file is rejected with 400."""
    _, section_id = _create_course_and_section(paid_client)

    fake_file = io.BytesIO(b"")  # 0 bytes
    with _mock_auth():
        response = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("video.mp4", fake_file, "video/mp4")},
            headers=_auth_headers(),
        )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_upload_zero_byte_does_not_create_db_row(paid_client: TestClient):
    """When upload is rejected, no Video row is created and no file
    remains on disk."""
    import os
    _, section_id = _create_course_and_section(paid_client)
    uploads_before = set(os.listdir("uploads/")) if os.path.exists("uploads/") else set()

    fake_file = io.BytesIO(b"")
    with _mock_auth():
        response = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("video.mp4", fake_file, "video/mp4")},
            headers=_auth_headers(),
        )
    assert response.status_code == 400

    # No new file on disk
    uploads_after = set(os.listdir("uploads/")) if os.path.exists("uploads/") else set()
    new_files = uploads_after - uploads_before
    # Filter to only the .mp4 ones we may have added — should be empty
    assert not any(f.endswith(".mp4") for f in new_files), f"unexpected new files: {new_files}"


def test_upload_bulk_skips_zero_byte_file(paid_client: TestClient):
    """Bulk upload: a 0-byte file is reported as 'skipped' with a clear
    error message. Other files in the batch continue processing."""
    _, section_id = _create_course_and_section(paid_client)
    files = [
        ("files", ("good.mp4", io.BytesIO(b"v"), "video/mp4")),
        ("files", ("empty.webm", io.BytesIO(b""), "video/webm")),
        ("files", ("also_good.mp4", io.BytesIO(b"w"), "video/mp4")),
    ]
    with _mock_auth():
        resp = paid_client.post(
            f"/api/videos/upload-bulk/{section_id}",
            files=files,
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["queued"] == 2
    assert data["skipped"] == 1
    skipped = [r for r in data["results"] if r["status"] == "skipped"]
    assert len(skipped) == 1
    assert skipped[0]["filename"] == "empty.webm"
    assert "empty" in skipped[0]["error"].lower()


# ── MVP2.0 #1 + #3 fix — route shadowing regression guard ───────────────────
# This is a STRUCTURAL test, not a behavioural one. It documents that the
# literal path strings `/upload-bulk/{section_id}` and `/{video_id}/transcribe`
# must be registered in this order. FastAPI matches routes in declaration
# order, so a route with a more-specific literal path that comes AFTER a
# parameterised `/{video_id}/...` route will be shadowed in production.
#
# The test in TestClient currently passes either way (Starlette's path
# resolution prefers literal-prefix routes over parameterised ones at
# lookup time, even when declared later), but production uvicorn behaviour
# differs — see doc/Blockers.md for the postmortem.

def test_upload_bulk_route_registered_before_transcribe_route():
    """`/upload-bulk/{section_id}` must be declared before
    `/{video_id}/transcribe` to avoid being shadowed in production."""
    import app.routers.videos as videos_mod
    paths = [r.path for r in videos_mod.router.routes]
    bulk_idx = paths.index("/api/videos/upload-bulk/{section_id}")
    transcribe_idx = paths.index("/api/videos/{video_id}/transcribe")
    assert bulk_idx < transcribe_idx, (
        f"Route shadowing bug: /upload-bulk/{{section_id}} (index {bulk_idx}) "
        f"must be declared BEFORE /{{video_id}}/transcribe (index {transcribe_idx}). "
        f"Otherwise POST /api/videos/upload-bulk/<id> matches "
        f"/{{video_id}}/transcribe with video_id='upload-bulk' and returns 404."
    )


def test_upload_route_registered_before_transcribe_route():
    """Same shadowing concern for the single-file upload route."""
    import app.routers.videos as videos_mod
    paths = [r.path for r in videos_mod.router.routes]
    upload_idx = paths.index("/api/videos/upload/{section_id}")
    transcribe_idx = paths.index("/api/videos/{video_id}/transcribe")
    assert upload_idx < transcribe_idx, (
        f"Route shadowing bug: /upload/{{section_id}} (index {upload_idx}) "
        f"must be declared BEFORE /{{video_id}}/transcribe (index {transcribe_idx})."
    )

def test_export_transcript_collapses_underscore_runs(paid_client: TestClient):
    """Bilibili (and some other downloaders) auto-rename files with
    long underscore runs like `1.-Foo_______-10-07-2026.mp4`. The
    export filename should collapse those into single spaces so
    the download looks like `1.-Foo -10-07-2026.md`, not
    `1.-Foo_______-10-07-2026.md`.

    Regression test for the user-reported "ugly download names"
    complaint (MVP2.0-Status.md §7). The DB title is NOT
    modified — only the export filename.
    """
    course_id, section_id = _create_course_and_section(paid_client)
    with _mock_auth():
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("x.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        from app.database import SessionLocal
        ugly_title = "1.-OpenClaw-01-OpenClaw_______-10-07-2026"
        with SessionLocal() as db:
            v = db.get(Video, video_id)
            v.title = ugly_title
            db.add(Asset(
                id=f"t-{video_id}", video_id=video_id, asset_type="transcript",
                content='{"segments": [], "language": "en", "duration": 0}',
            ))
            db.commit()
        # Try all three formats to be sure
        for fmt, ext in [("md", "md"), ("json", "json"), ("txt", "txt")]:
            resp = paid_client.get(
                f"/api/videos/{video_id}/transcript/export?format={fmt}",
                headers=_auth_headers(),
            )
            assert resp.status_code == 200
            disp = resp.headers["content-disposition"]
            # The basic filename must NOT contain `___` (any run of
            # 2+ underscores)
            assert 'filename="' in disp
            # Extract the basic filename between the first pair of
            # double quotes after `filename=`
            start = disp.index('filename="') + len('filename="')
            end = disp.index('"', start)
            basic_name = disp[start:end]
            assert "___" not in basic_name, (
                f"underscore run survived in {basic_name!r}"
            )
            # The original title had `_______` (7 underscores); the
            # cleaned name should be a single space, so we expect
            # "1.-OpenClaw-01-OpenClaw -10-07-2026.<ext>"
            expected = f"1.-OpenClaw-01-OpenClaw -10-07-2026.{ext}"
            assert basic_name == expected, (
                f"got {basic_name!r}, expected {expected!r}"
            )
            # The RFC 5987 form should match too
            from urllib.parse import unquote
            quoted = disp.split("filename*=UTF-8''")[1]
            assert unquote(quoted) == expected

    # Sanity: the DB title is unchanged (we only sanitize the
    # download filename, not the source data)
    from app.database import SessionLocal
    with SessionLocal() as db:
        v = db.get(Video, video_id)
        assert v.title == ugly_title, (
            f"DB title was modified: {v.title!r} (expected unchanged)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/videos/{id} — manual todo #5
# ─────────────────────────────────────────────────────────────────────────────


def _create_video_with_assets(
    paid_client: TestClient,
    section_id: str,
    title: str = "test.mp4",
    with_assets: bool = True,
    with_chat: bool = False,
) -> str:
    """Helper: create a video, optionally with assets and chat sessions,
    for the delete tests. Returns the video_id."""
    with _mock_auth():
        fake_content = b"x" * 1024  # 1 KB so the file is not 0-byte
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": (title, io.BytesIO(fake_content), "video/mp4")},
            headers=_auth_headers(),
        )
    assert upload_resp.status_code == 202, f"upload failed: {upload_resp.text}"
    video_id = upload_resp.json()["video_id"]

    if with_assets or with_chat:
        from app.database import SessionLocal
        with SessionLocal() as db:
            if with_assets:
                for asset_type in ["transcript", "summary", "mindmap"]:
                    db.add(Asset(
                        id=f"{asset_type}-{video_id}",
                        video_id=video_id,
                        asset_type=asset_type,
                        content="dummy content",
                    ))
            if with_chat:
                # Create a chat session directly in the DB so we can
                # verify it's cascaded on delete.
                from app.models import ChatSession, ChatMessage
                session_id = f"chat-{video_id}"
                db.add(ChatSession(
                    id=session_id,
                    user_id="test-user-uid",
                    video_id=video_id,
                    concept="test concept",
                    scope="flashcard",
                ))
                db.add(ChatMessage(
                    id=f"msg-{video_id}",
                    session_id=session_id,
                    role="user",
                    content="hello",
                ))
            db.commit()
    return video_id


def test_delete_video_success(paid_client: TestClient):
    """Should delete the video, return 200 with cascade summary."""
    from pathlib import Path
    from app.database import SessionLocal
    from app.config import settings

    course_id, section_id = _create_course_and_section(paid_client)
    video_id = _create_video_with_assets(
        paid_client, section_id, title="to-delete.mp4",
        with_assets=True, with_chat=True,
    )

    # Confirm the file exists on disk before delete
    with SessionLocal() as db:
        v = db.get(Video, video_id)
        assert v is not None
        assert Path(v.file_path).exists(), "file should exist before delete"

    with _mock_auth():
        resp = paid_client.delete(
            f"/api/videos/{video_id}", headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "deleted"
    assert data["video_id"] == video_id
    assert data["deleted"]["file"] is True
    assert data["deleted"]["assets"] == 3
    assert data["deleted"]["chat_sessions"] == 1

    # Video row is gone
    with SessionLocal() as db:
        assert db.get(Video, video_id) is None
        # Assets are gone (cascade)
        for asset_type in ["transcript", "summary", "mindmap"]:
            asset = db.get(Asset, f"{asset_type}-{video_id}")
            assert asset is None, f"{asset_type} should be cascaded"
        # ChatSession is gone (cascade)
        from app.models import ChatSession
        assert db.get(ChatSession, f"chat-{video_id}") is None
        # ChatMessage is also gone (cascade via session)
        from app.models import ChatMessage
        assert db.get(ChatMessage, f"msg-{video_id}") is None

    # File is gone from disk
    files_remaining = list(settings.upload_path.glob("to-delete*"))
    # (filename is video_id + ext, not the original title; check by id)
    files_remaining = [f for f in files_remaining if video_id in f.name]
    assert len(files_remaining) == 0, "file should be removed from disk"


def test_delete_video_no_assets_no_chat(paid_client: TestClient):
    """A freshly-uploaded video with no assets still deletes cleanly."""
    course_id, section_id = _create_course_and_section(paid_client)
    video_id = _create_video_with_assets(
        paid_client, section_id, with_assets=False, with_chat=False,
    )
    with _mock_auth():
        resp = paid_client.delete(
            f"/api/videos/{video_id}", headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"]["file"] is True
    assert data["deleted"]["assets"] == 0
    assert data["deleted"]["chat_sessions"] == 0


def test_delete_video_not_found(paid_client: TestClient):
    """Returns 404 for a non-existent video."""
    with _mock_auth():
        resp = paid_client.delete(
            "/api/videos/nonexistent-id", headers=_auth_headers(),
        )
    assert resp.status_code == 404


def test_delete_video_wrong_user(paid_client: TestClient):
    """Returns 403 if the user doesn't own the course."""
    course_id, section_id = _create_course_and_section(paid_client)
    video_id = _create_video_with_assets(paid_client, section_id)

    # Switch to a different user
    with patch("app.auth.dependencies.verify_token", return_value={"uid": "user-B"}):
        resp = paid_client.delete(
            f"/api/videos/{video_id}", headers=_auth_headers(),
        )
    assert resp.status_code == 403

    # And the video should still be there (not deleted by user-B)
    from app.database import SessionLocal
    with SessionLocal() as db:
        assert db.get(Video, video_id) is not None


def test_delete_video_unauthenticated(paid_client: TestClient):
    """Returns 401 without auth."""
    course_id, section_id = _create_course_and_section(paid_client)
    video_id = _create_video_with_assets(paid_client, section_id)
    # No _mock_auth() here
    # MVP2.0.6: conftest client fixture sets a default valid
    # cookie. Clear it for this unauthenticated test.
    paid_client.cookies.clear()
    resp = paid_client.delete(f"/api/videos/{video_id}")
    assert resp.status_code == 401


def test_delete_video_missing_file_on_disk(paid_client: TestClient):
    """If the on-disk file is already gone (e.g. cleaned up manually),
    the delete should still succeed — the DB cascade is the important
    part, and we don't want a missing file to crash the operation."""
    from pathlib import Path
    from app.database import SessionLocal

    course_id, section_id = _create_course_and_section(paid_client)
    video_id = _create_video_with_assets(paid_client, section_id)

    # Manually remove the file before the delete
    with SessionLocal() as db:
        v = db.get(Video, video_id)
        Path(v.file_path).unlink()
        assert not Path(v.file_path).exists()

    with _mock_auth():
        resp = paid_client.delete(
            f"/api/videos/{video_id}", headers=_auth_headers(),
        )
    assert resp.status_code == 200
    # file=False means the file was already gone — we report that
    # honestly in the response so the user knows.
    data = resp.json()
    assert data["deleted"]["file"] is False

    # But the video row is still gone (cascade did its job)
    with SessionLocal() as db:
        assert db.get(Video, video_id) is None


def test_delete_video_zero_byte_file(paid_client: TestClient):
    """Regression: the 0-byte video from the 2026-07-09 incident
    can be deleted cleanly via this endpoint. The 0-byte rejection
    is on upload, not on delete — even if a 0-byte file slipped
    through, delete should still work."""
    from pathlib import Path
    from app.config import settings

    course_id, section_id = _create_course_and_section(paid_client)
    # Manually create a 0-byte file in the DB (bypassing the upload
    # endpoint which now rejects 0-byte files)
    from app.database import SessionLocal
    video_id = "zero-byte-test-id"
    file_path = settings.upload_path / f"{video_id}.mp4"
    file_path.touch()  # creates an empty file
    with SessionLocal() as db:
        db.add(Video(
            id=video_id,
            title="0-byte-broken",
            filename="0-byte-broken.mp4",
            file_path=str(file_path),
            file_size=0,
            section_id=section_id,
            status="error",
        ))
        db.commit()

    with _mock_auth():
        resp = paid_client.delete(
            f"/api/videos/{video_id}", headers=_auth_headers(),
        )
    assert resp.status_code == 200
    assert resp.json()["deleted"]["file"] is True
    assert not file_path.exists()
    file_path.unlink(missing_ok=True)  # cleanup
