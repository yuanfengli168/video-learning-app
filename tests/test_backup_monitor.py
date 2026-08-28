"""Tests for the Day 10 backup monitoring subsystem.

Covers:
- backup_monitor module (collect_status, write_status_file, dedupe, etc.)
- /admin/backups page (renders, gated by ADMIN, shows status)
- /admin/backups/run (POST runs the script)
- /api/admin/backup-status (machine-readable JSON)
- /api/admin/data-freshness (per-table MAX(updated_at))
- /api/ready enhancement (now reports backup + integrity)

Day 10 incident (2026-08-28): prod DB wiped + backups silently failing
since Aug 22. These tests document the new monitoring surface so a
regression here would be caught in CI.
"""

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.backup_monitor import (
    BackupFile,
    BackupStatus,
    LaunchdJobStatus,
    STATUS_PATH,
    VolumeStatus,
    collect_status,
    probe_backup_files,
    probe_launchd_job,
    probe_raid_volume,
    read_status_file,
    write_status_file,
)


# ── Pure-logic tests (no I/O) ──────────────────────────────────────────


def test_backup_status_to_from_json_roundtrip():
    """Status roundtrips through JSON without losing fields."""
    s = BackupStatus(
        probe_ts=1234567890.0,
        probe_ts_iso="2026-08-28T00:00:00+0000",
        jobs=[LaunchdJobStatus(label="com.videoapp.backup-db", state="waiting", last_exit_code=0, is_healthy=True)],
        files=[BackupFile(path="/x/y.sqlite3", kind="db-hot", size_bytes=1024, mtime_ts=1234567890.0, age_hours=1.0)],
        volume=VolumeStatus(mount="/m", free_bytes=10**9, total_bytes=10**10, used_bytes=9*10**9, free_gb=0.93, is_low=False),
        is_healthy=True,
        reasons=[],
    )
    blob = s.to_json()
    s2 = BackupStatus.from_json(blob)
    assert s2.probe_ts == s.probe_ts
    assert s2.is_healthy is True
    assert len(s2.jobs) == 1
    assert s2.jobs[0].label == "com.videoapp.backup-db"
    assert s2.jobs[0].is_healthy is True
    assert s2.files[0].path == "/x/y.sqlite3"
    assert s2.volume is not None
    assert s2.volume.free_gb == 0.93


def test_backup_status_handles_null_volume():
    """VolumeStatus is optional (RAID not mounted case)."""
    s = BackupStatus(
        probe_ts=1234.0,
        probe_ts_iso="2026-08-28T00:00:00+0000",
        volume=None,
        is_healthy=False,
        reasons=["backup RAID volume not mounted"],
    )
    s2 = BackupStatus.from_json(s.to_json())
    assert s2.volume is None
    assert s2.reasons == ["backup RAID volume not mounted"]


def test_status_path_writable(tmp_path):
    """write_status_file + read_status_file is symmetric on a custom path."""
    target = tmp_path / "status.json"
    s = BackupStatus(probe_ts=1.0, probe_ts_iso="x", is_healthy=True)
    write_status_file(s, path=target)
    s2 = read_status_file(path=target)
    assert s2 is not None
    assert s2.is_healthy is True


def test_read_status_file_missing_returns_none(tmp_path):
    """read_status_file returns None (not an exception) if file absent."""
    target = tmp_path / "does-not-exist.json"
    assert read_status_file(path=target) is None


def test_read_status_file_corrupt_returns_none(tmp_path):
    """Garbage JSON doesn't crash; we treat it as 'probe never ran'."""
    target = tmp_path / "bad.json"
    target.write_text("not json {{{")
    assert read_status_file(path=target) is None


# ── probe_backup_files — dedupe ────────────────────────────────────────


def test_probe_backup_files_dedupes(tmp_path):
    """Files found under both a parent and child root are reported once."""
    (tmp_path / "child").mkdir()
    f = tmp_path / "child" / "x.sqlite3"
    f.write_bytes(b"x")
    files = probe_backup_files(roots=(tmp_path / "child", tmp_path))
    assert len(files) == 1
    assert files[0].path.endswith("x.sqlite3")


def test_probe_backup_files_missing_root_returns_empty(tmp_path):
    """Roots that don't exist are skipped silently."""
    files = probe_backup_files(roots=(tmp_path / "nope",))
    assert files == []


def test_probe_backup_files_sorts_newest_first(tmp_path):
    """Newest file first so the dashboard can show 'latest' without sorting."""
    older = tmp_path / "old.sqlite3"
    older.write_bytes(b"o")
    # Backdate it
    old_time = (datetime.now() - timedelta(days=2)).timestamp()
    import os
    os.utime(older, (old_time, old_time))

    newer = tmp_path / "new.sqlite3"
    newer.write_bytes(b"n")

    files = probe_backup_files(roots=(tmp_path,))
    assert files[0].path.endswith("new.sqlite3")
    assert files[1].path.endswith("old.sqlite3")


# ── probe_launchd_job — mock subprocess ────────────────────────────────


def test_probe_launchd_job_parses_state_and_exit_code():
    """The plist output parser pulls state + last exit code."""
    fake_output = """\
service state:
        state = waiting
        last exit code = 0
"""
    fake_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=fake_output, stderr=""
    )
    with patch("subprocess.run", return_value=fake_result):
        j = probe_launchd_job(uid=501, label="com.videoapp.backup-db")
    assert j.state == "waiting"
    assert j.last_exit_code == 0
    assert j.is_healthy is True


def test_probe_launchd_job_flags_failure():
    """Non-zero exit code → unhealthy."""
    fake_output = "state = not running\nlast exit code = 126\n"
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=fake_output, stderr="")
    with patch("subprocess.run", return_value=fake_result):
        j = probe_launchd_job(uid=501, label="com.videoapp.backup-db")
    assert j.is_healthy is False
    assert j.last_exit_code == 126


def test_probe_launchd_job_handles_timeout():
    """A hung launchd print → 'unknown' state, not a crash."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=5)):
        j = probe_launchd_job(uid=501, label="com.videoapp.backup-db")
    assert j.state == "unknown"
    assert j.is_healthy is False


# ── probe_raid_volume — mock diskutil ──────────────────────────────────


def test_probe_raid_volume_parses_apfs_keys():
    """On APFS, APFSContainerFree is the right field — not FreeSpace."""
    import plistlib
    fake_plist = plistlib.dumps({
        "FreeSpace": 0,                              # WRONG on APFS
        "APFSContainerFree": 100_000_000_000,         # 100 GB free
        "APFSContainerSize": 3_000_000_000_000,
    }).decode()
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=fake_plist, stderr="")
    with patch("subprocess.run", return_value=fake_result):
        v = probe_raid_volume()
    assert v is not None
    assert v.free_bytes == 100_000_000_000
    assert 90 < v.free_gb < 110
    assert v.is_low is False


def test_probe_raid_volume_flags_low_space():
    """Free space below threshold → is_low=True."""
    import plistlib
    fake_plist = plistlib.dumps({
        "FreeSpace": 100_000_000,           # ~0.1 GB
        "APFSContainerFree": 100_000_000,
        "APFSContainerSize": 1_000_000_000_000,
    }).decode()
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=fake_plist, stderr="")
    with patch("subprocess.run", return_value=fake_result):
        v = probe_raid_volume()
    assert v is not None
    assert v.is_low is True


def test_probe_raid_volume_unmounted():
    """Volume not mounted → returns None, doesn't crash."""
    with patch("pathlib.Path.exists", return_value=False):
        v = probe_raid_volume(mount="/Volumes/Nope")
    assert v is None


# ── collect_status — overall health decision ───────────────────────────


def test_collect_status_flags_stale_backup(tmp_path):
    """When the newest backup is older than STALE_BACKUP_HOURS, is_healthy=False."""
    import os
    from app.services.backup_monitor import BACKUP_ROOTS

    # Make a backup file with a forced old mtime
    fake_root = tmp_path / "raid"
    (fake_root / "db-backup").mkdir(parents=True)
    old_file = fake_root / "db-backup" / "old.sqlite3"
    old_file.write_bytes(b"x")
    old_mtime = (datetime.now() - timedelta(days=10)).timestamp()
    os.utime(old_file, (old_mtime, old_mtime))

    # Mock out launchd + diskutil so we focus on the files branch.
    with patch("app.services.backup_monitor.BACKUP_ROOTS", (fake_root,)):
        with patch("app.services.backup_monitor.probe_launchd_job",
                   return_value=LaunchdJobStatus(label="x", state="waiting", is_healthy=True)):
            with patch("app.services.backup_monitor.probe_raid_volume",
                       return_value=VolumeStatus(mount="/m", free_bytes=10**10, total_bytes=10**11,
                                                 used_bytes=9*10**10, free_gb=10.0, is_low=False)):
                s = collect_status()
    assert s.is_healthy is False
    assert any("newest backup" in r for r in s.reasons)


def test_collect_status_flags_unmounted_raid():
    """Missing RAID → reason mentions 'not mounted'."""
    with patch("app.services.backup_monitor.probe_launchd_job",
               return_value=LaunchdJobStatus(label="x", state="waiting", is_healthy=True)):
        with patch("app.services.backup_monitor.probe_raid_volume", return_value=None):
            with patch("app.services.backup_monitor.probe_backup_files", return_value=[]):
                s = collect_status()
    assert s.is_healthy is False
    assert any("not mounted" in r for r in s.reasons)


# ── HTTP integration tests ─────────────────────────────────────────────


def test_ready_endpoint_reports_backup_and_integrity(client: TestClient):
    """/api/ready now includes backup summary + integrity_ok fields."""
    r = client.get("/api/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert "integrity_ok" in body
    assert body["integrity_ok"] is True
    assert "backup" in body
    assert "probe_present" in body["backup"]


def test_admin_backups_page_renders_for_admin(admin_client: TestClient):
    """The /admin/backups page renders for an ADMIN user."""
    r = admin_client.get("/admin/backups")
    assert r.status_code == 200
    html = r.text
    assert "Backup Health" in html


def test_admin_backups_page_gated_for_paid(paid_client: TestClient):
    """PAID users do NOT see /admin/backups (ADMIN-only)."""
    r = paid_client.get("/admin/backups")
    # Should redirect or 403; the _admin_capability_dep enforces this.
    assert r.status_code in (302, 303, 307, 403)


def test_admin_backups_page_gated_for_free(client: TestClient):
    """FREE users cannot see /admin/backups.

    Use a fresh uid (not test-uid) to avoid the test-uid being
    upgraded to ADMIN by an earlier fixture in the same module.
    """
    with patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "random-free-user-abc", "email": "x@x.com"},
    ):
        r = client.get("/admin/backups")
    assert r.status_code in (302, 303, 307, 403)


def test_admin_backups_page_handles_missing_probe(admin_client: TestClient, tmp_path):
    """If /tmp/video-app-backup-status.json doesn't exist, page still renders."""
    # Point STATUS_PATH at a non-existent file just for this test.
    from app.services import backup_monitor as bm
    fake_path = tmp_path / "nope.json"
    with patch.object(bm, "STATUS_PATH", fake_path):
        r = admin_client.get("/admin/backups")
    assert r.status_code == 200
    assert "Probe has never run" in r.text or "hasn't written" in r.text


def test_admin_backups_page_shows_status(admin_client: TestClient, tmp_path):
    """When a status JSON exists, the page reflects its contents."""
    fake_path = tmp_path / "status.json"
    status = BackupStatus(
        probe_ts=1234567890.0,
        probe_ts_iso="2026-08-28T00:00:00+0000",
        jobs=[LaunchdJobStatus(label="com.videoapp.backup-db", state="not running",
                               last_exit_code=126, is_healthy=False)],
        files=[BackupFile(path="/x/y.sqlite3", kind="db-hot",
                          size_bytes=1024, mtime_ts=1234567890.0, age_hours=1.0)],
        volume=VolumeStatus(mount="/m", free_bytes=10**9, total_bytes=10**10,
                            used_bytes=9*10**9, free_gb=0.93, is_low=False),
        is_healthy=False,
        reasons=["no healthy launchd backup jobs"],
    )
    write_status_file(status, path=fake_path)
    from app.services import backup_monitor as bm
    with patch.object(bm, "STATUS_PATH", fake_path):
        r = admin_client.get("/admin/backups")
    assert r.status_code == 200
    html = r.text
    assert "Backup problems detected" in html
    assert "com.videoapp.backup-db" in html
    assert "Run backup now" in html


def test_data_freshness_endpoint_admin_only(admin_client: TestClient, client: TestClient):
    """/api/admin/data-freshness is gated to ADMIN."""
    r = admin_client.get("/api/admin/data-freshness")
    assert r.status_code == 200
    body = r.json()
    assert "courses" in body
    assert "videos" in body
    # FREE/PAID can't reach it. Use a different uid to avoid
    # contamination from the admin_client fixture (which just set
    # test-uid to ADMIN in the same in-memory DB).
    with patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "random-free-user-xyz"},
    ):
        r2 = client.get("/api/admin/data-freshness")
    assert r2.status_code in (302, 303, 307, 403, 401)


def test_backup_status_endpoint_admin_only(admin_client: TestClient, client: TestClient, tmp_path):
    """/api/admin/backup-status returns the JSON, gated to ADMIN."""
    from app.services import backup_monitor as bm
    fake_path = tmp_path / "status.json"
    write_status_file(
        BackupStatus(probe_ts=1.0, probe_ts_iso="x", is_healthy=True),
        path=fake_path,
    )
    with patch.object(bm, "STATUS_PATH", fake_path):
        r = admin_client.get("/api/admin/backup-status")
    assert r.status_code == 200
    assert r.json()["is_healthy"] is True


def test_backup_status_endpoint_503_when_probe_missing(admin_client: TestClient, tmp_path):
    """If the probe has never run, return 503 so monitors see a clear signal."""
    from app.services import backup_monitor as bm
    fake_path = tmp_path / "nope.json"
    with patch.object(bm, "STATUS_PATH", fake_path):
        r = admin_client.get("/api/admin/backup-status")
    assert r.status_code == 503
    assert r.json()["status"] == "no_probe_data"
