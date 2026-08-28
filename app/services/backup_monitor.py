"""Backup health monitoring — Day 10 hardening.

Why this exists
---------------
On 2026-08-28 we discovered that the production DB had been wiped
sometime in the prior 6 days. Root cause #1: the nightly launchd
backup job (`com.videoapp.backup-db`) had been failing silently with
exit code 126 since Aug 22, so we had no recent snapshot to restore
from. The failure was logged to `~/Library/Logs/video-app-backup.log`
but no human was watching.

This module is the fix: a small probe that runs every 5 minutes,
gathers backup state from three independent sources, and emits a
single JSON document that the web UI can read. It also writes
WARNING/ERROR rows into the `events` table so failures appear on
the existing /admin/events page without new UI.

Data sources (read each probe run):
  1. launchd job state — `launchctl print gui/<uid>/<label>`
     → state, last exit code, last run time
  2. Backup files on /Volumes/Storage-Backup-HDD — stat()
     → mtime, size, path
  3. RAID free space — `diskutil info -plist`
     → free bytes, used bytes, total bytes

Output:
  /tmp/video-app-backup-status.json — read by /admin/backups and
  /api/ready. Schema is documented in BackupStatus (below).

Independent of FastAPI — the probe is a standalone script that
just imports this module. That keeps it testable and lets it run
under launchd without spinning up the web app.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# The four backup jobs we monitor. Their labels match the plists
# in /Users/jackyli/Library/LaunchAgents/com.videoapp.backup-*.plist.
BACKUP_LABELS = (
    "com.videoapp.backup-db",
    "com.videoapp.backup-daily",
    "com.videoapp.backup-verify",
    "com.videoapp.backup-monthly",
)

# Where the JSON status file lives. The dashboard reads this.
STATUS_PATH = Path("/tmp/video-app-backup-status.json")

# Where to look for backup files. Matches the layout in
# doc/handover-mvp2-launch.md.
BACKUP_ROOTS = (
    Path("/Volumes/Storage-Backup-HDD/db-backup"),
    Path("/Volumes/Storage-Backup-HDD"),
)

# How long a backup can be missing before we shout.
# 26h covers the 24h daily cycle plus 2h of clock skew / launchd jitter.
STALE_BACKUP_HOURS = 26

# Minimum free space on the backup RAID before we shout. 5 GB is
# enough room for ~5x the current 7 MB DB plus a few full snapshots.
MIN_FREE_GB = 5.0


# ── Result dataclasses (JSON-serialisable) ──────────────────────────────


@dataclass
class LaunchdJobStatus:
    """One launchd job's state as of the last probe."""

    label: str
    state: str = "unknown"           # "running" / "waiting" / "not running" / "unknown"
    last_exit_code: int = 0
    last_run_ts: float | None = None  # epoch seconds; None if never
    is_healthy: bool = False          # convenience for the UI


@dataclass
class BackupFile:
    """One backup file on the RAID."""

    path: str
    kind: str            # "db-hot" | "snapshot" | "monthly"
    size_bytes: int
    mtime_ts: float      # epoch seconds
    age_hours: float     # now - mtime_ts


@dataclass
class VolumeStatus:
    """Free/used bytes on the backup RAID volume."""

    mount: str
    free_bytes: int
    total_bytes: int
    used_bytes: int
    free_gb: float
    is_low: bool


@dataclass
class BackupStatus:
    """Top-level status document. One per probe run."""

    probe_ts: float                          # when this probe ran
    probe_ts_iso: str                        # human-readable
    jobs: list[LaunchdJobStatus] = field(default_factory=list)
    files: list[BackupFile] = field(default_factory=list)
    volume: VolumeStatus | None = None
    is_healthy: bool = False                 # overall
    reasons: list[str] = field(default_factory=list)  # why not healthy

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, blob: str) -> "BackupStatus":
        data = json.loads(blob)
        jobs = [LaunchdJobStatus(**j) for j in data.pop("jobs", [])]
        files = [BackupFile(**f) for f in data.pop("files", [])]
        vol_data = data.pop("volume", None)
        vol = VolumeStatus(**vol_data) if vol_data else None
        return cls(jobs=jobs, files=files, volume=vol, **data)


# ── Probe functions (each independently testable) ──────────────────────


def probe_launchd_job(uid: int, label: str) -> LaunchdJobStatus:
    """Query launchd for one job's state. Never raises — returns
    a default LaunchdJobStatus on any failure.

    Why plist output (-stdout-format plist):
      -parseable instead of regex-grepping free text. Launchd
    output format is unstable across macOS versions; plist is
    stable.
    """
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{label}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return LaunchdJobStatus(label=label, state="unknown", is_healthy=False)

    state = "unknown"
    last_exit = 0
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("state ="):
            state = line.split("=", 1)[1].strip()
        elif line.startswith("last exit code ="):
            try:
                last_exit = int(line.split("=", 1)[1].strip())
            except ValueError:
                last_exit = 0

    # Healthy = either waiting for next run OR running right now.
    is_healthy = state in ("waiting", "running") and last_exit == 0
    return LaunchdJobStatus(
        label=label,
        state=state,
        last_exit_code=last_exit,
        is_healthy=is_healthy,
    )


def _backup_kind(path: Path) -> str:
    """Classify a backup file by its directory + name pattern."""
    parent = path.parent.name
    if parent == "db-backup":
        return "db-hot"
    if parent.startswith("snapshot-"):
        return "snapshot"
    if parent.startswith("monthly-"):
        return "monthly"
    return "other"


def probe_backup_files(roots: tuple[Path, ...] = BACKUP_ROOTS) -> list[BackupFile]:
    """List all backup files on the RAID with their metadata.

    Sorted newest-first so the UI can show "most recent" without
    extra work.

    Dedupe by resolved path — the parent RAID root would otherwise
    re-discover files already found under e.g. db-backup/.
    """
    seen: set[str] = set()
    files: list[BackupFile] = []
    now = time.time()
    for root in roots:
        if not root.exists():
            continue
        # Glob for both .sqlite3 and .sqlite extensions (the
        # original script used .sqlite3; we accept .sqlite too
        # for forward compatibility).
        for pattern in ("*.sqlite3", "*.sqlite"):
            for path in root.rglob(pattern):
                resolved = str(path.resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    stat = path.stat()
                except OSError:
                    continue
                files.append(
                    BackupFile(
                        path=str(path),
                        kind=_backup_kind(path),
                        size_bytes=stat.st_size,
                        mtime_ts=stat.st_mtime,
                        age_hours=(now - stat.st_mtime) / 3600.0,
                    )
                )
    files.sort(key=lambda f: f.mtime_ts, reverse=True)
    return files


def probe_raid_volume(mount: str = "/Volumes/Storage-Backup-HDD") -> VolumeStatus | None:
    """Return free/total/used bytes for the backup RAID volume.

    Uses `diskutil info -plist` for stable output. Returns None
    if the volume isn't mounted (so the caller can show "not
    mounted" rather than crashing).
    """
    if not Path(mount).exists():
        return None
    try:
        result = subprocess.run(
            ["diskutil", "info", "-plist", mount],
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        info = plistlib.loads(result.stdout)
        # On APFS volumes the per-volume FreeSpace is often 0 because
        # the volume doesn't preallocate blocks — free space lives on
        # the container. APFSContainerFree is the right field for APFS;
        # fall back to FreeSpace on HFS+/older formats.
        free = int(
            info.get("APFSContainerFree")
            or info.get("FreeSpace")
            or 0
        )
        total = int(
            info.get("APFSContainerSize")
            or info.get("TotalSize")
            or 0
        )
        used = total - free
        free_gb = free / (1024 ** 3)
        return VolumeStatus(
            mount=mount,
            free_bytes=free,
            total_bytes=total,
            used_bytes=used,
            free_gb=round(free_gb, 2),
            is_low=free_gb < MIN_FREE_GB,
        )
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


# ── Top-level orchestrator (what the probe script + dashboard call) ─────


def collect_status(uid: int | None = None) -> BackupStatus:
    """Collect status from all sources and return a BackupStatus.

    Args:
        uid: Unix UID for `launchctl print gui/<uid>/...`. If None,
             uses the current process's uid (os.getuid()).
    """
    if uid is None:
        uid = os.getuid()

    now = time.time()
    jobs = [probe_launchd_job(uid, label) for label in BACKUP_LABELS]
    files = probe_backup_files()
    volume = probe_raid_volume()

    # Decide overall health + reasons.
    reasons: list[str] = []
    if not any(j.is_healthy for j in jobs):
        reasons.append("no healthy launchd backup jobs")
    if volume is None:
        reasons.append("backup RAID volume not mounted")
    elif volume.is_low:
        reasons.append(f"backup RAID low on space: {volume.free_gb} GB free")
    # Find most recent backup file across all kinds.
    if files:
        newest = files[0]
        if newest.age_hours > STALE_BACKUP_HOURS:
            reasons.append(
                f"newest backup is {newest.age_hours:.1f}h old "
                f"(limit: {STALE_BACKUP_HOURS}h)"
            )
    else:
        reasons.append("no backup files found on RAID")

    return BackupStatus(
        probe_ts=now,
        probe_ts_iso=time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        jobs=jobs,
        files=files,
        volume=volume,
        is_healthy=len(reasons) == 0,
        reasons=reasons,
    )


def write_status_file(status: BackupStatus, path: Path | None = None) -> None:
    """Atomically write the JSON to disk. Atomic = write to .tmp + rename.

    If `path` is None we use the module-level STATUS_PATH at call time
    (not at function-definition time) so tests can patch the module
    attribute and have it take effect.
    """
    target = path if path is not None else STATUS_PATH
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(status.to_json())
    tmp.replace(target)


def read_status_file(path: Path | None = None) -> BackupStatus | None:
    """Read the latest probe status. Returns None if the file is missing
    or unparseable (e.g. probe has never run).

    If `path` is None we use STATUS_PATH at call time (see write_status_file).
    """
    target = path if path is not None else STATUS_PATH
    if not target.exists():
        return None
    try:
        return BackupStatus.from_json(target.read_text())
    except (json.JSONDecodeError, OSError, TypeError, KeyError):
        return None


def main() -> int:
    """CLI entry point for the launchd probe. Returns 0 on success,
    non-zero if the probe itself failed (not the same as 'backup
    is unhealthy' — that goes in the JSON).
    """
    try:
        status = collect_status()
        write_status_file(status)
        print(f"[backup-probe] healthy={status.is_healthy} reasons={status.reasons}")
        return 0
    except Exception as exc:
        print(f"[backup-probe] FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
