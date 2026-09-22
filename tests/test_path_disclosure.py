"""Path-disclosure hardening tests (2026-09-22).

Backstory — the beta screenshot: a PAID user's Tools tab showed
  "You can find the new file at:
   /Volumes/Storage-Medium-NVMe/video-app/uploads/<uuid>.mp4"
plus an "Open in Finder" button. Two problems:
  1. Absolute paths leak the server's volume layout + upload-dir
     pattern (recon material for path-traversal probing).
  2. "Open in Finder" pops Finder on the SERVER machine —
     meaningless to a remote user.

The 2026-09-22 hardening (app/routers/plugins.py +
app/services/plugins.py + app/routers/frontend.py +
app/templates/video.html):

  ADMIN  → full paths everywhere (they administer the machine)
  PAID   → filename only; output_path null; extra omitted;
           reveal endpoint 403s; swap resolves server-side
  FREE   → the Tools tab is already role-gated (unchanged)

Pinned here:
  1. _sanitize_run_for_role: admin keeps output_path/extra;
     non-admin gets output_path=None + filename + extra=None.
  2. Both runs endpoints apply the sanitizer.
  3. The webm_to_mp4 success message carries NO absolute path.
  4. Swap endpoint: no mp4_path needed (server-side lookup);
     409 when no successful conversion exists; PAID response
     has no new_path; admin's explicit path still honored.
  5. Reveal endpoint: PAID → 403 (admin-only now); the 403
     message must NOT contain the allowed-roots list.
  6. The one-time scrub migration: strips the path sentence
     from legacy plugin_runs.message rows; idempotent.
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


def _seed_roles(db: Session) -> None:
    """Insert users rows for the fake UIDs so require_capability's
    DB-backed role lookup resolves ADMIN/PAID (not FREE→403). The
    conftest client patches verify_token per-test; the role comes
    from the users table, so both rows must exist."""
    from sqlalchemy import text

    from app.auth.admin import clear_role_cache

    db.execute(
        text("INSERT OR REPLACE INTO users (user_id, email, role) "
             "VALUES ('admin-uid', 'admin@example.com', 0)")
    )
    db.execute(
        text("INSERT OR REPLACE INTO users (user_id, email, role) "
             "VALUES ('paid-uid', 'paid@example.com', 1)")
    )
    db.commit()
    clear_role_cache()


def _admin_auth():
    return patch(
        "app.auth.dependencies.verify_token", return_value=FAKE_ADMIN
    )


def _paid_auth():
    return patch(
        "app.auth.dependencies.verify_token", return_value=FAKE_PAID
    )


def _make_video(db: Session, vid: str = "v1", status: str = "ready") -> None:
    """Minimal owned video row (the endpoints don't check course
    ownership, but a real video row is required for the swap)."""
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video

    db.add(Course(id="c1", user_id="owner-uid", title="T"))
    db.add(Section(id="s1", course_id="c1", title="S"))
    db.add(
        Video(
            id=vid,
            section_id="s1",
            title="Test video",
            file_path="/uploads/lesson.webm",
            filename="lesson.webm",
            status=status,
        )
    )
    db.commit()


def _make_run(
    db: Session,
    vid: str = "v1",
    ok: bool = True,
    output_path: str | None = "/uploads/lesson.mp4",
    message: str = "Transcoded to MP4 (12.0 MB).",
) -> str:
    from app.models.plugin_run import PluginRun

    run = PluginRun(
        id="run-" + str(abs(hash(output_path)) % 10**8),
        video_id=vid,
        plugin_key="webm_to_mp4",
        ok=ok,
        status="done",
        message=message,
        output_path=output_path,
    )
    db.add(run)
    db.commit()
    return run.id


# ─────────────────────────────────────────────────────────────────────────────
# 1. The sanitizer helper
# ─────────────────────────────────────────────────────────────────────────────

def test_sanitizer_admin_keeps_paths():
    from app.models.plugin_run import PluginRun
    from app.routers.plugins import _sanitize_run_for_role

    run = PluginRun(
        id="r1", video_id="v1", plugin_key="webm_to_mp4",
        ok=True, status="done", message="m",
        output_path="/uploads/x.mp4", extra_json='{"old_path": "/uploads/x.webm"}',
        created_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    out = _sanitize_run_for_role(run, FAKE_ADMIN)
    assert out["output_path"] == "/uploads/x.mp4"
    assert out["extra"] == '{"old_path": "/uploads/x.webm"}'
    assert "filename" not in out  # admin shape unchanged


def test_sanitizer_non_admin_gets_filename_only():
    from app.models.plugin_run import PluginRun
    from app.routers.plugins import _sanitize_run_for_role

    run = PluginRun(
        id="r2", video_id="v1", plugin_key="webm_to_mp4",
        ok=True, status="done", message="m",
        output_path="/Volumes/Storage-Medium-NVMe/video-app/uploads/x.mp4",
        extra_json='{"old_path": "/Volumes/Storage-Medium-NVMe/x.webm"}',
        created_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    for user in (FAKE_PAID, {"uid": "u", "role": 2}, {}):
        out = _sanitize_run_for_role(run, user)
        assert out["output_path"] is None, "non-admin must NOT see the path"
        assert out["extra"] is None, "non-admin must NOT see extra (paths inside)"
        assert out["filename"] == "x.mp4"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Both runs endpoints sanitize
# ─────────────────────────────────────────────────────────────────────────────

def test_get_run_admin_sees_path(paid_and_admin_clients, db_session):
    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    _make_video(db_session)
    run_id = _make_run(db_session)

    with _admin_auth():
        r = admin_client.get(f"/api/plugins/runs/{run_id}")
    assert r.status_code == 200
    assert r.json()["output_path"] == "/uploads/lesson.mp4"


def test_get_run_paid_sees_filename_only(paid_and_admin_clients, db_session):
    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    _make_video(db_session)
    run_id = _make_run(db_session, output_path="/uploads/lesson.mp4")

    with _paid_auth():
        r = paid_client.get(f"/api/plugins/runs/{run_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["output_path"] is None
    assert body["filename"] == "lesson.mp4"
    assert body["extra"] is None


def test_by_video_paid_sees_filename_only(paid_and_admin_clients, db_session):
    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    _make_video(db_session)
    _make_run(db_session)

    with _paid_auth():
        r = paid_client.get("/api/plugins/runs/by-video/v1")
    assert r.status_code == 200
    run = r.json()["run"]
    assert run["output_path"] is None
    assert run["filename"] == "lesson.mp4"


def test_by_video_admin_sees_full_path(paid_and_admin_clients, db_session):
    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    _make_video(db_session)
    _make_run(db_session)

    with _admin_auth():
        r = admin_client.get("/api/plugins/runs/by-video/v1")
    run = r.json()["run"]
    assert run["output_path"] == "/uploads/lesson.mp4"


# ─────────────────────────────────────────────────────────────────────────────
# 3. The success message carries no path
# ─────────────────────────────────────────────────────────────────────────────

def test_webm_to_mp4_message_has_no_path(tmp_path, monkeypatch, db_session):
    """The stored PluginRun.message must not embed absolute paths —
    messages are echoed to every role via the runs endpoints."""
    from app.models.video import Video
    from app.services.plugins import run_plugin

    webm = tmp_path / "lesson.webm"
    webm.write_bytes(b"x")

    class FakeVideo:
        id = "v1"
        file_path = str(webm)
        filename = "lesson.webm"
        status = "ready"
        section_id = "s1"

    # Stub ffmpeg: create the dst file, succeed
    def fake_run(cmd, **kwargs):
        # cmd[-1] is the dst path
        Path(cmd[-1]).write_bytes(b"fake mp4")
        class P:
            returncode = 0
            stderr = ""
        return P()

    monkeypatch.setattr("app.services.plugins.subprocess.run", fake_run)
    result, run_row = run_plugin("webm_to_mp4", FakeVideo(), db_session)

    assert result.ok is True
    assert "/uploads" not in result.message
    assert str(tmp_path) not in result.message
    assert "You can find the new file" not in result.message
    # The path lives in output_path (sanitized per-role), not the message:
    assert result.output_path is not None


# ─────────────────────────────────────────────────────────────────────────────
# 4. Swap endpoint: path-free flow
# ─────────────────────────────────────────────────────────────────────────────

def test_swap_without_path_uses_latest_run(paid_and_admin_clients, db_session, tmp_path, monkeypatch):
    """PAID swap with NO mp4_path in the body — the server resolves the
    path from the latest successful webm_to_mp4 run."""
    from app.config import settings

    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    _make_video(db_session)
    mp4 = tmp_path / "lesson.mp4"
    mp4.write_bytes(b"fake mp4")
    _make_run(db_session, output_path=str(mp4))

    with _paid_auth():
        r = paid_client.post(
            "/api/plugins/swap-to-mp4",
            json={"video_id": "v1"},   # NO mp4_path
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["new_filename"] == "lesson.mp4"
    # PAID must not get the new absolute path back
    assert "new_path" not in body


def test_swap_without_any_run_409s(paid_and_admin_clients, db_session):
    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    _make_video(db_session)

    with _paid_auth():
        r = paid_client.post(
            "/api/plugins/swap-to-mp4", json={"video_id": "v1"}
        )
    assert r.status_code == 409
    assert "No completed MP4 conversion" in r.json()["detail"]


def test_swap_admin_path_override_still_works(paid_and_admin_clients, db_session, tmp_path, monkeypatch):
    """Admin may still pass an explicit path (backward compat + their
    machine)."""
    from app.config import settings

    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    _make_video(db_session)
    mp4 = tmp_path / "lesson.mp4"
    mp4.write_bytes(b"fake mp4")

    with _admin_auth():
        r = admin_client.post(
            "/api/plugins/swap-to-mp4",
            json={"video_id": "v1", "mp4_path": str(mp4)},
        )
    assert r.status_code == 200
    assert r.json()["new_path"] == str(mp4.resolve())


def test_swap_paid_cannot_override_path(paid_and_admin_clients, db_session, tmp_path, monkeypatch):
    """A non-admin sending mp4_path is IGNORED — otherwise the path-free
    design would be bypassable by replaying the old client."""
    from app.config import settings

    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    _make_video(db_session)
    mp4 = tmp_path / "lesson.mp4"
    mp4.write_bytes(b"fake mp4")
    # No run row → a body path must NOT be honored for PAID; the
    # request must 409 (no conversion to swap to), proving the body
    # path didn't reach the service.
    with _paid_auth():
        r = paid_client.post(
            "/api/plugins/swap-to-mp4",
            json={"video_id": "v1", "mp4_path": str(mp4)},
        )
    assert r.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# 5. Reveal endpoint: admin-only + generic 403
# ─────────────────────────────────────────────────────────────────────────────

def test_reveal_paid_gets_403(paid_and_admin_clients, db_session):
    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    with _paid_auth():
        r = paid_client.post(
            "/api/plugins/reveal",
            json={"path": "/etc/passwd"},
        )
    assert r.status_code == 403


def test_reveal_403_does_not_leak_allowed_roots(paid_and_admin_clients, db_session, tmp_path, monkeypatch):
    """The out-of-root 403 detail must not enumerate the server's
    directories. Admin hits the same check (they can probe, but the
    message stays generic — simpler code, no layout echo)."""
    from app.config import settings

    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))

    with _admin_auth():
        r = admin_client.post(
            "/api/plugins/reveal",
            json={"path": "/etc/passwd"},
        )
    assert r.status_code == 403
    assert "Allowed roots" not in r.json()["detail"]
    assert "/Volumes/" not in r.json()["detail"]


def test_reveal_admin_can_still_reveal(paid_and_admin_clients, db_session, tmp_path, monkeypatch):
    from app.config import settings

    paid_client, admin_client = paid_and_admin_clients
    _seed_roles(db_session)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    f = tmp_path / "in-root.txt"
    f.write_bytes(b"x")

    with patch("app.routers.plugins.subprocess.run") as fake_run:
        fake_run.return_value.returncode = 0
        with _admin_auth():
            r = admin_client.post(
                "/api/plugins/reveal", json={"path": str(f)}
            )
    assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 6. The scrub migration
# ─────────────────────────────────────────────────────────────────────────────

def test_scrub_strips_legacy_path_sentence(tmp_path):
    """A row with the old 'You can find the new file at: <path>' sentence
    gets the sentence removed; size/MB info preserved."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.database import _scrub_plugin_run_message_paths
    from app.models.plugin_run import PluginRun

    eng = create_engine(
        f"sqlite:///{tmp_path}/scrub.db",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=eng)
    Sess = sessionmaker(bind=eng)
    db = Sess()
    db.add(PluginRun(
        id="r1", video_id="v1", plugin_key="webm_to_mp4",
        ok=True, status="done",
        message=(
            "Transcoded to MP4 (54.3 MB). Original WebM is untouched. "
            "You can find the new file at: /Volumes/Storage-Medium-NVMe/x.mp4"
        ),
        output_path="/Volumes/Storage-Medium-NVMe/x.mp4",
    ))
    db.add(PluginRun(
        id="r2", video_id="v1", plugin_key="webm_to_mp4",
        ok=True, status="done",
        message="Transcoded to MP4 (10.0 MB). Original file is untouched.",
        output_path="/uploads/y.mp4",
    ))
    db.commit()

    # Rebind the module's engine to our scratch DB and run the scrub
    import app.database as db_mod
    original_engine = db_mod.engine
    db_mod.engine = eng
    try:
        _scrub_plugin_run_message_paths()
        db.expire_all()
        r1 = db.get(PluginRun, "r1")
        r2 = db.get(PluginRun, "r2")
        # Path sentence gone, size kept
        assert "You can find" not in r1.message
        assert "/Volumes/" not in r1.message
        assert "54.3 MB" in r1.message
        # The clean row is untouched
        assert r2.message == "Transcoded to MP4 (10.0 MB). Original file is untouched."
        # output_path column itself is UNTOUCHED (admin still sees it)
        assert r1.output_path == "/Volumes/Storage-Medium-NVMe/x.mp4"

        # Idempotent: run again → same result, no error
        _scrub_plugin_run_message_paths()
        db.expire_all()
        assert "You can find" not in db.get(PluginRun, "r1").message
    finally:
        db_mod.engine = original_engine
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# 7. Template-source wiring (house pattern)
# ─────────────────────────────────────────────────────────────────────────────

def test_video_template_gates_finder_and_path_by_role():
    """video.html must render the path/Finder branch only when
    last_run.output_path is truthy (admin), and a filename branch
    otherwise. Both the Jinja box and the JS twin are pinned."""
    src = Path("app/templates/video.html").read_text()
    # The admin branch keys on output_path presence…
    assert "{% if last_run.output_path %}" in src
    # …and the non-admin branch shows filename…
    assert "data-output-filename" in src
    # …the swap button no longer carries a path argument…
    assert "confirmSwapToMp4('{{ plugin.key }}', '" not in src
    # …performSwap posts only video_id…
    assert "mp4_path: _swapMp4Path" not in src
    # …and the old path-storing state is gone.
    assert "_swapMp4Path" not in src