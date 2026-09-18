"""Path-aware upload size cap — Cloudflare tunnel vs direct (2026-09-18).

Cloudflare's free plan rejects >100 MB request bodies at the EDGE
before they reach the server — a 700 MB upload through
www.capysmart.com surfaced as an opaque "server error" because the
request never arrived. The app therefore enforces the cap itself with
an actionable message:

  - direct requests (localhost / LAN):          10 GB cap (unchanged)
  - requests with Cf-Connecting-Ip header set:  100 MB cap (tunnel)

Cloudflare stamps every proxied request with Cf-Connecting-Ip, so its
presence is the honest signal the request came through the edge.
"""

import io
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.routers.videos import (
    MAX_FILE_SIZE,
    TUNNEL_MAX_FILE_SIZE,
    _effective_max_file_size,
)

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def test_tunnel_cap_constants():
    """The two tiers exist and are what the platform allows."""
    assert MAX_FILE_SIZE == 10 * 1024**3
    assert TUNNEL_MAX_FILE_SIZE == 100 * 1024**2


def test_effective_cap_direct_vs_tunnel():
    """No Cf header → 10 GB; Cf header → 100 MB."""
    # We use a minimal fake Request-like object: _effective_max_file_size
    # only reads .headers, so a dict-backed stub is faithful.
    class _Req:
        def __init__(self, headers: dict):
            self.headers = headers

    assert _effective_max_file_size(_Req({})) == MAX_FILE_SIZE
    assert _effective_max_file_size(
        _Req({"cf-connecting-ip": "203.0.113.7"})
    ) == TUNNEL_MAX_FILE_SIZE


def test_single_upload_100mb_rejected_via_tunnel_hint(
    paid_client: TestClient,
):
    """A >100 MB upload with the Cf header gets a 413 carrying the
    actionable 'upload from the server machine' hint."""
    course_id, section_id = _create_course_and_section(paid_client)

    with _mock_auth(), patch(
        "app.routers.videos.os.path.getsize",
        return_value=150 * 1024**2,
    ):
        r = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("big.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers={"cf-connecting-ip": "203.0.113.7", **_auth_headers()},
        )
    assert r.status_code == 413
    assert "100 MB" in r.json()["detail"]
    assert "localhost:8000" in r.json()["detail"]


def test_single_upload_150mb_accepted_direct(paid_client: TestClient):
    """The same 150 MB upload withOUT the Cf header is fine (LAN/localhost
    admin workflow for big files)."""
    course_id, section_id = _create_course_and_section(paid_client)

    with _mock_auth(), patch(
        "app.routers.videos.os.path.getsize",
        return_value=150 * 1024**2,
    ):
        r = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("big.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers=_auth_headers(),
        )
    assert r.status_code == 202, r.text


def test_bulk_upload_tunnel_cap_reports_hint(paid_client: TestClient):
    """Bulk path: a >100 MB file through the tunnel is skipped with the
    same actionable message (per-file outcome, batch continues)."""
    course_id, section_id = _create_course_and_section(paid_client)

    with _mock_auth(), patch(
        "app.routers.videos.os.path.getsize",
        return_value=300 * 1024**2,
    ):
        r = paid_client.post(
            f"/api/videos/upload-bulk/{section_id}",
            files=[("files", ("huge.mp4", io.BytesIO(b"x"), "video/mp4"))],
            headers={"cf-connecting-ip": "203.0.113.7", **_auth_headers()},
        )
    data = r.json()
    assert data["skipped"] == 1
    assert "100 MB" in data["results"][0]["error"]
    assert "localhost:8000" in data["results"][0]["error"]


# ── helpers (mirror tests/test_videos.py conventions) ────────────────

def _create_course_and_section(paid_client: TestClient):
    """Create a course + section owned by the authenticated user
    (same helper shape as tests/test_videos.py)."""
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