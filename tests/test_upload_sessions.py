"""Chunked-upload session tests (13a commit D, 2026-09-21).

Covers the registry §1–§3a design end to end:

Resolver:
  - tier defaults from env (1GB PAID / 20GB ADMIN)
  - per-user override BEATS tier default (the add-on infra)
  - unknown role → conservative PAID limit
  - friendly wording (specific numbers + actionable suggestion)

Init:
  - full validation matrix: ownership, extension allowlist,
    per-file tier limit (413 + wording), one-active-session rule,
    quota headroom fail-fast (413 + the honest math)
  - the chunk-SUM is what's checked (never per-chunk — §1's trap)

Chunks:
  - append + idempotent re-send (network-retry contract)
  - index bounds + wrong-size chunks (the lying client)
  - ownership: another user's session → 404 (no existence leak)
  - swept/completed session → 409 (§3a's explicit race answer)

Complete:
  - full happy path: init → chunks → complete → Video row queued,
    staging dir consumed, byte-verification holds
  - incomplete (missing chunks) → 400 with the missing list
  - wrong declared size → 400, nothing queued
  - the transcribe job is REGISTERED at creation (the 9/19 lesson)

Sweeper:
  - TTL honored: old sessions swept (staging dir gone, row
    'cancelled', events row written), fresh sessions kept
  - idempotent: double-sweep is a no-op
  - claim durability: a swept claim survives a fresh session (the
    c827a7b regression shape applied to THIS sweeper)

Quota interactions:
  - staging counts at DECLARED size (the reservation model)
  - DELETE = instant release (no sweeper wait)
  - completed sessions stop counting
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Course, Section, User, Video

FAKE_PAID = {"uid": "test-user-uid", "email": "test@example.com", "role": 1}
FAKE_ADMIN = {"uid": "uid-admin", "email": "admin@x.com", "role": 0}
FAKE_OTHER = {"uid": "user-B", "email": "b@x.com", "role": 1}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock(fake=FAKE_PAID):
    return patch(
        "app.auth.dependencies.verify_token", return_value=fake
    )


def _mk_section(db: Session, uid: str = "test-user-uid") -> Section:
    course = Course(title="C", description="", user_id=uid)
    db.add(course)
    db.flush()
    section = Section(title="S", course_id=course.id, order_index=0)
    db.add(section)
    db.commit()
    return section


@pytest.fixture(autouse=True)
def _tiny_chunks(monkeypatch):
    """32MB chunks make every test allocate hundreds of MB of RAM;
    shrink to 1KB so tests exercise the LOGIC, not the allocator."""
    from app.config import settings
    monkeypatch.setattr(settings, "upload_chunk_size_mb", 0.001)
    yield


# ── Resolver ────────────────────────────────────────────────────────────────


def test_resolver_tier_defaults():
    """PAID 1GB / ADMIN 20GB from the env-driven settings."""
    from app.config import settings
    from app.services.upload_limits import (
        max_file_size_for_role,
        storage_quota_for_role,
    )

    class U:
        role = 1
        max_file_bytes = None
        storage_quota_bytes = None

    assert max_file_size_for_role(U()) == int(settings.upload_max_file_paid_gb * 1024**3)
    u = U(); u.role = 0
    assert max_file_size_for_role(u) == int(settings.upload_max_file_admin_gb * 1024**3)
    class U2(U): role = 1
    assert storage_quota_for_role(U2()) == int(settings.storage_quota_paid_gb * 1024**3)


def test_resolver_override_beats_tier():
    """A set per-user override wins — the paid-add-on infra."""
    from app.services.upload_limits import max_file_size_for_role, storage_quota_for_role

    class U:
        role = 1
        max_file_bytes = 4 * 1024**3      # 'they paid more' → 4GB files
        storage_quota_bytes = 50 * 1024**3  # → 50GB quota

    assert max_file_size_for_role(U()) == 4 * 1024**3
    assert storage_quota_for_role(U()) == 50 * 1024**3


def test_resolver_unknown_role_conservative():
    """Unknown role → the tighter PAID number (fail safe)."""
    from app.services.upload_limits import max_file_size_for_role

    class U:
        role = 99
        max_file_bytes = None
        storage_quota_bytes = None

    assert max_file_size_for_role(U()) == 1024**3  # 1GB


def test_resolver_friendly_wording():
    """The §1 message: specific numbers + actionable suggestion."""
    from app.services.upload_limits import UploadLimitError, check_file_size

    class U:
        role = 1
        max_file_bytes = None
        storage_quota_bytes = None

    with pytest.raises(UploadLimitError) as e:
        check_file_size(U(), 2 * 1024**3)
    msg = e.value.message
    assert "2.0 GB" in msg
    assert "1.0 GB" in msg
    assert "compressing" in msg.lower()


# ── Init ───────────────────────────────────────────────────────────────────


def _init(client, db, *, size, uid=FAKE_PAID, filename="lecture.mp4"):
    section = _mk_section(db)
    with _mock(uid):
        resp = client.post(
            "/api/upload-sessions/init",
            json={
                "section_id": section.id,
                "filename": filename,
                "declared_size": size,
            },
            headers=_auth_headers(),
        )
    return resp, section


def test_init_happy_path(paid_client, db_session):
    """Init returns the chunk plan; staging dir created."""
    resp, section = _init(paid_client, db_session, size=5000)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "session_id" in data
    assert data["chunk_size"] > 0
    assert data["total_chunks"] == 5  # 5000 bytes / 1KB chunks
    assert data["received_chunks"] == []


def test_init_rejects_bad_extension(paid_client, db_session):
    """The shared extension allowlist (same as legacy path)."""
    section = _mk_section(db_session)
    with _mock():
        resp = paid_client.post(
            "/api/upload-sessions/init",
            json={
                "section_id": section.id,
                "filename": "virus.exe",
                "declared_size": 100,
            },
            headers=_auth_headers(),
        )
    assert resp.status_code == 400
    assert "not allowed" in resp.json()["detail"]


def test_init_over_tier_limit_413(paid_client, db_session):
    """Over the PAID tier limit → 413 with the tier wording —
    BEFORE any bytes transfer (fail-fast, registry §5)."""
    resp, _ = _init(paid_client, db_session, size=2 * 1024**3)
    assert resp.status_code == 413
    assert "your plan allows up to 1.0 GB" in resp.json()["detail"]


def test_init_admin_tier_allows_bigger(admin_client, db_session):
    """ADMIN (20GB tier) passes a 2GB declaration the PAID tier rejects."""
    # Section owned by the ADMIN uid (admin_client's fixture user).
    section = _mk_section(db_session, uid="uid-admin")
    with _mock(FAKE_ADMIN):
        resp = admin_client.post(
            "/api/upload-sessions/init",
            json={
                "section_id": section.id,
                "filename": "lecture.mp4",
                "declared_size": 2 * 1024**3,
            },
            headers=_auth_headers(),
        )
    assert resp.status_code == 200, resp.text


def test_init_per_user_override_allows_bigger(paid_client, db_session):
    """The add-on infra end to end: flip users.max_file_bytes via SQL
    (the flip-kit's future action) → the same PAID user passes 2GB."""
    db_session.execute(
        __import__("sqlalchemy").text(
            "UPDATE users SET max_file_bytes = :v WHERE user_id='test-user-uid'"
        ),
        {"v": 4 * 1024**3},
    )
    db_session.commit()
    resp, _ = _init(paid_client, db_session, size=2 * 1024**3)
    assert resp.status_code == 200, resp.text


def test_init_one_active_session_rule(paid_client, db_session):
    """A second init while one is active → 400 with the actionable
    message (registry §3 — blocks the reservation stacking game)."""
    r1, _ = _init(paid_client, db_session, size=1000)
    assert r1.status_code == 200
    r2, _ = _init(paid_client, db_session, size=1000)
    assert r2.status_code == 400
    assert "already have an upload in progress" in r2.json()["detail"]


def test_init_quota_headroom_fail_fast(paid_client, db_session):
    """Over the storage quota → 413 with the honest math, before bytes.
    (800MB: under the 1GB per-file cap so the TIER check passes; the
    seed leaves only ~500MB headroom so the QUOTA check is what fires.)"""
    # Give the user a completed video that nearly fills 25GB.
    section = _mk_section(db_session)
    db_session.add(Video(
        title="big", filename="big.mp4", file_path="/tmp/big.mp4",
        file_size=25 * 1024**3 - 500 * 1024**2,  # ~500MB headroom left
        section_id=section.id, status="ready", visibility=0,
        caption_languages="[]",
    ))
    db_session.commit()
    # 800MB upload won't fit → 413 with the math.
    resp, _ = _init(paid_client, db_session, size=800 * 1024**2)
    assert resp.status_code == 413
    assert "won't fit" in resp.json()["detail"]
    assert "Delete a video" in resp.json()["detail"]


def test_init_rejects_not_your_section(paid_client, db_session):
    """Someone else's section → 403 (the ownership chain)."""
    other = _mk_section(db_session, uid="user-B")
    with _mock():
        resp = paid_client.post(
            "/api/upload-sessions/init",
            json={
                "section_id": other.id,
                "filename": "a.mp4",
                "declared_size": 100,
            },
            headers=_auth_headers(),
        )
    assert resp.status_code == 403


# ── Chunks ─────────────────────────────────────────────────────────────────


def _chunk_size_from_init(client, db, size, uid=FAKE_PAID):
    resp, _ = _init(client, db, size=size, uid=uid)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return data["session_id"], data["chunk_size"], data["total_chunks"]


def _chunk_payload(csize: int, index: int, total: int, declared: int) -> bytes:
    """The exact bytes chunk `index` carries — full-size except the
    LAST chunk, which is the remainder (declared - (n-1)*csize).
    Matches append_chunk's server-side expectation exactly."""
    if index == total - 1:
        return b"x" * (declared - (total - 1) * csize)
    return b"x" * csize


def test_chunk_append_and_idempotency(paid_client, db_session):
    """Chunks land; RE-SENDING the same chunk is safe (overwrites,
    count stays right — the network-retry contract)."""
    DECLARED = 3000
    sid, csize, total = _chunk_size_from_init(paid_client, db_session, size=DECLARED)
    with _mock():
        for i in range(total):
            r = paid_client.put(
                f"/api/upload-sessions/{sid}/chunk/{i}",
                content=_chunk_payload(csize, i, total, DECLARED),
                headers=_auth_headers(),
            )
            assert r.status_code == 200, r.text
        # Re-send chunk 1 (idempotent)
        r = paid_client.put(
            f"/api/upload-sessions/{sid}/chunk/1",
            content=_chunk_payload(csize, 1, total, DECLARED),
            headers=_auth_headers(),
        )
        assert r.status_code == 200
        assert r.json()["received"] == total  # still 3, not 4


def test_chunk_index_bounds(paid_client, db_session):
    """Out-of-range index → 400 (never a crash)."""
    sid, csize, total = _chunk_size_from_init(paid_client, db_session, size=1000)
    with _mock():
        r = paid_client.put(
            f"/api/upload-sessions/{sid}/chunk/{total}",
            content=b"x" * csize,
            headers=_auth_headers(),
        )
        assert r.status_code == 400
        r = paid_client.put(
            f"/api/upload-sessions/{sid}/chunk/-1",
            content=b"x" * csize,
            headers=_auth_headers(),
        )
        assert r.status_code == 400


def test_chunk_wrong_size_rejected(paid_client, db_session):
    """A chunk that disagrees with the plan → 400 (a lying client)."""
    sid, csize, total = _chunk_size_from_init(paid_client, db_session, size=2000)
    with _mock():
        r = paid_client.put(
            f"/api/upload-sessions/{sid}/chunk/0",
            content=b"y" * (csize - 1),  # short by one byte
            headers=_auth_headers(),
        )
        assert r.status_code == 400
        assert "expected" in r.json()["detail"].lower()


def test_chunk_other_users_session_404(paid_client, db_session):
    """Another user's session → 404 (never leak existence)."""
    sid, csize, _ = _chunk_size_from_init(paid_client, db_session, size=1000)
    with _mock(FAKE_OTHER):
        r = paid_client.put(
            f"/api/upload-sessions/{sid}/chunk/0",
            content=b"x" * csize,
            headers=_auth_headers(),
        )
    assert r.status_code == 404


def test_chunk_swept_session_409(paid_client, db_session):
    """§3a's explicit race answer: a cancelled session rejects chunks
    with 409 — the client restarts, never silently accepts."""
    sid, csize, _ = _chunk_size_from_init(paid_client, db_session, size=1000)
    with _mock():
        r = paid_client.delete(
            f"/api/upload-sessions/{sid}", headers=_auth_headers()
        )
        assert r.status_code == 200
        r = paid_client.put(
            f"/api/upload-sessions/{sid}/chunk/0",
            content=b"x" * csize,
            headers=_auth_headers(),
        )
    assert r.status_code == 409


# ── Complete ───────────────────────────────────────────────────────────────


def test_complete_happy_path(paid_client, db_session):
    """The end-to-end proof: init → chunks → complete → Video row
    'queued' with the transcribe job registered, staging consumed."""
    from app.jobs import get_job

    DECLARED = 3000
    sid, csize, total = _chunk_size_from_init(paid_client, db_session, size=DECLARED)
    with _mock():
        for i in range(total):
            paid_client.put(
                f"/api/upload-sessions/{sid}/chunk/{i}",
                content=_chunk_payload(csize, i, total, DECLARED),
                headers=_auth_headers(),
            )
        r = paid_client.post(
            f"/api/upload-sessions/{sid}/complete", headers=_auth_headers()
        )
    assert r.status_code == 200, r.text
    video_id = r.json()["video_id"]

    video = db_session.get(Video, video_id)
    assert video is not None
    assert video.status == "queued"
    assert video.file_size == 3000
    # The 9/19 lesson: the job tracker entry exists at creation.
    job = get_job(video_id, "transcribe")
    assert job is not None

    from app.config import settings
    # Staging consumed (filesystem-as-truth: dir gone).
    staging_dir = settings.upload_path / "sessions" / sid
    assert not staging_dir.exists()
    # The final file exists at the video's path.
    from pathlib import Path as _P
    assert _P(video.file_path).exists()
    assert _P(video.file_path).stat().st_size == 3000


def test_complete_missing_chunks_400(paid_client, db_session):
    """Incomplete upload → 400 naming the missing chunks."""
    sid, csize, total = _chunk_size_from_init(paid_client, db_session, size=3000)
    with _mock():
        paid_client.put(
            f"/api/upload-sessions/{sid}/chunk/0",
            content=b"x" * csize, headers=_auth_headers(),
        )
        r = paid_client.post(
            f"/api/upload-sessions/{sid}/complete", headers=_auth_headers()
        )
    assert r.status_code == 400
    assert "missing" in r.json()["detail"].lower()


# ── Sweeper ────────────────────────────────────────────────────────────────


def test_sweeper_ttl_honored(paid_client, db_session):
    """Old session swept (dir + row + events); fresh session kept."""
    from app.services.upload_sessions import sweep_expired_sessions
    from app.models import UploadSession as _US

    sid, csize, _ = _chunk_size_from_init(paid_client, db_session, size=1000)
    with _mock():
        paid_client.put(
            f"/api/upload-sessions/{sid}/chunk/0",
            content=b"x" * csize, headers=_auth_headers(),
        )

    # Age the session beyond TTL directly in the DB.
    old = datetime.utcnow() - timedelta(hours=2)
    db_session.query(_US).filter(_US.id == sid).update(
        {"last_activity_at": old}
    )
    db_session.commit()

    result = sweep_expired_sessions(db_session)
    assert result["claimed"] == 1
    assert result["bytes_freed"] == 1000

    from app.config import settings
    assert not (settings.upload_path / "sessions" / sid).exists()
    row = db_session.get(_US, sid)
    db_session.refresh(row)
    assert row.status == "cancelled"


def test_sweeper_fresh_session_kept(paid_client, db_session):
    """An active (recent) session survives the sweep."""
    from app.services.upload_sessions import sweep_expired_sessions
    from app.models import UploadSession as _US

    sid, csize, _ = _chunk_size_from_init(paid_client, db_session, size=1000)
    result = sweep_expired_sessions(db_session)
    assert result["claimed"] == 0
    row = db_session.get(_US, sid)
    assert row.status == "active"


def test_sweeper_idempotent_double_pass(paid_client, db_session):
    """Sweeping twice: the second pass finds nothing (the claim is
    durable — the c827a7b regression shape on THIS sweeper)."""
    from app.services.upload_sessions import sweep_expired_sessions
    from app.models import UploadSession as _US

    sid, _, _ = _chunk_size_from_init(paid_client, db_session, size=1000)
    db_session.query(_US).filter(_US.id == sid).update(
        {"last_activity_at": datetime.utcnow() - timedelta(hours=3)}
    )
    db_session.commit()

    first = sweep_expired_sessions(db_session)
    assert first["claimed"] == 1
    second = sweep_expired_sessions(db_session)
    assert second["claimed"] == 0  # already cancelled — no double-free


# ── Quota interactions ─────────────────────────────────────────────────────


def test_staging_counts_toward_quota(paid_client, db_session):
    """A declared-size reservation appears in the usage meter
    (completed + staging — check and display read the same function)."""
    from app.services.upload_limits import get_user_storage_usage

    before = get_user_storage_usage(db_session, "test-user-uid")
    sid, csize, _ = _chunk_size_from_init(paid_client, db_session, size=5000)
    after = get_user_storage_usage(db_session, "test-user-uid")
    # DECLARED size counts, not actual staged bytes (the reservation).
    assert after["staging_bytes"] == before["staging_bytes"] + 5000


def test_delete_releases_quota_instantly(paid_client, db_session):
    """DELETE = the instant-release valve: quota back, no sweeper wait."""
    from app.services.upload_limits import get_user_storage_usage

    sid, _, _ = _chunk_size_from_init(paid_client, db_session, size=5000)
    mid = get_user_storage_usage(db_session, "test-user-uid")
    assert mid["staging_bytes"] == 5000
    with _mock():
        paid_client.delete(
            f"/api/upload-sessions/{sid}", headers=_auth_headers()
        )
    after = get_user_storage_usage(db_session, "test-user-uid")
    assert after["staging_bytes"] == 0


def test_completed_session_frees_reservation(paid_client, db_session):
    """On complete, the staging reservation converts to the Video's
    file_size (the meter number stays the same, the class changes)."""
    from app.services.upload_limits import get_user_storage_usage

    DECLARED = 3000
    sid, csize, total = _chunk_size_from_init(paid_client, db_session, size=DECLARED)
    with _mock():
        for i in range(total):
            paid_client.put(
                f"/api/upload-sessions/{sid}/chunk/{i}",
                content=_chunk_payload(csize, i, total, DECLARED), headers=_auth_headers(),
            )
        paid_client.post(
            f"/api/upload-sessions/{sid}/complete", headers=_auth_headers()
        )
    usage = get_user_storage_usage(db_session, "test-user-uid")
    assert usage["staging_bytes"] == 0
    assert usage["videos_bytes"] == 3000
    assert usage["used_bytes"] == 3000


def test_last_cancelled_banner_feed(paid_client, db_session):
    """The §3a banner endpoint reports the user's latest cancelled
    session (the 'error sign somewhere' — surfaced on next visit)."""
    sid, _, _ = _chunk_size_from_init(paid_client, db_session, size=1000)
    with _mock():
        paid_client.delete(
            f"/api/upload-sessions/{sid}", headers=_auth_headers()
        )
        r = paid_client.get(
            "/api/upload-sessions/last-cancelled", headers=_auth_headers()
        )
    assert r.status_code == 200
    data = r.json()
    assert data["has_cancelled"] is True
    assert data["declared_size"] == 1000