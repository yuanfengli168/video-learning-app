"""Tests for videos router — upload, transcribe, get."""

import io
from unittest.mock import patch

from fastapi.testclient import TestClient

# Save a reference to the real _run_auto_pipeline BEFORE the no_auto_pipeline
# autouse fixture (conftest.py) replaces it. Module-level code runs at import
# time, which is before any pytest fixtures are set up.
import app.routers.videos as _videos_module
_REAL_RUN_AUTO_PIPELINE = _videos_module._run_auto_pipeline

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def _create_course_and_section(client: TestClient):
    """Helper: create a course and section, return (course_id, section_id)."""
    with _mock_auth():
        course_resp = client.post(
            "/api/courses",
            json={"title": "ML"},
            headers=_auth_headers(),
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
    return course_id, section_id


def test_list_whisper_models(client: TestClient):
    """/api/videos/models should return available models."""
    response = client.get("/api/videos/models")
    assert response.status_code == 200
    models = response.json()["models"]
    assert "base" in models
    assert "small" in models
    assert "medium" in models


def test_upload_video(client: TestClient):
    """Should upload a video file and queue auto-processing (MVP2.0 #1)."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        response = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("test.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
    assert response.status_code == 202
    assert "video_id" in response.json()
    assert response.json()["status"] == "queued"
    assert response.json()["auto_process"] is True


def test_upload_invalid_extension(client: TestClient):
    """Should reject non-video files."""
    _, section_id = _create_course_and_section(client)

    fake_file = io.BytesIO(b"text content")
    with _mock_auth():
        response = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("document.txt", fake_file, "text/plain")},
            headers=_auth_headers(),
        )
    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"]


def test_upload_to_nonexistent_section(client: TestClient):
    """Should return 404 for non-existent section."""
    fake_video = io.BytesIO(b"fake")
    with _mock_auth():
        response = client.post(
            "/api/videos/upload/nonexistent-section",
            files={"file": ("test.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
    assert response.status_code == 404


def test_get_video(client: TestClient):
    """Should get video details."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(
            f"/api/videos/{video_id}", headers=_auth_headers()
        )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "lecture"
    assert data["status"] == "queued"  # MVP2.0 #1: auto-pipeline queued on upload
    assert data["has_transcript"] is False


def test_get_video_not_found(client: TestClient):
    """Should return 404 for non-existent video."""
    with _mock_auth():
        response = client.get(
            "/api/videos/nonexistent", headers=_auth_headers()
        )
    assert response.status_code == 404


def test_transcribe_video(client: TestClient):
    """Should kick off transcription and return 202 Accepted with a job."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = client.post(
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
            response = client.post(
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
        get_resp = client.get(
            f"/api/videos/{video_id}/transcript", headers=_auth_headers()
        )
    assert get_resp.status_code == 200
    segs = get_resp.json()["segments"]
    assert len(segs) == 1
    assert segs[0]["text"] == "Hello"
    assert get_resp.json()["language"] == "en"


def test_get_transcript(client: TestClient):
    """Should get the transcript after the (mocked) transcribe worker ran."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = client.post(
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
            client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )

        # Now get the transcript
        response = client.get(
            f"/api/videos/{video_id}/transcript", headers=_auth_headers()
        )
    assert response.status_code == 200
    data = response.json()
    assert data["language"] == "en"
    assert len(data["segments"]) == 1
    assert data["segments"][0]["text"] == "Hello world"


def test_get_transcript_not_found(client: TestClient):
    """Should return 404 if no transcript exists."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(
            f"/api/videos/{video_id}/transcript", headers=_auth_headers()
        )
    assert response.status_code == 404


def test_transcribe_invalid_model(client: TestClient):
    """Should return 400 for invalid model name."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.post(
            f"/api/videos/{video_id}/transcribe?model_name=nonexistent",
            headers=_auth_headers(),
        )
    assert response.status_code == 400


def test_transcribe_failure_sets_error_status(client: TestClient):
    """Should set video status to 'error' if the background transcription fails."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = client.post(
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
            response = client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )
    # Endpoint returns 202 — the failure happens in the background.
    assert response.status_code == 202

    # Verify the (mocked) worker marked the video as 'error'.
    with _mock_auth():
        get_resp = client.get(
            f"/api/videos/{video_id}", headers=_auth_headers()
        )
    assert get_resp.json()["status"] == "error"


# ── MVP2.0 #1 — Auto-pipeline tests ──────────────────────────────────────────

def test_upload_returns_202_and_queued_status(client: TestClient):
    """Upload endpoint returns 202 + status 'queued' + auto_process=True."""
    _, section_id = _create_course_and_section(client)
    fake = io.BytesIO(b"fake video content")
    with _mock_auth():
        resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("v.mp4", fake, "video/mp4")},
            headers=_auth_headers(),
        )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "queued"
    assert data["auto_process"] is True
    assert "video_id" in data


def test_upload_creates_transcribe_job_immediately(client: TestClient):
    """The transcribe job tracker is started at upload time, before the
    background task runs, so the UI can poll /status right away."""
    from app.jobs import get_job
    _, section_id = _create_course_and_section(client)
    fake = io.BytesIO(b"fake")
    with _mock_auth():
        resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("v.mp4", fake, "video/mp4")},
            headers=_auth_headers(),
        )
    video_id = resp.json()["video_id"]
    job = get_job(video_id, "transcribe")
    assert job is not None
    assert job["status"] == "running"  # queued state appears as "running" initially


def test_auto_pipeline_chains_transcribe_then_generate(client: TestClient):
    """_run_auto_pipeline calls transcribe then generate in order."""
    _, section_id = _create_course_and_section(client)
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

    def fake_generate(vid: str) -> None:
        called_order.append("generate")

    # Upload (no_auto_pipeline autouse fixture suppresses the background call)
    fake = io.BytesIO(b"fake")
    with _mock_auth():
        resp = client.post(
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


def test_auto_pipeline_skips_generate_if_transcription_fails(client: TestClient):
    """If transcription fails, generation must NOT be called."""
    _, section_id = _create_course_and_section(client)
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

    def fake_generate(vid: str) -> None:
        generate_called.append(vid)

    fake = io.BytesIO(b"fake")
    with _mock_auth():
        resp = client.post(
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


# ── MVP2.0 #3 — Bulk upload tests ─────────────────────────────────────────────

def test_upload_bulk_queues_all_valid_files(client: TestClient):
    """Bulk endpoint queues all valid files and returns per-file results."""
    _, section_id = _create_course_and_section(client)
    files = [
        ("files", ("a.mp4", io.BytesIO(b"v1"), "video/mp4")),
        ("files", ("b.webm", io.BytesIO(b"v2"), "video/webm")),
    ]
    with _mock_auth():
        resp = client.post(
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


def test_upload_bulk_skips_invalid_extension(client: TestClient):
    """Bulk endpoint skips files with disallowed extensions."""
    _, section_id = _create_course_and_section(client)
    files = [
        ("files", ("video.mp4", io.BytesIO(b"v"), "video/mp4")),
        ("files", ("doc.pdf", io.BytesIO(b"d"), "application/pdf")),
    ]
    with _mock_auth():
        resp = client.post(
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


def test_upload_bulk_skips_file_exceeding_2gb(client: TestClient):
    """Bulk endpoint skips a file that exceeds the 2 GB cap."""
    import os
    _, section_id = _create_course_and_section(client)

    def fake_getsize(path: str) -> int:
        # Make every file appear larger than 2 GB
        return 3 * 1024 * 1024 * 1024

    files = [
        ("files", ("big.mp4", io.BytesIO(b"x"), "video/mp4")),
        ("files", ("small.mp4", io.BytesIO(b"y"), "video/mp4")),
    ]
    with (
        patch("app.routers.videos.os.path.getsize", side_effect=fake_getsize),
        _mock_auth(),
    ):
        resp = client.post(
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


def test_upload_bulk_partial_success(client: TestClient):
    """One valid file + one over-limit file: partial success."""
    import os
    _, section_id = _create_course_and_section(client)
    call_count = [0]

    def size_side_effect(path: str) -> int:
        call_count[0] += 1
        # First file: 100 bytes (valid). Second file: 3 GB (over limit).
        return 100 if call_count[0] == 1 else 3 * 1024 * 1024 * 1024

    files = [
        ("files", ("good.mp4", io.BytesIO(b"v"), "video/mp4")),
        ("files", ("huge.mp4", io.BytesIO(b"w"), "video/mp4")),
    ]
    with (
        patch("app.routers.videos.os.path.getsize", side_effect=size_side_effect),
        _mock_auth(),
    ):
        resp = client.post(
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


def test_upload_bulk_wrong_section(client: TestClient):
    """Bulk endpoint returns 404 for non-existent section."""
    files = [("files", ("v.mp4", io.BytesIO(b"x"), "video/mp4"))]
    with _mock_auth():
        resp = client.post(
            "/api/videos/upload-bulk/nonexistent-section",
            files=files,
            headers=_auth_headers(),
        )
    assert resp.status_code == 404


def test_upload_bulk_empty_files(client: TestClient):
    """Bulk endpoint returns 400 when no files are provided."""
    _, section_id = _create_course_and_section(client)
    with _mock_auth():
        resp = client.post(
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

def test_upload_rejects_zero_byte_file(client: TestClient):
    """Single upload of a 0-byte file is rejected with 400."""
    _, section_id = _create_course_and_section(client)

    fake_file = io.BytesIO(b"")  # 0 bytes
    with _mock_auth():
        response = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("video.mp4", fake_file, "video/mp4")},
            headers=_auth_headers(),
        )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_upload_zero_byte_does_not_create_db_row(client: TestClient):
    """When upload is rejected, no Video row is created and no file
    remains on disk."""
    import os
    _, section_id = _create_course_and_section(client)
    uploads_before = set(os.listdir("uploads/")) if os.path.exists("uploads/") else set()

    fake_file = io.BytesIO(b"")
    with _mock_auth():
        response = client.post(
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


def test_upload_bulk_skips_zero_byte_file(client: TestClient):
    """Bulk upload: a 0-byte file is reported as 'skipped' with a clear
    error message. Other files in the batch continue processing."""
    _, section_id = _create_course_and_section(client)
    files = [
        ("files", ("good.mp4", io.BytesIO(b"v"), "video/mp4")),
        ("files", ("empty.webm", io.BytesIO(b""), "video/webm")),
        ("files", ("also_good.mp4", io.BytesIO(b"w"), "video/mp4")),
    ]
    with _mock_auth():
        resp = client.post(
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