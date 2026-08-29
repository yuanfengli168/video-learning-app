#!/usr/bin/env python3
"""
reset_db.py — Wipe all data rows from the live DB while keeping schema, indexes,
and a single ADMIN user (test-uid). Use this when you want a fresh catalog
without recreating the database file.

Safety:
  - Requires --confirm (or interactive "yes" prompt)
  - Default dry-run shows what WOULD be deleted
  - Always backs up the current DB to logs/reset-<timestamp>.db before deleting
  - Single transaction: rolls back on any error

What survives:
  - Schema (all tables, indexes, constraints)
  - The user with user_id='test-uid' (your personal ADMIN)
  - All other data: deleted

What gets deleted:
  - assets, videos, sections, courses
  - chat_sessions, chat_messages
  - events, paid_waitlist, plugin_runs
  - All users except test-uid

Usage:
    python scripts/reset_db.py --dry-run          # preview only
    python scripts/reset_db.py --confirm          # actually wipe (with backup)
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_LIVE = Path("/Volumes/Storage-Fast-NVMe/video_learning.db")
LOG_DIR = Path("logs")
PRESERVED_USERS = {"test-uid"}

# Order matters: delete child rows before parents (FK constraints).
# SQLite enforces FKs only when PRAGMA foreign_keys=ON; we don't enable that
# here so we delete in dependency order anyway to avoid weirdness.
DELETE_ORDER = [
    "plugin_runs",        # depends on videos
    "assets",             # depends on videos
    "chat_messages",      # depends on chat_sessions
    "chat_sessions",      # depends on videos, users
    "videos",             # depends on sections
    "sections",           # depends on courses
    "courses",            # root
    "events",             # standalone
    "paid_waitlist",      # standalone
]


def _live_path() -> Path:
    return DEFAULT_LIVE


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for tbl in DELETE_ORDER + ["users"]:
        cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}")
        out[tbl] = cur.fetchone()[0]
    return out


def _users_to_preserve(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT user_id FROM users WHERE user_id IN ({})".format(
            ",".join("?" for _ in PRESERVED_USERS)
        ),
        list(PRESERVED_USERS),
    ).fetchall()
    return [r[0] for r in rows]


def dry_run(live: Path) -> None:
    print("=" * 70)
    print(f"DRY RUN: {live}")
    print("=" * 70)
    if not live.exists():
        print(f"  ERROR: live DB does not exist at {live}")
        return
    conn = sqlite3.connect(live)
    counts = _row_counts(conn)
    preserved = _users_to_preserve(conn)
    conn.close()
    print("\n  Current row counts:")
    for t, n in counts.items():
        print(f"    {t:18s} {n}")
    print(f"\n  Users preserved: {preserved}")
    print("\n  After reset:")
    for t in DELETE_ORDER:
        print(f"    {t:18s} 0 (wiped)")
    print(f"    {'users':18s} {len(preserved)} (only preserved users)")
    print("\n  Schema: UNCHANGED (tables/indexes/FKs stay)")
    print("=" * 70)


def apply_reset(live: Path) -> dict[str, int]:
    if not live.exists():
        raise FileNotFoundError(f"live DB not found: {live}")

    # 1. Backup before destructive operation
    LOG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    backup_path = LOG_DIR / f"reset-{timestamp}.db"
    shutil.copy2(live, backup_path)

    # 2. Connect and wipe inside transaction
    conn = sqlite3.connect(live)
    deleted: dict[str, int] = {}
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in DELETE_ORDER:
            cur = conn.execute(f"DELETE FROM {table}")
            deleted[table] = cur.rowcount
        # Demote non-preserved users (delete them)
        placeholders = ",".join("?" for _ in PRESERVED_USERS)
        cur = conn.execute(
            f"DELETE FROM users WHERE user_id NOT IN ({placeholders})",
            list(PRESERVED_USERS),
        )
        deleted["users_removed"] = cur.rowcount
        cur = conn.execute(
            f"SELECT COUNT(*) FROM users WHERE user_id IN ({placeholders})",
            list(PRESERVED_USERS),
        )
        deleted["users_kept"] = cur.fetchone()[0]
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    deleted["_backup_path"] = str(backup_path)
    return deleted


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--live", type=Path, default=DEFAULT_LIVE,
                   help=f"Path to live DB (default: {DEFAULT_LIVE})")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview what would be deleted without writing")
    p.add_argument("--confirm", action="store_true",
                   help="Skip interactive confirmation")
    args = p.parse_args()

    if args.dry_run and args.confirm:
        print("Cannot use --dry-run and --confirm together", file=sys.stderr)
        return 2
    if not args.dry_run and not args.confirm:
        ans = input("This will DELETE all data except test-uid. Type 'yes' to continue: ")
        if ans.strip().lower() != "yes":
            print("Aborted.")
            return 1

    if args.dry_run:
        dry_run(args.live)
        return 0

    print(f"Backing up to logs/reset-<timestamp>.db ...")
    deleted = apply_reset(args.live)
    print("=" * 70)
    print("RESET COMPLETE")
    print("=" * 70)
    for k, v in deleted.items():
        if k.startswith("_"):
            print(f"  {k:20s} {v}")
        else:
            print(f"  {k:20s} {v} rows affected")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
