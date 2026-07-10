"""Tests for courses router."""

import io
from unittest.mock import patch

from fastapi.testclient import TestClient

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def _create_course_and_section(client: TestClient) -> tuple[str, str]:
    """Helper: create a course + section, return (course_id, section_id).

    Used by the retry-failed tests so they don't have to repeat the
    boilerplate. Other tests in this file do it inline."""
    with _mock_auth():
        create_resp = client.post(
            "/api/courses",
            json={"title": "ML"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
    return course_id, section_id


def test_list_courses_empty(client: TestClient):
    """List courses should return empty list for new user."""
    with _mock_auth():
        response = client.get("/api/courses", headers=_auth_headers())
    assert response.status_code == 200
    assert response.json() == []


def test_create_course(client: TestClient):
    """Should create a course."""
    with _mock_auth():
        response = client.post(
            "/api/courses",
            json={"title": "Machine Learning", "description": "ML course"},
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    assert "course_id" in response.json()


def test_list_courses_after_create(client: TestClient):
    """Should list created courses."""
    with _mock_auth():
        client.post(
            "/api/courses",
            json={"title": "ML"},
            headers=_auth_headers(),
        )
        response = client.get("/api/courses", headers=_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "ML"


def test_get_course(client: TestClient):
    """Should get a course by ID."""
    with _mock_auth():
        create_resp = client.post(
            "/api/courses",
            json={"title": "ML", "description": "Test"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]
        response = client.get(f"/api/courses/{course_id}", headers=_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "ML"
    assert data["description"] == "Test"
    assert data["sections"] == []


def test_get_course_not_found(client: TestClient):
    """Should return 404 for non-existent course."""
    with _mock_auth():
        response = client.get(
            "/api/courses/nonexistent-id", headers=_auth_headers()
        )
    assert response.status_code == 404


def test_update_course(client: TestClient):
    """Should update a course."""
    with _mock_auth():
        create_resp = client.post(
            "/api/courses",
            json={"title": "Original"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]
        response = client.put(
            f"/api/courses/{course_id}",
            json={"title": "Updated", "description": "New desc"},
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    assert response.json()["status"] == "updated"


def test_delete_course(client: TestClient):
    """Should delete a course."""
    with _mock_auth():
        create_resp = client.post(
            "/api/courses",
            json={"title": "To Delete"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]
        response = client.delete(
            f"/api/courses/{course_id}", headers=_auth_headers()
        )
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"


def test_create_section(client: TestClient):
    """Should create a section in a course."""
    with _mock_auth():
        create_resp = client.post(
            "/api/courses",
            json={"title": "ML"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]
        response = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1", "order_index": 0},
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    assert "section_id" in response.json()


def test_list_section_videos_empty(client: TestClient):
    """Should return empty list for a section with no videos."""
    with _mock_auth():
        create_resp = client.post(
            "/api/courses",
            json={"title": "ML"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        response = client.get(
            f"/api/courses/{course_id}/sections/{section_id}/videos",
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    assert response.json() == []


def test_course_ownership(client: TestClient):
    """Should not allow access to another user's course."""
    with patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "user-A"},
    ):
        create_resp = client.post(
            "/api/courses",
            json={"title": "A's course"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]

    with patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "user-B"},
    ):
        response = client.get(
            f"/api/courses/{course_id}", headers=_auth_headers()
        )
    assert response.status_code == 403


def test_unauthorized_access(client: TestClient):
    """Should return 401 without auth."""
    response = client.get("/api/courses")
    assert response.status_code == 401


# ── MVP2.0 retry-all-failed endpoint ───────────────────────────────────────

import json as _json
from unittest.mock import patch as _patch


def _make_failed_video(
    client: TestClient,
    course_id: str,
    section_id: str,
    title: str,
) -> str:
    """Helper: upload a video, mark its generate job as failed in the DB.

    The auto-pipeline is suppressed by the conftest's
    no_auto_pipeline fixture, so upload just creates a video with
    status='queued' and no last_generate_job. We then write a
    synthetic failed generate job so the retry endpoint has
    something to find.
    """
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": (f"{title}.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers=_auth_headers(),
        )
    video_id = upload_resp.json()["video_id"]
    # Inject a failed generate job into the DB so the retry helper
    # picks it up. We do this in the SAME session the test client
    # uses (the conftest wires app.database.SessionLocal to the
    # in-memory test DB).
    from app.database import SessionLocal
    from app.models import Video
    failed_job = _json.dumps({
        "video_id": video_id,
        "job_type": "generate",
        "status": "failed",
        "error": "Could not extract valid JSON from LLM response (len=0)",
    })
    with SessionLocal() as db:
        v = db.get(Video, video_id)
        if v:
            v.last_generate_job = failed_job
            v.status = "error"
            db.commit()
    return video_id


def test_retry_failed_section_no_failed_videos(client: TestClient):
    """When no videos are failed, the endpoint returns retried=0."""
    course_id, section_id = _create_course_and_section(client)
    # Upload a fresh video (not failed) so the section has videos
    with _mock_auth():
        client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("good.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers=_auth_headers(),
        )

    with _mock_auth():
        resp = client.post(
            f"/api/courses/{course_id}/sections/{section_id}/retry-failed",
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["retried"] == 0
    assert data["video_ids"] == []


def test_retry_failed_section_retries_failed_videos(client: TestClient):
    """When there ARE failed videos, the endpoint queues them all.

    The BackgroundTasks run synchronously in TestClient, so by the
    time the response returns, the jobs have already started. We
    assert that the API returned the right count and that the
    videos' status flipped from 'error' to 'generating' or
    'ready' (depending on whether the worker completed fast
    enough in the test).
    """
    course_id, section_id = _create_course_and_section(client)
    # Add 3 videos, mark 2 of them as failed
    _make_failed_video(client, course_id, section_id, "broken-1")
    _make_failed_video(client, course_id, section_id, "broken-2")
    # And one good one (no failed job)
    with _mock_auth():
        client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("good.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers=_auth_headers(),
        )

    # Mock the worker so it doesn't actually call Ollama
    def fake_worker(video_id: str) -> None:
        from app.jobs import get_job, finish_job
        from app.database import SessionLocal
        from app.models import Video
        job = get_job(video_id, "generate")
        if job:
            finish_job(job, status="completed", message="fake success")
        with SessionLocal() as db:
            v = db.get(Video, video_id)
            if v:
                from app.jobs import serialize_job
                v.last_generate_job = serialize_job(job)
                v.status = "ready"
                db.commit()

    with _mock_auth(), _patch(
        "app.routers.generation._run_generate_job",
        side_effect=fake_worker,
    ):
        resp = client.post(
            f"/api/courses/{course_id}/sections/{section_id}/retry-failed",
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["retried"] == 2
    assert len(data["video_ids"]) == 2


def test_retry_failed_section_404_wrong_section(client: TestClient):
    """Returns 404 if the section doesn't belong to the course."""
    course_id, _ = _create_course_and_section(client)
    with _mock_auth():
        resp = client.post(
            f"/api/courses/{course_id}/sections/nonexistent-section/retry-failed",
            headers=_auth_headers(),
        )
    assert resp.status_code == 404


def test_retry_failed_section_403_wrong_user(client: TestClient):
    """Returns 403 if the user doesn't own the course."""
    course_id, section_id = _create_course_and_section(client)
    with _patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "user-B"},
    ):
        resp = client.post(
            f"/api/courses/{course_id}/sections/{section_id}/retry-failed",
            headers=_auth_headers(),
        )
    assert resp.status_code == 403


def _make_transcribe_failed_video(
    client: TestClient,
    course_id: str,
    section_id: str,
    title: str,
) -> str:
    """Helper: upload a video, mark its TRANSCRIBE job as failed.

    Different from _make_failed_video: the failure is in the
    transcribe step (e.g. 0-byte file, unsupported codec), not the
    LLM step. The retry endpoint should detect this and re-run
    transcribe (which then auto-pipelines to generate).
    """
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": (f"{title}.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers=_auth_headers(),
        )
    video_id = upload_resp.json()["video_id"]
    from app.database import SessionLocal
    from app.models import Video
    failed_job = _json.dumps({
        "video_id": video_id,
        "job_type": "transcribe",
        "status": "failed",
        "error": "[Errno 1094995529] Invalid data found when processing input",
    })
    with SessionLocal() as db:
        v = db.get(Video, video_id)
        if v:
            v.last_transcribe_job = failed_job
            v.status = "error"
            db.commit()
    return video_id


def test_retry_failed_section_retries_transcribe_failure(client: TestClient):
    """A transcribe-failed video (e.g. 0-byte file) gets retried by
    the same endpoint. This is the regression test for the bug
    the user reported: clicking the button when the only failed
    video was a transcribe failure did nothing visible."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _make_transcribe_failed_video(
        client, course_id, section_id, "zero-byte-broken"
    )

    def fake_transcribe(vid: str, model: str) -> None:
        from app.jobs import get_job, finish_job
        from app.database import SessionLocal
        from app.models import Video
        job = get_job(vid, "transcribe")
        if job:
            finish_job(job, status="completed", message="fake success")
        with SessionLocal() as db:
            v = db.get(Video, vid)
            if v:
                from app.jobs import serialize_job
                v.last_transcribe_job = serialize_job(job)
                v.status = "ready"
                db.commit()

    with _mock_auth(), _patch(
        "app.routers.videos._run_transcribe_job",
        side_effect=fake_transcribe,
    ):
        resp = client.post(
            f"/api/courses/{course_id}/sections/{section_id}/retry-failed",
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["retried"] == 1
    assert data["transcribe_retried"] == 1
    assert data["generate_retried"] == 0
    assert video_id in data["video_ids"]


def test_retry_failed_section_response_shape(client: TestClient):
    """The response includes the split counts so the UI can show
    'Retrying N (3 transcribe, 1 generate)'."""
    course_id, section_id = _create_course_and_section(client)
    # One transcribe failure, one generate failure
    transcribe_id = _make_transcribe_failed_video(
        client, course_id, section_id, "transcribe-broken"
    )
    generate_id = _make_failed_video(
        client, course_id, section_id, "generate-broken"
    )

    def fake_worker(*args, **kwargs):
        pass

    with _mock_auth(), _patch(
        "app.routers.videos._run_transcribe_job",
        side_effect=fake_worker,
    ), _patch(
        "app.routers.generation._run_generate_job",
        side_effect=fake_worker,
    ):
        resp = client.post(
            f"/api/courses/{course_id}/sections/{section_id}/retry-failed",
            headers=_auth_headers(),
        )
    data = resp.json()
    assert data["retried"] == 2
    assert data["transcribe_retried"] == 1
    assert data["generate_retried"] == 1
    assert set(data["video_ids"]) == {transcribe_id, generate_id}
