"""Tests for the events retention prune script (2026-09-05, commit 6/6).

Tests scripts/prune_events.py logic against a temp SQLite DB +
temp archive dir — never touching the live DB or the real HDD.

Covers the safety contract:
  1. Archive-first: rows land in the HDD jsonl BEFORE deletion.
  2. HDD-not-mounted → prune NOTHING (the keep-forever guarantee).
  3. Dry-run default: counts printed, zero rows touched.
  4. Monthly files: events-archive-YYYY-MM.jsonl + _exported marker.
  5. Idempotent re-run: marker dedupe — no duplicate lines.
  6. Only stale rows deleted; fresh rows survive.
  7. Batch deletion works for >BATCH_SIZE rows.
"""

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path("scripts/prune_events.py").resolve()
PROJECT_ROOT = SCRIPT.parents[1]

CUTOFF_DAYS = 90
BATCH_SIZE = 5000

pytestmark = pytest.mark.slow


def _make_db(path: Path) -> None:
    """Create a minimal events table matching the live schema."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE events (
            id VARCHAR(36) PRIMARY KEY,
            ts DATETIME NOT NULL,
            level VARCHAR(16) NOT NULL,
            source VARCHAR(64) NOT NULL,
            message TEXT NOT NULL,
            user_id VARCHAR(128),
            video_id VARCHAR(36),
            context_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def _insert_events(db: Path, rows: list[tuple[str, str]]) -> None:
    """rows: list of (id, ts_iso)."""
    conn = sqlite3.connect(str(db))
    for i, (eid, ts) in enumerate(rows):
        conn.execute(
            "INSERT INTO events (id, ts, level, source, message, "
            "user_id, video_id, context_json) "
            "VALUES (?, ?, 'INFO', 'ui.player', 'ui player play', "
            "'u1', NULL, '{}')",
            (eid, ts),
        )
    conn.commit()
    conn.close()


def _count(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    return n


def _run(db: Path, archive: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--db", str(db),
            "--archive-root", str(archive / "video-learning-app-data"),
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=120,
    )


# ─────────────────────────────────────────────────────────────────────────────

def test_dry_run_touches_nothing(tmp_path):
    db = tmp_path / "t.db"
    _make_db(db)
    # One stale row (180 days old), one fresh.
    _insert_events(db, [
        ("stale-1", "2026-01-01 00:00:00"),
        ("fresh-1", "2999-01-01 00:00:00"),  # future-proof fresh
    ])
    archive = tmp_path / "archive"

    r = _run(db, archive)  # no --apply
    assert r.returncode == 0
    assert "Dry run" in r.stdout
    assert _count(db) == 2  # untouched
    assert not (archive / "video-learning-app-data").exists()


def test_apply_archives_then_deletes(tmp_path):
    db = tmp_path / "t.db"
    _make_db(db)
    _insert_events(db, [
        ("old-1", "2026-01-15 10:00:00"),   # ~8 months old → stale
        ("new-1", "2999-01-01 00:00:00"),    # fresh, survives
    ])
    archive = tmp_path / "archive"

    r = _run(db, archive, "--apply")
    assert r.returncode == 0
    assert "archived 1" in r.stdout
    assert "deleted 1" in r.stdout

    # Live table: only fresh row left.
    assert _count(db) == 1
    conn = sqlite3.connect(str(db))
    survivor = conn.execute("SELECT id FROM events").fetchone()[0]
    conn.close()
    assert survivor == "new-1"

    # HDD archive: the stale row, in its month file, with marker.
    month_file = archive / "video-learning-app-data" / "events-archive-2026-01.jsonl"
    assert month_file.exists()
    lines = month_file.read_text().strip().split("\n")
    data_lines = [l for l in lines if not l.startswith("#")]
    assert len(data_lines) == 1
    row = json.loads(data_lines[0])
    assert row["id"] == "old-1"
    assert lines[-1].startswith("# _exported: 1")


def test_hdd_not_mounted_prunes_nothing(tmp_path):
    """Un-creatable archive root (path under a FILE, like an unmounted
    volume) → exit 0, delete NOTHING."""
    db = tmp_path / "t.db"
    _make_db(db)
    _insert_events(db, [("old-1", "2026-01-15 10:00:00")])
    # A regular FILE where the archive dir's parent would be — mkdir
    # under it always fails, exactly like /Volumes/<volume> missing.
    blocker = tmp_path / "not-mounted"
    blocker.write_text("i am a file, not a mount point")
    bad_root = blocker / "video-learning-app-data"

    r = _run(db, bad_root, "--apply")
    assert r.returncode == 0
    assert "pruning nothing" in r.stdout
    assert _count(db) == 1  # the stale row SURVIVES


def test_apply_idempotent_no_duplicates(tmp_path):
    """Running twice must not duplicate archive lines (marker dedupe)."""
    db = tmp_path / "t.db"
    _make_db(db)
    _insert_events(db, [("old-1", "2026-01-15 10:00:00")])
    archive = tmp_path / "archive"

    r1 = _run(db, archive, "--apply")
    assert r1.returncode == 0
    month_file = archive / "video-learning-app-data" / "events-archive-2026-01.jsonl"

    # Re-insert the same stale row (simulating a restore) and re-run:
    # the archive already has 1 exported → to_append should be 0 extra.
    _insert_events(db, [("old-1", "2026-01-15 10:00:00")])
    r2 = _run(db, archive, "--apply")
    assert r2.returncode == 0
    lines = month_file.read_text().strip().split("\n")
    data_lines = [l for l in lines if not l.startswith("#")]
    assert len(data_lines) == 1  # no duplication


def test_batched_delete_over_batch_size(tmp_path):
    """>BATCH_SIZE stale rows prune in chunks without truncation."""
    db = tmp_path / "t.db"
    _make_db(db)
    rows = [(f"old-{i}", f"2026-01-01 {i % 24:02d}:00:00") for i in range(BATCH_SIZE + 3)]
    _insert_events(db, rows)
    archive = tmp_path / "archive"

    r = _run(db, archive, "--apply")
    assert r.returncode == 0
    assert f"deleted {BATCH_SIZE + 3}" in r.stdout
    assert _count(db) == 0
    month_file = archive / "video-learning-app-data" / "events-archive-2026-01.jsonl"
    data_lines = [l for l in month_file.read_text().split("\n") if not l.startswith("#") and l.strip()]
    assert len(data_lines) == BATCH_SIZE + 3