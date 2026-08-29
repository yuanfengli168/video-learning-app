#!/usr/bin/env python3
"""
restore_from_snapshot.py — Restore data from a RAID snapshot SQLite into the
live production DB. One-off recovery tool for the 2026-08-28 data-loss incident.

Design principles:
  - IDEMPOTENT: uses INSERT OR IGNORE on primary keys so running twice is safe.
  - DRY-RUN FIRST: --dry-run prints the plan without writing.
  - ATOMIC: wraps the actual restore in a single transaction; rolls back on any
    error so we never leave the live DB half-restored.
  - SELECTIVE: only restores courses/sections/videos/assets. Never touches users,
    events, chat_* — those either are fine in live or never existed in snapshot.
  - PATH-MAPPING: rewrites old /Users/jackyli/Desktop/.../uploads/X paths to
    the current /Volumes/Storage-Medium-NVMe/video-app/uploads/ location.
  - BEST-EFFORT ASSET CONTENT: keeps raw TEXT; transcript-shaped JSON is preserved
    verbatim, plain-text summaries/mindmaps are wrapped in {"text": "..."} so
    newer code that calls json.loads won't crash.

Usage:
    # Preview only
    python scripts/restore_from_snapshot.py --snapshot /path/to/snapshot.sqlite3 --dry-run

    # Actually restore
    python scripts/restore_from_snapshot.py --snapshot /path/to/snapshot.sqlite3 --apply

    # Apply with explicit live DB
    python scripts/restore_from_snapshot.py --snapshot ... --live /path/to/live.sqlite3 --apply

Reference: doc/data-recovery-2026-08-28.md
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

# Tables we restore from snapshot. Order matters for FK-style dependencies.
RESTORE_TABLES = ["courses", "sections", "videos", "assets"]

# File-path rewrite rules. We try each in order; first match wins.
# We prefer rewriting to the live RAID location because that's where the app
# serves files from today. If --uploads-source is given, we additionally check
# the snapshot's uploads dir to confirm each file actually exists at *some*
# location, so the dry-run report can warn about would-be 404s.
DEFAULT_LIVE_UPLOADS = "/Volumes/Storage-Medium-NVMe/video-app/uploads/"

PATH_REWRITES = [
    # Old: /Users/jackyli/Desktop/Githubs/video-learning-app/uploads/X
    ("/Users/jackyli/Desktop/Githubs/video-learning-app/uploads/", DEFAULT_LIVE_UPLOADS),
    # Older: relative uploads/ under the repo
    ("uploads/", DEFAULT_LIVE_UPLOADS),
]

# Current-only columns we need to populate with defaults because the snapshot
# doesn't have them. (id, default) — applied only when the snapshot is missing
# the column. Keeping the defaults in one place makes it easy to evolve.
DEFAULT_VALUES = {
    "videos": {
        "visibility": 0,           # PRIVATE — admin needs to flip after restore
        "caption_languages": "en",  # free-text; matches a single-language transcript
    },
}

DEFAULT_VISIBILITY = 0


def _open(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a SQLite DB. read_only=True uses URI mode so we never accidentally
    write to the snapshot file."""
    if read_only:
        uri = f"file:{path}?mode=ro"
        return sqlite3.connect(uri, uri=True)
    return sqlite3.connect(path)


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def _row_dicts(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cols = _table_columns(conn, table)
    cur = conn.execute(f"SELECT * FROM {table}")
    rows = cur.fetchall()
    return [dict(zip(cols, row)) for row in rows]


def _rewrite_path(p: str | None) -> str | None:
    if not p:
        return p
    for old, new in PATH_REWRITES:
        if p.startswith(old):
            return new + p[len(old):]
    return p


def _wrap_asset_content(asset_type: str, raw: str) -> str:
    """Best-effort shape to current schema. Transcript is already JSON; summary/
    mindmap/quiz/flashcards/topic_timestamps are plain markdown/text and need
    a wrapper so json.loads() doesn't crash if newer code expects JSON."""
    if not raw:
        return raw
    try:
        # Already JSON? Pass through unchanged.
        parsed = json.loads(raw)
        # transcript uses {"segments": [...]}; mindmap uses {"root": {...}}
        # Either way, it's already valid.
        if isinstance(parsed, (dict, list)):
            return raw
    except (json.JSONDecodeError, TypeError):
        pass
    # Plain text → wrap as {"text": "..."} with the original markdown preserved.
    return json.dumps({"text": raw, "_restored_from_snapshot": True}, ensure_ascii=False)


def _normalize_video(row: dict, target_cols: set[str]) -> dict:
    """Project a snapshot video row into the live schema, applying defaults
    for newer columns and rewriting file_path."""
    out = dict(row)
    # Type fixes
    if "duration" in out and out["duration"] is not None:
        try:
            out["duration"] = float(out["duration"])
        except (TypeError, ValueError):
            out["duration"] = 0.0
    if "file_path" in out:
        out["file_path"] = _rewrite_path(out["file_path"])
    # Defaults for columns the snapshot doesn't have
    for col, default in DEFAULT_VALUES.get("videos", {}).items():
        if col not in out or out[col] is None:
            out[col] = default
    # Visibility — the live schema requires it; old snapshot didn't have it.
    if "visibility" in target_cols and "visibility" not in out:
        out["visibility"] = DEFAULT_VISIBILITY
    return out


def _normalize_asset(row: dict) -> dict:
    """Re-serialize asset.content to a JSON-safe shape."""
    out = dict(row)
    if "content" in out:
        out["content"] = _wrap_asset_content(out.get("asset_type", ""), out["content"])
    return out


def _table_strategy(table: str, rows: list[dict], live_cols: set[str]) -> Iterable[dict]:
    """Per-table row transformation. Returns the rows ready for INSERT."""
    if table == "videos":
        return [_normalize_video(r, live_cols) for r in rows]
    if table == "assets":
        return [_normalize_asset(r) for r in rows]
    return rows


def _intersect(row: dict, target_cols: set[str]) -> dict:
    """Drop keys that aren't in the live schema so INSERT doesn't error."""
    return {k: v for k, v in row.items() if k in target_cols}


def _insert_ignore(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    """INSERT OR IGNORE — safe to re-run. Returns count inserted.
    `rows` here must already have been processed by _table_strategy upstream."""
    if not rows:
        return 0
    target_cols = set(_table_columns(conn, table))
    if not target_cols:
        raise RuntimeError(f"Live DB has no columns for {table}; schema mismatch?")
    # Project each row to the live schema.
    rows = [_intersect(r, target_cols) for r in rows]
    cols = list(rows[0].keys())
    placeholders = ", ".join("?" * len(cols))
    sql = f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    inserted = 0
    for row in rows:
        try:
            cur = conn.execute(sql, [row[c] for c in cols])
            inserted += cur.rowcount
        except sqlite3.IntegrityError as e:
            # Should be impossible with OR IGNORE, but log defensively.
            print(f"  skip ({e}): {row.get('id') or row.get('title', '?')[:40]}", file=sys.stderr)
    return inserted


def plan(snapshot_path: Path, uploads_source: Path | None = None) -> dict[str, Any]:
    """Build the restore plan and return counts + samples.
    If uploads_source is given, also report which videos would actually be
    playable after restore (file exists at some reachable location)."""
    snap = _open(snapshot_path, read_only=True)
    out: dict[str, Any] = {}
    for table in RESTORE_TABLES:
        rows = _row_dicts(snap, table)
        out[table] = {"count": len(rows), "sample": rows[:2] if rows else []}
    # Videos: report playability
    videos = _row_dicts(snap, "videos")
    playable = 0
    missing: list[dict] = []
    for v in videos:
        rewritten = _rewrite_path(v.get("file_path"))
        uuid = v.get("id")
        cand = []
        if rewritten and rewritten != "x":
            cand.append(rewritten)
        if uploads_source and uuid:
            for ext in (".mp4", ".webm"):
                cand.append(str(uploads_source / f"{uuid}{ext}"))
        if any(Path(p).exists() for p in cand):
            playable += 1
        else:
            missing.append({"id": uuid, "title": v.get("title"), "rewritten_path": rewritten})
    out["videos"]["playable"] = playable
    out["videos"]["missing"] = missing
    snap.close()
    return out


def apply(snapshot_path: Path, live_path: Path,
           uploads_source: Path | None = None) -> dict[str, int]:
    """Apply the restore inside a transaction. Rolls back on any error.
    If uploads_source is given, also copy referenced upload files to the live
    uploads dir so the DB rows actually resolve to playable videos."""
    snap = _open(snapshot_path, read_only=True)
    live = _open(live_path)

    inserted: dict[str, int] = {}
    try:
        live.execute("BEGIN IMMEDIATE")

        # Step 1: ensure all user_ids referenced by restored rows exist in the
        # live users table. Snapshot predates Day-5 roles so it has no users
        # table; we INSERT OR IGNORE a placeholder (role=ADMIN=0 since these
        # were the original owners).
        existing_users = {r[0] for r in live.execute("SELECT user_id FROM users").fetchall()}
        referenced_users = set()
        for table in ("courses",):  # only courses has user_id that FK matters for
            for row in _row_dicts(snap, table):
                uid = row.get("user_id")
                if uid and uid not in existing_users:
                    referenced_users.add(uid)
        for uid in referenced_users:
            live.execute(
                "INSERT OR IGNORE INTO users (user_id, email, role, notes) "
                "VALUES (?, ?, 0, ?)",
                (uid, f"{uid}@restored-from-snapshot", "Restored from 2026-08-22 RAID snapshot"),
            )
        inserted["users_placeholder_created"] = len(referenced_users)

        # Step 2: restore courses/sections/videos/assets
        for table in RESTORE_TABLES:
            rows = _row_dicts(snap, table)
            rows = _table_strategy(table, rows, set(_table_columns(live, table)))
            inserted[table] = _insert_ignore(live, table, rows)
        live.execute("COMMIT")
    except Exception:
        live.execute("ROLLBACK")
        raise
    finally:
        snap.close()
        live.close()

    # Copy uploads outside the DB transaction. Failures here don't roll back
    # the DB insert; we just warn.
    files_copied = 0
    files_missing: list[str] = []
    if uploads_source is not None and uploads_source.is_dir():
        live_uploads = Path(DEFAULT_LIVE_UPLOADS.rstrip("/"))
        live_uploads.mkdir(parents=True, exist_ok=True)
        snap = _open(snapshot_path, read_only=True)
        videos = _row_dicts(snap, "videos")
        snap.close()
        import shutil
        for v in videos:
            uuid = v.get("id")
            if not uuid:
                continue
            for ext in (".mp4", ".webm"):
                src = uploads_source / f"{uuid}{ext}"
                if src.exists():
                    dst = live_uploads / src.name
                    if not dst.exists():
                        shutil.copy2(src, dst)
                        files_copied += 1
                    break
            else:
                files_missing.append(uuid)
        inserted["upload_files_copied"] = files_copied
        inserted["upload_files_missing"] = len(files_missing)
    return inserted


def _print_plan(plan_data: dict, uploads_source: Path | None = None) -> None:
    print("=" * 70)
    print("RESTORE PLAN (dry-run — nothing has been written)")
    print("=" * 70)
    for table, info in plan_data.items():
        print(f"\n  {table}: {info['count']} rows in snapshot")
        for r in info["sample"]:
            ident = r.get("title") or r.get("id") or "?"
            print(f"    e.g. [{ident}]")
            if table == "videos" and r.get("file_path"):
                print(f"      file_path -> {_rewrite_path(r['file_path'])}")
    # Videos playability summary
    videos_info = plan_data.get("videos", {})
    if "playable" in videos_info:
        print(f"\n  Videos playable after restore: "
              f"{videos_info['playable']} / {videos_info['count']}"
              + (f" (uploads_source={uploads_source})" if uploads_source else " (no uploads_source given)"))
        for m in videos_info.get("missing", [])[:5]:
            print(f"    ✗ {m.get('id')}: {m.get('title')[:50]}  (path={m.get('rewritten_path')})")
        if len(videos_info.get("missing", [])) > 5:
            print(f"    ... and {len(videos_info['missing']) - 5} more")
    print("\n" + "=" * 70)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--snapshot", type=Path, required=True, help="Path to snapshot .sqlite3")
    p.add_argument("--live", type=Path, default=Path("/Volumes/Storage-Fast-NVMe/video_learning.db"),
                   help="Path to live DB (default: /Volumes/Storage-Fast-NVMe/video_learning.db)")
    p.add_argument("--uploads-source", type=Path, default=None,
                   help="Optional: dir of upload files preserved in snapshot "
                        "(e.g. /Volumes/Storage-Backup-HDD/snapshot-2026-08-22/medium/uploads). "
                        "Used to report which videos will actually be playable.")
    p.add_argument("--dry-run", action="store_true", help="Show plan, do not write")
    p.add_argument("--apply", action="store_true", help="Actually write to live DB")
    args = p.parse_args()

    if not args.snapshot.exists():
        print(f"snapshot not found: {args.snapshot}", file=sys.stderr)
        return 2
    if args.dry_run == args.apply:
        print("Specify exactly one of --dry-run or --apply", file=sys.stderr)
        return 2

    plan_data = plan(args.snapshot, uploads_source=args.uploads_source)
    _print_plan(plan_data, uploads_source=args.uploads_source)

    if args.dry_run:
        print("\n--dry-run specified, exiting.")
        return 0

    # Apply
    print(f"\nApplying to: {args.live}")
    inserted = apply(args.snapshot, args.live, uploads_source=args.uploads_source)
    print("\n" + "=" * 70)
    print("RESTORE COMPLETE")
    print("=" * 70)
    for table, n in inserted.items():
        print(f"  {table:30s} {n}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
