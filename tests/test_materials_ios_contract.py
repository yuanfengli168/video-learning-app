"""MVP0.2: Contract tests between the backend and iOS Swift Codable models.

The iOS app's `MaterialModels.swift` defines `VideoMaterialItem` and
`VideoMaterialsResponse` with `CodingKeys` that map snake_case JSON
to camelCase Swift fields. If the backend ever changes the wire
format (renames a key, changes a type), the iOS app will silently
drop data on decode. These tests pin the JSON shape so any change
must be reflected in the Swift models (and vice-versa).

Mirrors of `ios/PocketMVP/Models/MaterialModels.swift` + the
APIClient.fetchVideoMaterials() + fetchMaterialText() URL paths.
"""

from __future__ import annotations

import io
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Video

FAKE_USER = {"uid": "test-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def _mac_origin_headers():
    return {
        "Authorization": "Bearer fake-token",
        "Origin": "https://localhost:8443",
    }


def _create_course_section_video(client, db_session):
    """Create a course + section + video row. Returns (course_id, section_id, video_id)."""
    with _mock_auth():
        course_id = client.post(
            "/api/courses", json={"title": "iOS Contract"},
            headers=_auth_headers(),
        ).json()["course_id"]
        section_id = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "S1"}, headers=_auth_headers(),
        ).json()["section_id"]
    db_session.add(Video(
        id="ios-contract-vid", title="V", filename="v.mp4",
        file_path="/tmp/v.mp4", file_size=10, section_id=section_id, status="ready",
    ))
    db_session.commit()
    return course_id, section_id, "ios-contract-vid"


def _upload_md(client, section_id, filename="c.md", body=b"hello"):
    return client.post(
        "/api/materials",
        files={"file": (filename, io.BytesIO(body), "text/markdown")},
        data={"section_id": section_id},
        headers=_mac_origin_headers(),
    )


# ────────────────────────────────────────────────────────────────────
# /api/videos/{id}/materials shape (VideoMaterialsResponse + VideoMaterialItem)
# ────────────────────────────────────────────────────────────────────


def test_ios_video_materials_response_matches_swift_codingkeys(client, db_session):
    """JSON returned by /api/videos/{id}/materials must match the iOS CodingKeys.

    Keys asserted (must match VideoMaterialItem.CodingKeys):
      - material_id, filename, size_bytes, char_count, added_at

    Keys asserted (must match VideoMaterialsResponse.CodingKeys):
      - video_id, selected_ids, available
    """
    course_id, section_id, video_id = _create_course_section_video(client, db_session)
    with _mock_auth():
        up = _upload_md(client, section_id, "c1.md", b"first content")
        m1 = up.json()["id"]
        # Time passes so added_at differs deterministically
        client.put(
            f"/api/videos/{video_id}/materials",
            json={"material_ids": [m1]},
            headers=_auth_headers(),
        )
        resp = client.get(
            f"/api/videos/{video_id}/materials",
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()

    # Top-level keys
    assert set(data.keys()) == {"video_id", "selected_ids", "available"}

    # selected_ids content
    assert data["selected_ids"] == [m1]
    assert data["video_id"] == video_id

    # Each item in 'available' has the keys the iOS Swift struct expects
    assert len(data["available"]) == 1
    item = data["available"][0]
    # MVP0.2 followup: extraction_method was added so the iOS picker
    # can show an "OCR" badge for materials that required OCR.
    # It's optional in the Swift struct (extractionMethod: String?)
    # so existing keys must still match.
    assert set(item.keys()) == {
        "material_id", "filename", "size_bytes", "char_count", "added_at",
        "extraction_method",
    }
    assert item["material_id"] == m1
    assert item["filename"] == "c1.md"
    assert isinstance(item["size_bytes"], int)
    assert isinstance(item["char_count"], int) and item["char_count"] > 0
    # added_at is parseable as ISO 8601 (Swift's default JSONDecoder.dateDecodingStrategy = .deferredToDate)
    # — backend returns datetime.isoformat() which Swift interprets as a Double seconds-since-2001
    # only if the format matches. For now we just assert it's a non-empty string.
    assert isinstance(item["added_at"], str) and len(item["added_at"]) > 0
    # extraction_method is "pypdf" for any material that went through
    # the extractor with a successful read path (.md / .txt / native-PDF).
    # See app/services/material_extractor.py:extract() — the method tag
    # is "pypdf" for non-OCR paths. OCR-tagged materials would have
    # "vision" / "ollama_vision" / "tesseract" here.
    assert item["extraction_method"] == "pypdf"


def test_ios_video_materials_response_empty_selected(client, db_session):
    """Empty selected_ids — iOS renders 'No materials in context'."""
    course_id, section_id, video_id = _create_course_section_video(client, db_session)
    with _mock_auth():
        _upload_md(client, section_id, "e.md")
        resp = client.get(
            f"/api/videos/{video_id}/materials",
            headers=_auth_headers(),
        )
    data = resp.json()
    assert data["selected_ids"] == []
    assert isinstance(data["available"], list)


# ────────────────────────────────────────────────────────────────────
# /api/materials/{id}/text shape (APIClient.fetchMaterialText returns String)
# ────────────────────────────────────────────────────────────────────


def test_ios_material_text_endpoint_returns_text_plain(client, db_session):
    """APIClient.fetchMaterialText expects a text/plain body — not JSON."""
    course_id, section_id, _ = _create_course_section_video(client, db_session)
    with _mock_auth():
        up = _upload_md(client, section_id, "t.md", b"# Title\n\nBody text here")
        material_id = up.json()["id"]
        resp = client.get(
            f"/api/materials/{material_id}/text",
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    # Content-Type must be text/plain (iOS String(data:encoding:) won't JSON-decode this)
    ct = resp.headers.get("content-type", "")
    assert ct.startswith("text/plain"), f"expected text/plain, got {ct!r}"
    # Body is the extracted text directly
    assert "Title" in resp.text
    assert "Body text here" in resp.text


# ────────────────────────────────────────────────────────────────────
# /m/snapshot shape (Video.selected_materials in VideoOut)
# ────────────────────────────────────────────────────────────────────


def test_ios_pocket_snapshot_video_selected_materials_field(client, db_session):
    """VideoOut.selected_materials is the field iOS reads to decide whether
    to show the 'N materials in context' badge and the MaterialsPanel."""
    course_id, section_id, video_id = _create_course_section_video(client, db_session)
    with _mock_auth():
        m1 = _upload_md(client, section_id, "s.md").json()["id"]
        client.put(
            f"/api/videos/{video_id}/materials",
            json={"material_ids": [m1]},
            headers=_auth_headers(),
        )
        snap = client.get(
            "/m/snapshot", headers=_auth_headers(),
        ).json()
    target = next(v for v in snap["videos"] if v["id"] == video_id)
    # Field must exist (so the iOS Video.init(from:) decodeIfPresent gets a key)
    assert "selected_materials" in target
    # And must be a list of strings
    assert isinstance(target["selected_materials"], list)
    assert target["selected_materials"] == [m1]


def test_ios_pocket_snapshot_handles_old_server_without_field(client, db_session):
    """iOS Video.init(from:) uses decodeIfPresent for backward compat.
    If the server returns a snapshot without selected_materials, the
    iOS client should default to [] (don't crash).
    Verify the server actually sends the field (so we don't accidentally
    break the contract that lets iOS upgrade independently of the server).
    """
    course_id, section_id, video_id = _create_course_section_video(client, db_session)
    with _mock_auth():
        snap = client.get(
            "/m/snapshot", headers=_auth_headers(),
        ).json()
    target = next(v for v in snap["videos"] if v["id"] == video_id)
    # Even with NO materials selected, the field MUST be present (empty list)
    assert "selected_materials" in target
    assert target["selected_materials"] == []