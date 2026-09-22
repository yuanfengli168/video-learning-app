"""Storage-doubling fix tests (2026-09-22, Todo Q5/Q6 ratified).

Backstory — the swap flow left BOTH files on disk (original WebM +
converted MP4) while the /usage meter sums `videos.file_size` (one
row = one file). A PAID user who swapped a 1GB WebM saw "1GB used"
while actually consuming 2GB — the disk would hit the wall long
before the meter turned red.

The fix: `swap_video_file_to(..., delete_original=...)` —
  - non-admin (router passes role != 0 → True): the ORIGINAL file
    is deleted after the DB commit (row points at the new file
    first; a failed delete never fails the swap — it's a sweeper-
    class cleanup, never data loss)
  - admin (role == 0 → False): keeps both, same as always

Also pinned: the audit row records `original_deleted` +
`storage_freed_bytes`, the user-facing message says the original
was deleted, and the modal/card template text is role-conditional.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

FAKE_ADMIN = {"uid": "admin-uid", "email": "admin@example.com", "role": 0}
FAKE_PAID = {"uid": "paid-uid", "email": "paid@example.com", "role": 1}


def _admin_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_ADMIN)


def _paid_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_PAID)


def _seed_roles(db: Session) -> None:
    from app.auth.admin import clear_role_cache

    db.execute(text(
        "INSERT OR REPLACE INTO users (user_id, email, role) "
        "VALUES ('admin-uid', 'admin@example.com', 0)"))
    db.execute(text(
        "INSERT OR REPLACE INTO users (user_id, email, role) "
        "VALUES ('paid-uid', 'paid@example.com', 1)"))
    db.commit()
    clear_role_cache()


def _make_video(db: Session, file_path: str) -> None:
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video

    db.add(Course(id="c1", user_id="owner-uid", title="T"))
    db.add(Section(id="s1", course_id="c1", title="S"))
    db.add(Video(
        id="v1", section_id="s1", title="Test video",
        file_path=file_path, filename=Path(file_path).name,
        status="ready", file_size=1000,
    ))
    db.commit()


def _make_run(db: Session, output_path: str) -> None:
    from app.models.plugin_run import PluginRun

    db.add(PluginRun(
        id="run-1", video_id="v1", plugin_key="webm_to_mp4",
        ok=True, status="done",
        message="Transcoded to MP4 (1.0 MB). Original file is untouched.",
        output_path=output_path,
    ))
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# 1. PAID swap deletes the original (the storage fix)
# ─────────────────────────────────────────────────────────────────────────────

def test_paid_swap_deletes_original(paid_and_admin_clients, db_session, tmp_path, monkeypatch):
    """PAID: after the swap, the old file is GONE, the new file exists,
    and the response message says so."""
    from app.config import settings

    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    webm = tmp_path / "lesson.webm"
    webm.write_bytes(b"original bytes")
    mp4 = tmp_path / "lesson.mp4"
    mp4.write_bytes(b"converted bytes")
    _make_video(db_session, str(webm))
    _make_run(db_session, str(mp4))

    with _paid_auth():
        r = paid_client.post("/api/plugins/swap-to-mp4",
                            json={"video_id": "v1"})
    assert r.status_code == 200
    assert "deleted" in r.json()["message"].lower()
    # THE assertion of this whole fix:
    assert not webm.exists(), "PAID swap must delete the original file"
    assert mp4.exists()
    # DB row points at the new file with the new size
    from app.models.video import Video
    db_session.expire_all()
    v = db_session.get(Video, "v1")
    assert v.file_path == str(mp4.resolve())
    assert v.filename == "lesson.mp4"
    assert v.file_size == len(b"converted bytes")


def test_admin_swap_keeps_original(paid_and_admin_clients, db_session, tmp_path, monkeypatch):
    """ADMIN: both files remain (their machine, their choice)."""
    from app.config import settings

    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    webm = tmp_path / "lesson.webm"
    webm.write_bytes(b"original bytes")
    mp4 = tmp_path / "lesson.mp4"
    mp4.write_bytes(b"converted bytes")
    _make_video(db_session, str(webm))
    _make_run(db_session, str(mp4))

    with _admin_auth():
        r = admin_client.post("/api/plugins/swap-to-mp4",
                              json={"video_id": "v1", "mp4_path": str(mp4)})
    assert r.status_code == 200
    assert webm.exists(), "ADMIN swap must keep the original"
    assert mp4.exists()
    assert "deleted" not in r.json()["message"].lower()


def test_swap_audit_row_records_deletion(paid_and_admin_clients, db_session, tmp_path, monkeypatch):
    """The PluginRun audit row carries original_deleted + storage_freed_bytes
    for non-admin (accountability: what bytes were reclaimed, when)."""
    from app.config import settings

    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    webm = tmp_path / "lesson.webm"
    webm.write_bytes(b"x" * 1000)
    mp4 = tmp_path / "lesson.mp4"
    mp4.write_bytes(b"y" * 100)
    _make_video(db_session, str(webm))
    _make_run(db_session, str(mp4))

    with _paid_auth():
        paid_client.post("/api/plugins/swap-to-mp4", json={"video_id": "v1"})

    from app.models.plugin_run import PluginRun
    import json as _json
    row = (db_session.query(PluginRun)
           .filter(PluginRun.plugin_key == "swap_to_mp4")
           .order_by(PluginRun.created_at.desc()).first())
    extra = _json.loads(row.extra_json)
    assert extra["original_deleted"] is True
    assert extra["storage_freed_bytes"] == 1000  # the old file's bytes
    assert "Original deleted" in row.message


def test_failed_delete_never_fails_swap(paid_and_admin_clients, db_session, tmp_path, monkeypatch):
    """If unlink raises (permissions, volume gone), the swap STILL
    succeeds — the row already points at the new file; the orphaned
    original is sweeper-class cleanup, not data loss."""
    from app.config import settings

    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    webm = tmp_path / "lesson.webm"
    webm.write_bytes(b"x")
    mp4 = tmp_path / "lesson.mp4"
    mp4.write_bytes(b"y")
    _make_video(db_session, str(webm))
    _make_run(db_session, str(mp4))

    def boom(*a, **kw):
        raise OSError("simulated permission denial")

    # unlink is called on the Path instance — patch at class level
    with patch.object(Path, "unlink", boom):
        with _paid_auth():
            r = paid_client.post("/api/plugins/swap-to-mp4",
                                 json={"video_id": "v1"})
    assert r.status_code == 200
    # Message must NOT claim deletion
    assert "deleted" not in r.json()["message"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# 2. Template text is role-conditional
# ─────────────────────────────────────────────────────────────────────────────

def test_modal_text_role_conditional():
    """video.html: the 'original NOT deleted' paragraph renders for
    ADMIN; the 'will be deleted' warning for everyone else. Both
    branches must exist (Jinja can't branch on a missing var)."""
    src = Path("app/templates/video.html").read_text()
    # The admin branch
    assert "The original file is NOT deleted" in src
    # The non-admin branch (the new warning)
    assert "will be deleted immediately after" in src
    # And the branch is keyed on the role
    assert "{% if user_role == 'ADMIN' %}" in src


def test_tools_card_description_role_conditional():
    """The Tools-tab card no longer unconditionally says 'your original
    is never modified' — for non-admin it explains the swap deletes the
    original."""
    src = Path("app/templates/video.html").read_text()
    assert "original is deleted to keep your storage" in src
    # The admin branch keeps the old wording inside the conditional
    assert "your original is never modified" in src


# ─────────────────────────────────────────────────────────────────────────────
# 3. Service-level unit check of the delete order
# ─────────────────────────────────────────────────────────────────────────────

def test_delete_happens_after_commit(monkeypatch, db_session, tmp_path):
    """The commit must precede the unlink — if we crash between them,
    the row already points at the new file (orphaned original is
    cleanup; the reverse order would leave a row pointing at a
    deleted file = actual data loss)."""
    from app.models.video import Video
    from app.services.plugins import swap_video_file_to

    webm = tmp_path / "lesson.webm"
    webm.write_bytes(b"x")
    mp4 = tmp_path / "lesson.mp4"
    mp4.write_bytes(b"y")

    # _make_video equivalent, minimal
    from app.models.course import Course
    from app.models.section import Section
    db_session.add_all([
        Course(id="c1", user_id="u", title="T"),
        Section(id="s1", course_id="c1", title="S"),
        Video(id="v1", section_id="s1", title="T",
              file_path=str(webm), filename="lesson.webm",
              status="ready", file_size=1),
    ])
    db_session.commit()

    calls: list[str] = []
    real_commit = db_session.commit
    monkeypatch.setattr(db_session, "commit", lambda: (calls.append("commit"), real_commit())[1])
    real_unlink = Path.unlink
    def spy_unlink(self, *a, **kw):
        calls.append("unlink")
        return real_unlink(self, *a, **kw)
    monkeypatch.setattr(Path, "unlink", spy_unlink)

    video = db_session.get(Video, "v1")
    result = swap_video_file_to(video, str(mp4), db_session, delete_original=True)

    assert result.ok is True
    # THE order assertion:
    assert calls[0] == "commit"
    assert "unlink" in calls
    assert not webm.exists()