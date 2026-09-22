#!/usr/bin/env python3
"""export_for_comparison.py — reference adapter (input-contract-v1 §3).

Dumps the CALLING branch's videos (read via plain sqlite3 — the
contract forbids importing the tool's package, and this adapter
deliberately imports nothing from app/ either: P2 self-containment
works both ways) to `compare-ai-results/1` JSON.

Usage:
  python scripts/export_for_comparison.py -o input.json --limit 50
  python scripts/export_for_comparison.py -o input.json --video-ids a b c
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

DB_PATH = "/Volumes/Storage-Fast-NVMe/video_learning.db"


def export(db_path: str, limit: int | None,
           video_ids: list[str] | None) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)  # read-only
    try:
        cur = conn.cursor()
        if video_ids:
            ph = ",".join("?" * len(video_ids))
            cur.execute(
                f"""
                SELECT v.id, v.title, v.language, v.duration
                FROM videos v WHERE v.id IN ({ph})
                """,
                video_ids,
            )
            video_rows = cur.fetchall()
        else:
            cur.execute(
                """
                SELECT DISTINCT v.id, v.title, v.language, v.duration
                FROM videos v
                JOIN assets a ON a.video_id = v.id
                WHERE v.status = 'ready'
                  AND v.youtube_id IS NOT NULL
                  AND a.asset_type = 'transcript'
                ORDER BY v.created_at ASC
                LIMIT ?
                """,
                (limit or -1,),
            )
            video_rows = cur.fetchall()

        transcripts = []
        for vid, title, language, duration in video_rows:
            cur.execute(
                "SELECT content FROM assets "
                "WHERE video_id = ? AND asset_type = 'transcript' LIMIT 1",
                (vid,),
            )
            row = cur.fetchone()
            if not row:
                continue
            content = json.loads(row[0])
            segments = content.get("segments") or []
            if not segments:
                continue
            transcripts.append({
                "video_id": vid,
                "title": title,
                "language": language,
                "duration_seconds": duration,
                "source": "youtube_captions",
                "segments": [
                    {"start": float(s["start"]), "end": float(s["end"]),
                     "text": str(s["text"])}
                    for s in segments
                ],
            })
    finally:
        conn.close()

    return {
        "schema": "compare-ai-results/1",
        "transcripts": transcripts,
        "models": ["glm-5.2:cloud", "minimax-m3:cloud"],
        "tasks": ["materials"],
        "dimensions": ["all"],
        "labels": {
            "glm-5.2:cloud": "reference",
            "minimax-m3:cloud": "candidate",
        },
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--video-ids", nargs="*", default=None)
    p.add_argument("--db", default=DB_PATH)
    args = p.parse_args()

    data = export(args.db, args.limit, args.video_ids)
    with open(args.out, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[export] {len(data['transcripts'])} transcript(s) → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())