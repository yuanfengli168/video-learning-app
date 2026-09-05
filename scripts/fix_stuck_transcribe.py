#!/usr/bin/env python3
"""Mark stuck "transcribing" videos as failed so the UI stops lying.

Background context (2026-09-03):
  The user's Mac has `huggingface.co` blocked at the firewall.
  `_run_transcribe_job` calls `mlx_whisper.transcribe()` which
  internally calls `huggingface_hub.snapshot_download()`. That call
  hangs forever on the network hang (no exception, no log, no
  progress). The video row stays `status='transcribing'`,
  `progress=0` forever.

This script finds all such stuck rows and marks them as `failed`
with a clear error message in `last_transcribe_job.error`.

Usage:
  python scripts/fix_stuck_transcribe.py           # dry-run, list stuck rows
  python scripts/fix_stuck_transcribe.py --apply   # actually update DB
  python scripts/fix_stuck_transcribe.py --minutes 10   # change threshold
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = "/Volumes/Storage-Fast-NVMe/video_learning.db"
DEFAULT_MINUTES = 15  # longer than this in 'transcribing' = stuck


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to SQLite DB")
    parser.add_argument(
        "--minutes",
        type=int,
        default=DEFAULT_MINUTES,
        help="Mark rows stuck for at least this many minutes (default: 15)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry-run, only lists rows)",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                id,
                title,
                status,
                whisper_model,
                whisper_backend,
                transcribe_started_at,
                transcribed_at,
                (julianday('now') - julianday(transcribe_started_at)) * 24 * 60
                    AS minutes_in_state,
                last_transcribe_job
            FROM videos
            WHERE status = 'transcribing'
              AND transcribe_started_at IS NOT NULL
              AND (julianday('now') - julianday(transcribe_started_at)) * 24 * 60
                  >= ?
            ORDER BY transcribe_started_at ASC
            """,
            (args.minutes,),
        ).fetchall()

        if not rows:
            print(
                f"No videos stuck in 'transcribing' for >= {args.minutes} min."
            )
            return 0

        print(f"Found {len(rows)} stuck video(s):")
        print()
        for r in rows:
            print(f"  • {r['id']}")
            print(f"    title:        {r['title']}")
            print(
                f"    model:        {r['whisper_model']} "
                f"(backend={r['whisper_backend']})"
            )
            print(f"    started:      {r['transcribe_started_at']}")
            print(f"    minutes stuck: {r['minutes_in_state']:.1f}")
            print()

        if not args.apply:
            print("Dry run. Pass --apply to mark these as failed.")
            return 0

        # Apply: rewrite last_transcribe_job with status=failed, error message,
        # and bump status to 'error' so the UI shows the actual problem.
        for r in rows:
            existing_job = {}
            if r["last_transcribe_job"]:
                try:
                    existing_job = json.loads(r["last_transcribe_job"])
                except json.JSONDecodeError:
                    pass

            existing_job.update(
                {
                    "status": "failed",
                    "completed_at": datetime.now(timezone.utc)
                    .replace(tzinfo=None)
                    .isoformat(),
                    "error": (
                        f"Transcribe stuck at 'Loading Whisper model' for "
                        f"{r['minutes_in_state']:.0f}+ minutes. Likely cause: "
                        f"Hugging Face model download hanging on this machine. "
                        f"Fix: pre-download the model or check network access "
                        f"to huggingface.co. (Marked failed by "
                        f"fix_stuck_transcribe.py at "
                        f"{datetime.now().isoformat(timespec='seconds')})"
                    ),
                    "message": (
                        f"Failed after {r['minutes_in_state']:.0f} min stuck — "
                        f"see error field"
                    ),
                }
            )

            conn.execute(
                """
                UPDATE videos
                SET status = 'error',
                    last_transcribe_job = ?,
                    transcribed_at = COALESCE(
                        transcribed_at,
                        datetime('now')
                    ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (json.dumps(existing_job), r["id"]),
            )
            print(f"  ✓ Marked {r['id']} as failed.")

        conn.commit()
        print(f"\nDone. {len(rows)} video(s) marked as failed.")
        print(
            "Reload the video page in the browser — you'll see the actual "
            "error message instead of '0% Loading Whisper'."
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
