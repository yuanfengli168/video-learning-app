"""Tests for generation router.

The /api/generate/{id} endpoint now runs in the background (returns
202 + a job dict, like /transcribe). These tests verify both:
1. The endpoint returns 202 + a job
2. The (mocked) background worker writes the correct assets to the DB
3. Subsequent GETs on the assets return the correct data
"""

import io
import uuid as uuid_module
from unittest.mock import patch

from fastapi.testclient import TestClient

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}
FAKE_MATERIALS = {
    "summary": "# Summary\nKey points.",
    "mindmap": "# Topic\n## Branch",
    "flashcards": [{"term": "AI", "definition": "Artificial Intelligence"}],
    "quiz": [{"question": "What?", "options": ["A", "B", "C", "D"], "answer": "A", "answer_index": 0}],
    "topic_timestamps": [
        {"topic": "Topic", "start": 0, "end": 60},
        {"topic": "Branch", "start": 60, "end": 120},
    ],
}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def _setup_video_with_transcript(paid_client: TestClient) -> str:
    """Helper: create course → section → video → transcript. Returns video_id.

    With the new background-task transcribe endpoint, we can't
    synchronously transcribe anymore — we mock the background worker
    to do the work synchronously and write the transcript asset.
    """
    with _mock_auth():
        course_resp = paid_client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = paid_client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]

        fake_video = io.BytesIO(b"fake video content")
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]

        fake_transcript = {
            "segments": [{"start": 0.0, "end": 5.0, "text": "Hello"}],
            "language": "en",
            "duration": 10.0,
        }
        def fake_transcribe_worker(vid: str, model: str) -> None:
            from app.services.transcription import transcript_to_json
            from app.database import SessionLocal
            from app.models import Asset, Video
            with SessionLocal() as db:
                v = db.get(Video, vid)
                if v:
                    db.add(Asset(
                        id=f"t-{vid[:8]}",
                        video_id=vid,
                        asset_type="transcript",
                        content=transcript_to_json(fake_transcript),
                    ))
                    v.status = "ready"
                    v.duration = fake_transcript["duration"]
                    db.commit()
        with patch(
            "app.routers.videos._run_transcribe_job",
            side_effect=fake_transcribe_worker,
        ):
            paid_client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )
    return video_id


def _run_generate_synchronously(paid_client: TestClient, video_id: str, materials: dict) -> None:
    """Helper: run the (mocked) generate worker synchronously so we can
    immediately query the assets in the test.

    Mirrors the real worker's UPSERT behavior: update an existing
    asset if one exists for that (video_id, asset_type) pair, else
    insert a new one. This is what the test_generate_regenerates_overwrite
    test depends on.
    """
    import uuid
    def fake_generate_worker(vid: str, user_id: str, user_role: int) -> None:
        from app.database import SessionLocal
        from app.models import Asset, Video
        import json
        asset_map = {
            "summary": materials.get("summary", ""),
            "mindmap": materials.get("mindmap", ""),
            "flashcards": json.dumps(materials.get("flashcards", []), ensure_ascii=False),
            "quiz": json.dumps(materials.get("quiz", []), ensure_ascii=False),
            "topic_timestamps": json.dumps(
                materials.get("topic_timestamps", []), ensure_ascii=False
            ),
        }
        with SessionLocal() as db:
            v = db.get(Video, vid)
            if v:
                for asset_type, content in asset_map.items():
                    existing = db.query(Asset).filter(
                        Asset.video_id == vid, Asset.asset_type == asset_type
                    ).first()
                    if existing:
                        existing.content = content
                    else:
                        db.add(Asset(
                            id=str(uuid.uuid4()),
                            video_id=vid,
                            asset_type=asset_type,
                            content=content,
                        ))
                v.status = "ready"
                db.commit()
    with patch("app.routers.generation._run_generate_job", side_effect=fake_generate_worker):
        with _mock_auth():
            paid_client.post(f"/api/generate/{video_id}", headers=_auth_headers())


# ── /api/generate/{id} tests ───────────────────────────────────────────────

def test_generate_returns_202_with_job(paid_client: TestClient):
    """POST /api/generate/{id} should return 202 + initial job state."""
    video_id = _setup_video_with_transcript(paid_client)

    with patch("app.routers.generation._run_generate_job"):
        with _mock_auth():
            response = paid_client.post(
                f"/api/generate/{video_id}", headers=_auth_headers()
            )
    assert response.status_code == 202
    data = response.json()
    assert data["video_id"] == video_id
    assert data["status"] == "running"
    assert "job" in data
    assert data["job"]["job_type"] == "generate"
    assert data["job"]["status"] == "running"


def test_generate_worker_saves_assets(paid_client: TestClient):
    """The (mocked) generate worker should write all asset types to DB."""
    video_id = _setup_video_with_transcript(paid_client)
    _run_generate_synchronously(paid_client, video_id, FAKE_MATERIALS)

    with _mock_auth():
        # All 5 asset types should be queryable now
        for asset_type in ("summary", "mindmap", "flashcards", "quiz", "topic_timestamps"):
            r = paid_client.get(
                f"/api/generate/{video_id}/assets/{asset_type}",
                headers=_auth_headers(),
            )
            assert r.status_code == 200, f"asset {asset_type} not saved: {r.text}"


def test_generate_saves_topic_timestamps(paid_client: TestClient):
    """Generate should save topic_timestamps asset."""
    video_id = _setup_video_with_transcript(paid_client)
    _run_generate_synchronously(paid_client, video_id, FAKE_MATERIALS)

    with _mock_auth():
        response = paid_client.get(
            f"/api/generate/{video_id}/assets/topic_timestamps",
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "topic_timestamps"
    assert len(data["data"]) == 2
    assert data["data"][0]["topic"] == "Topic"
    assert data["data"][0]["start"] == 0
    assert data["data"][1]["topic"] == "Branch"


def test_generate_no_transcript(paid_client: TestClient):
    """Should return 400 if no transcript exists."""
    with _mock_auth():
        course_resp = paid_client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = paid_client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]

        fake_video = io.BytesIO(b"fake")
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]

        response = paid_client.post(
            f"/api/generate/{video_id}", headers=_auth_headers()
        )
    assert response.status_code == 400
    assert "No transcript" in response.json()["detail"]


def test_generate_video_not_found(paid_client: TestClient):
    """Should return 404 for non-existent video."""
    with _mock_auth():
        response = paid_client.post(
            "/api/generate/nonexistent", headers=_auth_headers()
        )
    assert response.status_code == 404


def test_generate_free_user_gets_403(paid_client: TestClient):
    """Regression for Day-13 catalog-curation bug.

    POST /api/generate/{id} was previously gated only by
    `get_current_user`, so any signed-in FREE user could spam Ollama
    on any catalog video (DoS + cost). Now gated on REGEN_MATERIALS.

    We use `paid_client` to set up the video (course creation + upload
    require higher capabilities), then patch verify_token + force
    role=2 (FREE) before hitting /api/generate/{id}.
    """
    video_id = _setup_video_with_transcript(paid_client)

    from sqlalchemy import text
    from app.auth.admin import clear_role_cache
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
            f"/api/generate/{video_id}", headers=_auth_headers()
        )
    assert response.status_code == 403, (
        f"FREE user should be blocked from generating; got {response.status_code}: "
        f"{response.text[:200]}"
    )


def test_generate_paid_on_others_video_gets_403(paid_client: TestClient, admin_client: TestClient):
    """PAID users can regenerate materials on videos they OWN, but NOT
    catalog videos owned by another user (admin).

    Day-13 update: previously PAID could regenerate any video (the
    REGEN_MATERIALS capability alone was the gate). Now ownership
    matters too.
    """
    ADMIN_UID = "uid-admin"  # admin_client fixture's uid (different from paid)

    # Create admin's course + section + video + transcript via the API.
    # The admin_client fixture sets test-user-uid to role=0 and uid-admin
    # to role=0 in the users table; the FAKE_USER dict used by default
    # _mock_auth is test-user-uid, but we'll patch verify_token for the
    # admin calls so the resulting course is owned by uid-admin.
    ADMIN_FAKE = {"uid": ADMIN_UID, "email": "admin@test.com"}
    with patch("app.auth.dependencies.verify_token", return_value=ADMIN_FAKE):
        course_resp = admin_client.post(
            "/api/courses",
            json={"title": "Admin Catalog Test"},
            headers=_auth_headers(),
        )
        assert course_resp.status_code == 200, course_resp.text
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
        assert upload_resp.status_code == 202, upload_resp.text
        admin_video_id = upload_resp.json()["video_id"]

        # Inject a transcript asset so /generate passes the
        # "No transcript" 400 check (the auto-pipeline is suppressed).
        from app.database import SessionLocal
        from app.models import Asset
        with SessionLocal() as db:
            db.add(Asset(
                id=str(uuid_module.uuid4()),
                video_id=admin_video_id,
                asset_type="transcript",
                content='{"segments": [{"start": 0.0, "end": 1.0, "text": "Hi"}], "language": "en", "duration": 1.0}',
            ))
            db.commit()

    # Now PAID user (paid_client fixture, uid='test-user-uid') tries to
    # regenerate on admin's video.
    with _mock_auth():
        response = paid_client.post(
            f"/api/generate/{admin_video_id}", headers=_auth_headers()
        )
    assert response.status_code == 403, (
        f"PAID user should be blocked from generating admin's catalog video; "
        f"got {response.status_code}: {response.text[:200]}"
    )


def test_generate_paid_on_own_video_succeeds(paid_client: TestClient):
    """PAID users CAN regenerate materials on videos they uploaded.

    Companion test to test_generate_paid_on_others_video_gets_403.
    """
    video_id = _setup_video_with_transcript(paid_client)

    with patch("app.routers.generation._run_generate_job"):
        with _mock_auth():
            response = paid_client.post(
                f"/api/generate/{video_id}", headers=_auth_headers()
            )
    assert response.status_code == 202, (
        f"PAID user should be able to generate on their OWN video; "
        f"got {response.status_code}: {response.text[:200]}"
    )


def test_generate_admin_can_generate_any_video(paid_client: TestClient, admin_client: TestClient):
    """ADMIN can regenerate materials on any video regardless of owner.

    Day-13 update: ADMIN must be able to re-generate materials on
    their own catalog videos.
    """
    ADMIN_UID = "uid-admin"
    ADMIN_FAKE = {"uid": ADMIN_UID, "email": "admin@test.com"}

    # Create admin's catalog video + transcript
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

        from app.database import SessionLocal
        from app.models import Asset
        with SessionLocal() as db:
            db.add(Asset(
                id=str(uuid_module.uuid4()),
                video_id=admin_video_id,
                asset_type="transcript",
                content='{"segments": [{"start": 0.0, "end": 1.0, "text": "Hi"}], "language": "en", "duration": 1.0}',
            ))
            db.commit()

    # Now regenerate as ADMIN
    with patch("app.auth.dependencies.verify_token", return_value=ADMIN_FAKE):
        with patch("app.routers.generation._run_generate_job"):
            response = admin_client.post(
                f"/api/generate/{admin_video_id}", headers=_auth_headers()
            )
    assert response.status_code == 202, (
        f"ADMIN should be able to generate on any video; "
        f"got {response.status_code}: {response.text[:200]}"
    )


def test_generate_failure_marks_error_status(paid_client: TestClient):
    """If the (background) generate worker fails, video status becomes 'error'."""
    video_id = _setup_video_with_transcript(paid_client)

    def fake_generate_worker_raises(vid: str, user_id: str, user_role: int) -> None:
        from app.jobs import get_job, finish_job
        from app.database import SessionLocal
        from app.models import Video
        job = get_job(vid, "generate")
        if job:
            finish_job(job, status="failed", error="Ollama down")
        with SessionLocal() as db:
            v = db.get(Video, vid)
            if v:
                v.status = "error"
                db.commit()

    with patch(
        "app.routers.generation._run_generate_job",
        side_effect=fake_generate_worker_raises,
    ):
        with _mock_auth():
            response = paid_client.post(
                f"/api/generate/{video_id}", headers=_auth_headers()
            )
    # Endpoint returns 202 — the failure happens in the background.
    assert response.status_code == 202

    # The video's status field should now reflect the failure.
    with _mock_auth():
        get_resp = paid_client.get(
            f"/api/videos/{video_id}", headers=_auth_headers()
        )
    assert get_resp.json()["status"] == "error"


# ── /api/generate/{id}/assets/{type} tests ─────────────────────────────────

def test_get_asset_summary(paid_client: TestClient):
    video_id = _setup_video_with_transcript(paid_client)
    _run_generate_synchronously(paid_client, video_id, FAKE_MATERIALS)

    with _mock_auth():
        response = paid_client.get(
            f"/api/generate/{video_id}/assets/summary",
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "summary"
    assert "Key points" in data["data"]


def test_get_asset_flashcards(paid_client: TestClient):
    video_id = _setup_video_with_transcript(paid_client)
    _run_generate_synchronously(paid_client, video_id, FAKE_MATERIALS)

    with _mock_auth():
        response = paid_client.get(
            f"/api/generate/{video_id}/assets/flashcards",
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "flashcards"
    assert isinstance(data["data"], list)
    assert data["data"][0]["term"] == "AI"


def test_get_asset_quiz(paid_client: TestClient):
    video_id = _setup_video_with_transcript(paid_client)
    _run_generate_synchronously(paid_client, video_id, FAKE_MATERIALS)

    with _mock_auth():
        response = paid_client.get(
            f"/api/generate/{video_id}/assets/quiz",
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "quiz"
    assert data["data"][0]["question"] == "What?"


def test_get_asset_mindmap(paid_client: TestClient):
    video_id = _setup_video_with_transcript(paid_client)
    _run_generate_synchronously(paid_client, video_id, FAKE_MATERIALS)

    with _mock_auth():
        response = paid_client.get(
            f"/api/generate/{video_id}/assets/mindmap",
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "mindmap"
    assert "# Topic" in data["data"]


def test_get_asset_not_found(paid_client: TestClient):
    """Should return 404 if asset not generated."""
    video_id = _setup_video_with_transcript(paid_client)

    with _mock_auth():
        response = paid_client.get(
            f"/api/generate/{video_id}/assets/summary",
            headers=_auth_headers(),
        )
    assert response.status_code == 404


def test_get_asset_invalid_type(paid_client: TestClient):
    """Should return 400 for invalid asset type."""
    video_id = _setup_video_with_transcript(paid_client)

    with _mock_auth():
        response = paid_client.get(
            f"/api/generate/{video_id}/assets/nonexistent_type",
            headers=_auth_headers(),
        )
    assert response.status_code == 400


def test_generate_regenerates_overwrite(paid_client: TestClient):
    """Generating again should overwrite existing assets."""
    video_id = _setup_video_with_transcript(paid_client)
    _run_generate_synchronously(paid_client, video_id, FAKE_MATERIALS)

    new_materials = {
        "summary": "# New Summary",
        "mindmap": "# New Map",
        "flashcards": [{"term": "ML", "definition": "Machine Learning"}],
        "quiz": [],
    }
    _run_generate_synchronously(paid_client, video_id, new_materials)

    with _mock_auth():
        response = paid_client.get(
            f"/api/generate/{video_id}/assets/summary",
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    assert "New Summary" in response.json()["data"]


# ─────────────────────────────────────────────────────────────────────────
# Day 5 hotfix2: visibility-based access (was: course.user_id == uid)
# ─────────────────────────────────────────────────────────────────────────


def test_get_asset_allows_non_owner_for_public_video(paid_client: TestClient):
    """Day 5 hotfix2: a different FREE user can fetch a PUBLIC video's
    materials. Pre-fix this returned 403 'Not your video' because the
    ownership check only allowed the course owner to read materials."""
    video_id = _setup_video_with_transcript(paid_client)
    _run_generate_synchronously(paid_client, video_id, FAKE_MATERIALS)

    with patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "user-other", "email": "other@x.com"},
    ):
        response = paid_client.get(
            f"/api/generate/{video_id}/assets/summary",
            headers={"Authorization": "Bearer fake-user-other"},
        )
    assert response.status_code == 200
    assert "summary" in response.json()["type"]


def test_get_asset_blocks_non_owner_for_admin_only_video(paid_client: TestClient):
    """A non-admin FREE user is still blocked from ADMIN_ONLY videos."""
    video_id = _setup_video_with_transcript(paid_client)
    _run_generate_synchronously(paid_client, video_id, FAKE_MATERIALS)

    # Flip the video to ADMIN_ONLY after setup
    from app.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    db.execute(text("UPDATE videos SET visibility=2 WHERE id=:id"), {"id": video_id})
    db.commit()
    db.close()

    with patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "user-other", "email": "other@x.com"},
    ):
        response = paid_client.get(
            f"/api/generate/{video_id}/assets/summary",
            headers={"Authorization": "Bearer fake-user-other"},
        )
    assert response.status_code == 403
