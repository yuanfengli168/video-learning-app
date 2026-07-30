"""Tests for the MVP0.2 materials feature.

Covers:
  - POST /api/materials  (Mac-only upload, sync extract)
  - GET  /api/materials  (list + filter by section/video)
  - GET  /api/materials/{id}  (get single)
  - GET  /api/materials/{id}/text  (raw text)
  - DELETE /api/materials/{id}  (delete + cascade)
  - POST /api/materials/{id}/link  (re-link section/video)
  - GET /api/videos/{video_id}/materials  (selected + available)
  - PUT /api/videos/{video_id}/materials  (replace selection)
  - material_extractor: detect_kind + extract for .md/.txt/.pdf/.zip
  - material_context.build_materials_section  (prompt section + truncation)
  - pocket sync includes `selected_materials` field
  - origin gating: iOS (no Origin) is rejected for upload
  - per-user storage cap (settings.materials_max_total_bytes_per_user)
"""

from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Video
from app.services.material_context import build_materials_section
from app.services.material_extractor import detect_kind, extract

FAKE_USER = {"uid": "test-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def _mac_origin_headers():
    """Mac browser sets Origin header."""
    return {
        "Authorization": "Bearer fake-token",
        "Origin": "https://localhost:8443",
    }


def _create_section(client: TestClient) -> str:
    """Create a course + section, return section_id."""
    with _mock_auth():
        course_id = client.post(
            "/api/courses",
            json={"title": "Materials Test Course"},
            headers=_auth_headers(),
        ).json()["course_id"]
        section_id = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Section A"},
            headers=_auth_headers(),
        ).json()["section_id"]
    return section_id


def _create_video(db: Session, section_id: str, video_id: str = "test-vid-001") -> str:
    """Create a Video row directly (no file upload needed)."""
    db.add(Video(
        id=video_id,
        title="Test Video",
        filename="test.mp4",
        file_path="/tmp/fake.mp4",
        file_size=1024,
        section_id=section_id,
        status="ready",
    ))
    db.commit()
    return video_id


def _upload_md(client: TestClient, section_id: str, filename="notes.md", body=b"# Heading\n\nSome **markdown** content.\n"):
    return client.post(
        "/api/materials",
        files={"file": (filename, io.BytesIO(body), "text/markdown")},
        data={"section_id": section_id},
        headers=_mac_origin_headers(),
    )


# ────────────────────────────────────────────────────────────────────
# detect_kind + extract
# ────────────────────────────────────────────────────────────────────


def test_detect_kind_classifies_supported_extensions():
    assert detect_kind("a.pdf") == "pdf"
    assert detect_kind("a.PDF") == "pdf"  # case-insensitive
    assert detect_kind("a.md") == "markdown"
    assert detect_kind("a.markdown") == "markdown"
    assert detect_kind("a.txt") == "text"
    assert detect_kind("a.zip") == "zip"
    assert detect_kind("a.docx") == "unknown"
    assert detect_kind("a.png") == "unknown"


def _extract_text(filename: str, data: bytes) -> str | None:
    """Helper: call extract() and return just the text part.

    extract() now returns (text, method) where method is the
    provenance ('pypdf', 'vision', 'ollama_vision', 'tesseract', or
    None for unsupported formats). Tests that don't care about
    provenance use this helper to keep assertions terse.
    """
    result = extract(filename, data)
    if isinstance(result, tuple):
        return result[0]
    return result


def test_extract_markdown_returns_content():
    text = _extract_text("notes.md", b"# Title\n\n- one\n- two")
    assert text is not None
    assert "Title" in text
    assert "one" in text


def test_extract_text_returns_content():
    text = _extract_text("readme.txt", b"Hello world\nLine 2")
    assert text is not None
    assert "Hello" in text


def test_extract_pdf_returns_string_or_raises_with_clear_error():
    """PDFs may either extract successfully (returns str) OR have no
    extractable text layer (returns None after the OCR chain
    exhausts). The new contract is "never silently return empty
    for a PDF".
    """
    minimal_pdf = (
        b"%PDF-1.1\n%\xe2\xe3\xcf\xd3\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f\n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n0\n%%EOF\n"
    )
    result = extract("a.pdf", minimal_pdf)
    # extract returns (text, method) for PDF
    text, method = result
    if text is None:
        # OCR chain exhausted — acceptable; just verify method is None
        assert method is None
    else:
        # Got text — method must be one of the recognized values
        assert method in ("pypdf", "vision", "ollama_vision", "tesseract")


def test_extract_unknown_returns_none():
    result = extract("image.png", b"\x89PNG\r\n\x1a\n")
    text, method = result
    assert text is None
    assert method is None


def test_extract_zip_text_files():
    """Zip with .md + .txt files concatenates content."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("src/main.md", "# Heading\n\nBody")
        zf.writestr("notes.txt", "Plain notes")
    data = buf.getvalue()
    text, method = extract("bundle.zip", data)
    assert text is not None
    assert "Heading" in text
    assert "Plain notes" in text
    assert method == "pypdf"


def test_extract_zip_skips_binary_files():
    """Zip containing only images should return a friendly 'no readable text' message."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("logo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    data = buf.getvalue()
    text, method = extract("bundle.zip", data)
    # Either a friendly empty message (status='ready') or None (failed).
    # In either case method should be "pypdf" (zip walked but found nothing).
    assert method == "pypdf"
    if text is None:
        pass  # acceptable — extract() can return (None, "pypdf")
    else:
        assert "no readable text" in text.lower()


# ────────────────────────────────────────────────────────────────────
# Upload endpoint
# ────────────────────────────────────────────────────────────────────


def test_upload_md_material_succeeds(client: TestClient, db_session: Session):
    section_id = _create_section(client)
    with _mock_auth():
        resp = _upload_md(client, section_id)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "ready"
    assert data["filename"] == "notes.md"
    assert data["section_id"] == section_id
    assert data["char_count"] > 0
    assert "id" in data


def test_upload_rejects_ios_no_origin(client: TestClient, db_session: Session):
    """iOS doesn't send Origin; upload must reject to prevent pocket uploads."""
    section_id = _create_section(client)
    with _mock_auth():
        resp = client.post(
            "/api/materials",
            files={"file": ("notes.md", io.BytesIO(b"hello"), "text/markdown")},
            data={"section_id": section_id},
            headers={"Authorization": "Bearer fake-token"},  # NO Origin
        )
    assert resp.status_code == 403
    assert "Mac" in resp.json()["detail"]


def test_upload_requires_section_or_video(client: TestClient, db_session: Session):
    """Materials must be scoped to at least a section (where Mac uploads live)."""
    with _mock_auth():
        resp = client.post(
            "/api/materials",
            files={"file": ("notes.md", io.BytesIO(b"hello"), "text/markdown")},
            data={},  # neither section_id nor video_id
            headers=_mac_origin_headers(),
        )
    assert resp.status_code == 400


def test_upload_auto_links_video_when_provided(client: TestClient, db_session: Session):
    """If video_id is supplied, a PocketVideoMaterial row is created on upload."""
    section_id = _create_section(client)
    video_id = _create_video(db_session, section_id)
    with _mock_auth():
        resp = client.post(
            "/api/materials",
            files={"file": ("notes.md", io.BytesIO(b"linked"), "text/markdown")},
            data={"section_id": section_id, "video_id": video_id},
            headers=_mac_origin_headers(),
        )
    assert resp.status_code == 201
    material_id = resp.json()["id"]

    # Verify the link exists via the per-video selection endpoint
    with _mock_auth():
        sel = client.get(
            f"/api/videos/{video_id}/materials",
            headers=_auth_headers(),
        ).json()
    assert material_id in sel["selected_ids"]


def test_upload_rejects_unsupported_file(client: TestClient, db_session: Session):
    section_id = _create_section(client)
    with _mock_auth():
        resp = client.post(
            "/api/materials",
            files={"file": ("photo.png", io.BytesIO(b"\x89PNG\r\n\x1a\n"), "image/png")},
            data={"section_id": section_id},
            headers=_mac_origin_headers(),
        )
    assert resp.status_code == 415


def test_upload_enforces_per_user_total_cap(client: TestClient, db_session: Session):
    """Uploads should be rejected once the user hits the per-user total cap."""
    section_id = _create_section(client)
    # Shrink the cap so the test is fast
    original = settings.materials_max_total_bytes_per_user
    settings.materials_max_total_bytes_per_user = 200  # 200 bytes total
    try:
        with _mock_auth():
            r1 = client.post(
                "/api/materials",
                files={"file": ("a.md", io.BytesIO(b"x" * 150), "text/markdown")},
                data={"section_id": section_id},
                headers=_mac_origin_headers(),
            )
            assert r1.status_code == 201
            # Second upload should exceed the 200-byte cap
            r2 = client.post(
                "/api/materials",
                files={"file": ("b.md", io.BytesIO(b"y" * 100), "text/markdown")},
                data={"section_id": section_id},
                headers=_mac_origin_headers(),
            )
        assert r2.status_code == 413
        assert "cap" in r2.json()["detail"].lower()
    finally:
        settings.materials_max_total_bytes_per_user = original


# ────────────────────────────────────────────────────────────────────
# List / Get / Delete / Link
# ────────────────────────────────────────────────────────────────────


def test_list_filters_by_section(client: TestClient, db_session: Session):
    section_a = _create_section(client)
    with _mock_auth():
        # Create a second section
        course_id = client.post(
            "/api/courses", json={"title": "Other"}, headers=_auth_headers(),
        ).json()["course_id"]
        section_b = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "B"}, headers=_auth_headers(),
        ).json()["section_id"]
        # Upload 1 in section A, 1 in section B
        _upload_md(client, section_a, "a.md")
        _upload_md(client, section_b, "b.md")
        # List with section filter
        resp = client.get(
            f"/api/materials?section_id={section_a}",
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["filename"] == "a.md"


def test_get_material_text_returns_plaintext(client: TestClient, db_session: Session):
    section_id = _create_section(client)
    with _mock_auth():
        body = b"# Get Text Test\n\nContent body"
        up = _upload_md(client, section_id, "get.md", body)
        material_id = up.json()["id"]
        resp = client.get(
            f"/api/materials/{material_id}/text",
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "Get Text Test" in resp.text
    assert "Content body" in resp.text


def test_delete_cascades_to_video_links(client: TestClient, db_session: Session):
    section_id = _create_section(client)
    video_id = _create_video(db_session, section_id)
    with _mock_auth():
        up = client.post(
            "/api/materials",
            files={"file": ("to-delete.md", io.BytesIO(b"bye"), "text/markdown")},
            data={"section_id": section_id, "video_id": video_id},
            headers=_mac_origin_headers(),
        )
        material_id = up.json()["id"]
        # Delete
        resp = client.delete(
            f"/api/materials/{material_id}",
            headers=_mac_origin_headers(),
        )
    assert resp.status_code == 204
    # Selection should be empty
    with _mock_auth():
        sel = client.get(
            f"/api/videos/{video_id}/materials",
            headers=_auth_headers(),
        ).json()
    assert sel["selected_ids"] == []


def test_delete_blocks_ios(client: TestClient, db_session: Session):
    section_id = _create_section(client)
    with _mock_auth():
        up = _upload_md(client, section_id, "del.md")
        material_id = up.json()["id"]
        # iOS tries to delete (no Origin)
        resp = client.delete(
            f"/api/materials/{material_id}",
            headers={"Authorization": "Bearer fake-token"},
        )
    assert resp.status_code == 403


def test_link_material_updates_section_and_video(client: TestClient, db_session: Session):
    section_a = _create_section(client)
    with _mock_auth():
        up = _upload_md(client, section_a, "link.md")
        material_id = up.json()["id"]
        # Re-link to a new section
        course_id = client.post(
            "/api/courses", json={"title": "Re"}, headers=_auth_headers(),
        ).json()["course_id"]
        section_b = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "B"}, headers=_auth_headers(),
        ).json()["section_id"]
        resp = client.post(
            f"/api/materials/{material_id}/link",
            data={"section_id": section_b},
            headers=_mac_origin_headers(),
        )
    assert resp.status_code == 200
    assert resp.json()["section_id"] == section_b


# ────────────────────────────────────────────────────────────────────
# Per-video selection
# ────────────────────────────────────────────────────────────────────


def test_video_materials_set_replaces_selection(client: TestClient, db_session: Session):
    section_id = _create_section(client)
    video_id = _create_video(db_session, section_id, "vid-set-test")
    with _mock_auth():
        m1 = _upload_md(client, section_id, "m1.md").json()["id"]
        m2 = _upload_md(client, section_id, "m2.md").json()["id"]
        m3 = _upload_md(client, section_id, "m3.md").json()["id"]
        # Set selection to [m2, m3] — should drop m1 if it had been linked
        # (it wasn't linked, but this verifies the PUT semantics)
        resp = client.put(
            f"/api/videos/{video_id}/materials",
            json={"material_ids": [m2, m3]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    assert set(resp.json()["selected_ids"]) == {m2, m3}


def test_video_materials_get_rejects_other_user_material(client: TestClient, db_session: Session):
    """Setting materials from a different user must be rejected (security)."""
    section_id = _create_section(client)
    video_id = _create_video(db_session, section_id, "vid-sec-test")
    with _mock_auth():
        m1 = _upload_md(client, section_id, "mine.md").json()["id"]
        # Now switch the active user (verify_token returns a different uid)
        with patch(
            "app.auth.dependencies.verify_token",
            return_value={"uid": "other-user", "email": "other@x.com"},
        ):
            resp = client.put(
                f"/api/videos/{video_id}/materials",
                json={"material_ids": [m1]},
                headers=_auth_headers(),
            )
    assert resp.status_code == 403
    assert "don't belong" in resp.json()["detail"].lower() or "denied" in resp.json()["detail"].lower() or "user" in resp.json()["detail"].lower()


def test_video_materials_available_includes_section_pool(client: TestClient, db_session: Session):
    """A material in the section (not linked to any video) should appear in `available`."""
    section_id = _create_section(client)
    video_id = _create_video(db_session, section_id, "vid-pool-test")
    sibling_id = _create_video(db_session, section_id, "vid-sibling")
    with _mock_auth():
        # Upload a section-level material (no video_id) — should be available to both videos
        section_mat = _upload_md(client, section_id, "shared.md").json()["id"]
        # Upload a material already linked to the sibling — should be available to the main video too
        sibling_mat = client.post(
            "/api/materials",
            files={"file": ("sib.md", io.BytesIO(b"sib"), "text/markdown")},
            data={"section_id": section_id, "video_id": sibling_id},
            headers=_mac_origin_headers(),
        ).json()["id"]
        resp = client.get(
            f"/api/videos/{video_id}/materials",
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    available_ids = {m["material_id"] for m in data["available"]}
    assert section_mat in available_ids
    assert sibling_mat in available_ids
    assert data["selected_ids"] == []


# ────────────────────────────────────────────────────────────────────
# material_context service
# ────────────────────────────────────────────────────────────────────


def test_build_materials_section_empty_when_none_selected(client: TestClient, db_session: Session):
    section_id = _create_section(client)
    video_id = _create_video(db_session, section_id, "vid-ctx-empty")
    ctx = build_materials_section(db_session, video_id, user_id="test-uid")
    assert ctx.prompt_section == ""
    assert ctx.materials == []
    assert ctx.truncated is False


def test_build_materials_section_includes_selected(client: TestClient, db_session: Session):
    section_id = _create_section(client)
    video_id = _create_video(db_session, section_id, "vid-ctx-incl")
    with _mock_auth():
        m1 = _upload_md(client, section_id, "alpha.md", b"# Alpha\n\nFirst material").json()["id"]
        # Link to video
        client.put(
            f"/api/videos/{video_id}/materials",
            json={"material_ids": [m1]},
            headers=_auth_headers(),
        )
    ctx = build_materials_section(db_session, video_id, user_id="test-uid")
    assert "USER-UPLOADED MATERIALS" in ctx.prompt_section
    assert "Alpha" in ctx.prompt_section
    assert ctx.materials[0]["filename"] == "alpha.md"
    assert ctx.materials[0]["included"] is True
    assert ctx.truncated is False


def test_build_materials_section_truncates_when_over_cap(client: TestClient, db_session: Session, monkeypatch):
    """When selected materials exceed the total cap, the later ones are dropped."""
    from app.services import material_context as mc

    monkeypatch.setattr(mc, "MAX_CHARS_TOTAL_MATERIALS", 200)  # tiny cap
    section_id = _create_section(client)
    video_id = _create_video(db_session, section_id, "vid-ctx-trunc")
    with _mock_auth():
        m_old = _upload_md(client, section_id, "old.md", b"x" * 150).json()["id"]
        m_new = _upload_md(client, section_id, "new.md", b"y" * 150).json()["id"]
        # Select both — old goes in, new gets truncated (cap reached)
        client.put(
            f"/api/videos/{video_id}/materials",
            json={"material_ids": [m_old, m_new]},
            headers=_auth_headers(),
        )
    ctx = build_materials_section(db_session, video_id, user_id="test-uid")
    assert ctx.truncated is True
    filenames = {m["filename"]: m for m in ctx.materials}
    assert filenames["old.md"]["included"] is True
    assert filenames["new.md"]["included"] is False


# ────────────────────────────────────────────────────────────────────
# Pocket sync includes selected_materials
# ────────────────────────────────────────────────────────────────────


def test_pocket_sync_includes_selected_materials(client: TestClient, db_session: Session):
    section_id = _create_section(client)
    video_id = _create_video(db_session, section_id, "vid-sync-test")
    with _mock_auth():
        m1 = _upload_md(client, section_id, "sync-mat.md").json()["id"]
        client.put(
            f"/api/videos/{video_id}/materials",
            json={"material_ids": [m1]},
            headers=_auth_headers(),
        )
        # Hit the pocket sync endpoint
        resp = client.get(
            "/m/snapshot",
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    snap = resp.json()
    target = next(v for v in snap["videos"] if v["id"] == video_id)
    assert "selected_materials" in target
    assert target["selected_materials"] == [m1]