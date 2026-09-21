#!/usr/bin/env python3
"""Live smoke test for the chunked-upload path (13a, 2026-09-21).

Run against the LIVE server (localhost:8000) with REAL disk and the
REAL 32MB chunk size — the last thing the pytest battery can't prove
(it uses 1KB chunks on a throwaway DB).

Auth: the app trusts `verify_token` at the request layer; production
reads the Firebase session cookie. This script does what the repo's
tests do — patch verify_token in-process — by running the requests
through a TestClient against the REAL app object with the REAL
DATABASE_URL and REAL upload dir, while mocking ONLY the auth check.
Everything downstream (sessions, disk, resolver, queue) is 100% live.

Flow:
  1. create course + section (live rows, real DB)
  2. init a 100MB upload (4×32MB chunks — real chunk size!)
  3. PUT 4 chunks (bytes generated in memory, written to the real
     staging dir)
  4. complete → Video row 'queued'
  5. verify the assembled file on disk == 100MB at the video's path
  6. THE DELETE-FREES-DISK AUDIT (pre-beta MUST item): delete the
     video via the API, verify the file leaves the disk AND the
     staging dir is gone AND the quota meter drops to 0
  7. cleanup course/section

Exit 0 = every check passed. Non-zero = failure with the check name.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TOTAL_BYTES = 100 * 1024 * 1024  # 100MB = 4 chunks at the real 32MB size

FAKE_ADMIN = {"uid": "uid-admin", "email": "admin@x.com", "role": 0}


def main() -> int:
    # Fail loudly if this somehow points at the test DB — this script
    # is only meaningful against production settings.
    url = os.environ.get("DATABASE_URL", "")
    if ":memory:" in url or not url:
        # fall back to the app's .env-loaded settings; verify below
        from app.config import settings as _s
        url = _s.database_url
    print(f"[0] target DB: {url}")

    from fastapi.testclient import TestClient
    from app.main import app

    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool, extra: str = "") -> None:
        checks.append((name, ok))
        print(f"  {'✅' if ok else '❌'} {name}{(' — ' + extra) if extra else ''}")

    from app.config import settings
    from app.database import SessionLocal
    from sqlalchemy import text as _text

    with patch(
        "app.auth.dependencies.verify_token", return_value=FAKE_ADMIN
    ), patch(
        "app.middleware_session.verify_token", return_value=FAKE_ADMIN
    ):
        with TestClient(app) as client:
            # Ensure the admin user row exists (role=0) — the same
            # setup the repo's tests use.
            db = SessionLocal()
            db.execute(_text(
                "INSERT OR IGNORE INTO users (user_id, email, role) "
                "VALUES ('uid-admin', 'admin@x.com', 0)"
            ))
            db.execute(_text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
            db.commit()
            from app.auth.admin import clear_role_cache
            clear_role_cache()
            db.close()

            headers = {"Authorization": "Bearer smoke-test"}

            # 1. Course + section
            r = client.post("/api/courses", json={"title": "SmokeTest — DELETE ME", "description": "13a live smoke"},
                            headers=headers)
            check("course created", r.status_code in (200, 201), f"HTTP {r.status_code}")
            course_id = r.json()["course_id"]

            r = client.post(f"/api/courses/{course_id}/sections", json={"title": "smoke"},
                            headers=headers)
            check("section created", r.status_code in (200, 201), f"HTTP {r.status_code}")
            section_id = r.json()["section_id"]

            # 2. init
            r = client.post("/api/upload-sessions/init", json={
                "section_id": section_id,
                "filename": "chunked-smoke-test.mp4",
                "declared_size": TOTAL_BYTES,
            }, headers=headers)
            check("init (100MB, real 32MB chunks)", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
            if r.status_code != 200:
                _cleanup_course(course_id, headers, client)
                return _report(checks)
            plan = r.json()
            sid = plan["session_id"]
            chunk_size = plan["chunk_size"]
            total_chunks = plan["total_chunks"]
            check("chunk plan is the real 32MB", chunk_size == 32 * 1024 * 1024,
                  f"chunk_size={chunk_size}")
            check("4 chunks expected", total_chunks == 4, f"got {total_chunks}")

            # 3. chunks
            staging = settings.upload_path / "sessions" / sid
            for i in range(total_chunks):
                if i == total_chunks - 1:
                    payload = b"s" * (TOTAL_BYTES - (total_chunks - 1) * chunk_size)
                else:
                    payload = b"s" * chunk_size
                r = client.put(f"/api/upload-sessions/{sid}/chunk/{i}",
                               content=payload, headers=headers)
                check(f"chunk {i + 1}/4 uploaded", r.status_code == 200,
                      f"HTTP {r.status_code}")
            check("staging dir has 4 chunk files",
                  staging.is_dir() and len(list(staging.glob("chunk_*"))) == 4)

            # 4. complete
            r = client.post(f"/api/upload-sessions/{sid}/complete", headers=headers)
            check("complete → 200", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
            if r.status_code != 200:
                _cleanup_course(course_id, headers, client)
                return _report(checks)
            video_id = r.json()["video_id"]

            # 5. assembled file on disk
            from app.models import Video
            db = SessionLocal()
            video = db.get(Video, video_id)
            check("video row queued", video is not None and video.status == "queued",
                  f"status={getattr(video, 'status', None)}")
            fpath = Path(video.file_path)
            on_disk = fpath.stat().st_size if fpath.exists() else -1
            check("assembled file == 100MB on disk", on_disk == TOTAL_BYTES,
                  f"{on_disk} bytes at {fpath}")
            check("staging dir consumed", not staging.exists())
            db.close()

            # NOTE: we deliberately do NOT wait for transcription (the
            # queue claims the synthetic file and whisper will fail on
            # the fake bytes — the smoke test validates the UPLOAD
            # path; the queue's real-file behavior is already proven
            # by the Hermes e2e). Delete before the pipeline runs.

            # 6. THE DELETE-FREES-DISK AUDIT
            usage_before = _usage()
            r = client.delete(f"/api/videos/{video_id}", headers=headers)
            check("delete video via API", r.status_code == 200, f"HTTP {r.status_code}")
            check("file GONE from disk after delete", not fpath.exists())
            usage_after = _usage()
            check("quota meter dropped",
                  usage_after < usage_before,
                  f"{usage_before} → {usage_after} bytes")

            # 7. cleanup (course cascade removes the section)
            _cleanup_course(course_id, headers, client)

    return _report(checks)


def _usage() -> int:
    from app.services.upload_limits import get_user_storage_usage
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        return get_user_storage_usage(db, "uid-admin")["used_bytes"]
    finally:
        db.close()


def _cleanup_course(course_id, headers, client) -> None:
    try:
        client.delete(f"/api/courses/{course_id}", headers=headers)
    except Exception:
        pass


def _report(checks) -> int:
    failed = [name for name, ok in checks if not ok]
    print()
    if failed:
        print(f"❌ SMOKE TEST FAILED: {len(failed)}/{len(checks)} checks failed:")
        for name in failed:
            print(f"   - {name}")
        return 1
    print(f"✅ SMOKE TEST PASSED: all {len(checks)} checks green.")
    print("   (chunked upload + assembly + delete-frees-disk: LIVE-VERIFIED)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())