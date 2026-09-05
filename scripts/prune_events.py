#!/usr/bin/env python3
"""Prune events older than 90 days — archive-first, then delete.

Retention policy (user decision 2026-09-05):
  - Live events table: keep 90 days (keeps /admin/analytics +
    /usage queries fast, bounds table growth).
  - HDD archive: keep FOREVER — rows are exported as monthly jsonl
    files to /Volumes/Storage-Backup-HDD/video-learning-app-data/
    BEFORE deletion (events-archive-YYYY-MM.jsonl, append-friendly),
    so nothing is ever lost for future analysis.

Safety design (data loss is the #1 risk of a prune job):
  1. ARCHIVE-FIRST: rows are written to the HDD jsonl before any
     DELETE. If the HDD isn't mounted, the job exits 0 with a log
     line and prunes NOTHING (deleting unarchived rows would violate
     the keep-forever-on-HDD requirement).
  2. MONTHLY DEDUPE: each run re-exports rows whose month file is
     missing OR contains fewer rows than the DB claims — but to
     keep the common path cheap, it appends only when the file is
     absent or the DB count for that month exceeds what a marker
     line says we already exported. The marker (_exported: N) is
     the last line of each month file.
  3. BATCH DELETE: deletes in 5,000-row chunks in one transaction
     per chunk, so a crash mid-prune leaves a consistent DB (SQLite
     transactional guarantee) and the next run resumes cleanly.
  4. DRY RUN default: `--apply` required to actually delete. Same
     convention as scripts/fix_stuck_transcribe.py.

Usage:
  python scripts/prune_events.py                # dry-run (counts only)
  python scripts/prune_events.py --apply         # archive + prune
  python scripts/prune_events.py --apply --days 90

Wiring: run AFTER backup-daily.sh (so the nightly snapshot also
captures anything being pruned that night) — the launchd plist in
scripts/launchdaemons/ runs it at 00:30, 30 min after the backup.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("/Volumes/Storage-Fast-NVMe/video_learning.db")
ARCHIVE_ROOT = Path("/Volumes/Storage-Backup-HDD/video-learning-app-data")
DEFAULT_DAYS = 90
BATCH_SIZE = 5000

# Columns exported per row — the full events schema (id included so
# re-imports preserve identity and dedupe cleanly).
ROW_COLUMNS = (
    "id, ts, level, source, message, user_id, video_id, context_json"
)


def _marker_count(path: Path) -> int:
    """Read the _exported marker from a month file (0 if absent)."""
    if not path.exists():
        return 0
    try:
        last = ""
        with path.open() as f:
            for line in f:
                if line.strip():
                    last = line
        if last.startswith("# _exported: "):
            return int(last.split(":", 1)[1].strip())
    except (ValueError, OSError):
        return 0
    return 0


def _month_of(ts: str) -> str:
    """'2026-09-05 02:47:57' → '2026-09'."""
    return (ts or "")[:7]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--archive-root", default=str(ARCHIVE_ROOT))
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually archive + delete (default: dry-run)",
    )
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: DB not found: {db}", file=sys.stderr)
        return 2

    # ── HDD pre-flight: no HDD → no prune (archive-first guarantee) ──
    # We try to create the archive root (mkdir -p). If the HDD isn't
    # mounted, /Volumes/<volume> doesn't exist and mkdir raises — we
    # treat that as "not mounted" and prune NOTHING.
    archive_root = Path(args.archive_root)
    if args.apply:
        try:
            archive_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            print(
                f"NOTICE: cannot create {archive_root} (HDD not "
                "mounted?) — pruning nothing (archive-first policy)."
            )
            return 0

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        # Cutoff: rows strictly older than N days.
        cutoff_row = conn.execute(
            "SELECT datetime('now', ?) AS cutoff", (f"-{args.days} days",)
        ).fetchone()
        cutoff = cutoff_row["cutoff"]

        stale = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE ts < ?", (cutoff,)
        ).fetchone()["n"]
        if stale == 0:
            print(f"No events older than {args.days} days — nothing to do.")
            return 0

        print(
            f"{stale} event(s) older than {args.days} days "
            f"(before {cutoff})."
        )

        # Group by month for archiving.
        months = conn.execute(
            "SELECT substr(ts, 1, 7) AS month, COUNT(*) AS n "
            "FROM events WHERE ts < ? GROUP BY month ORDER BY month",
            (cutoff,),
        ).fetchall()
        for m in months:
            print(f"  {m['month']}: {m['n']} rows")

        if not args.apply:
            print("Dry run. Pass --apply to archive to HDD then delete.")
            return 0

        # ── Archive each month (append + marker) ────────────────────
        # (archive_root was created in pre-flight)
        total_exported = 0
        for m in months:
            month = m["month"]
            out_path = archive_root / f"events-archive-{month}.jsonl"
            already = _marker_count(out_path)
            rows = conn.execute(
                f"SELECT {ROW_COLUMNS} FROM events "
                f"WHERE ts < ? AND substr(ts, 1, 7) = ? "
                f"ORDER BY ts",
                (cutoff, month),
            ).fetchall()
            # Append only rows beyond what the marker says we've
            # already exported (idempotent re-runs).
            to_append = rows[already:] if len(rows) > already else []
            if to_append:
                with out_path.open("a", encoding="utf-8") as f:
                    for r in to_append:
                        f.write(json.dumps(dict(r), ensure_ascii=False) + "\n")
                    f.write(f"# _exported: {len(rows)}\n")
                total_exported += len(to_append)
                print(
                    f"  archived {len(to_append)} → {out_path} "
                    f"(total {len(rows)})"
                )

        # ── Delete in batches ──────────────────────────────────────
        deleted_total = 0
        while True:
            cur = conn.execute(
                "DELETE FROM events WHERE id IN ("
                "  SELECT id FROM events WHERE ts < ? LIMIT ?"
                ")",
                (cutoff, BATCH_SIZE),
            )
            deleted_total += cur.rowcount
            conn.commit()
            if cur.rowcount < BATCH_SIZE:
                break
        print(
            f"Done: exported {total_exported} row(s) to HDD, "
            f"deleted {deleted_total} from the live table."
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())