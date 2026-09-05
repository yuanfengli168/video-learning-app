"""Per-user personal activity feed (2026-09-05, commit 5/6).

Powers the PAID "Your Activity" page (/activity). Aggregates the
signed-in user's own ui.* + LLM events from the events table —
same worker-independent source as /usage and /admin/*.

Scope: strictly the CALLER's own events (uid-filtered) — enforced
twice: the service takes uid as a required arg, and the route only
ever passes the authenticated user's uid. No query parameter can
select another user's rows.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def get_user_activity(
    db: Session, uid: str, *, limit: int = 100
) -> dict[str, Any]:
    """The signed-in user's own recent activity.

    Shape:
      {
        "summary": {"plays": n, "chats": n, "videos_watched": n,
                    "llm_requests": n},
        "recent": [ {"ts": "...", "source": "...", "message": "...",
                      "video_title": ...} ],
      }

    `recent` is capped (default 100 rows) — the page shows a feed,
    not an unbounded export.
    """
    limit = max(1, min(limit, 500))

    summary_row = db.execute(
        text(
            """
            SELECT
              (SELECT COUNT(*) FROM events WHERE user_id = :uid
                 AND message LIKE 'ui player play%') AS plays,
              (SELECT COUNT(*) FROM events WHERE user_id = :uid
                 AND source = 'ui.chat') AS chats,
              (SELECT COUNT(DISTINCT video_id) FROM events
                 WHERE user_id = :uid
                   AND source = 'ui.player'
                   AND video_id IS NOT NULL) AS videos_watched,
              (SELECT COUNT(*) FROM events WHERE user_id = :uid
                 AND source = 'services.llm_providers'
                 AND message LIKE 'LLM call succeeded via %') AS llm
            """
        ),
        {"uid": uid},
    ).fetchone()

    recent_rows = db.execute(
        text(
            """
            SELECT e.ts, e.source, e.message, v.title
            FROM events e
            LEFT JOIN videos v ON v.id = e.video_id
            WHERE e.user_id = :uid
            ORDER BY e.ts DESC
            LIMIT :limit
            """
        ),
        {"uid": uid, "limit": limit},
    ).fetchall()

    return {
        "summary": {
            "plays": int(summary_row[0] or 0),
            "chats": int(summary_row[1] or 0),
            "videos_watched": int(summary_row[2] or 0),
            "llm_requests": int(summary_row[3] or 0),
        },
        "recent": [
            {
                "ts": str(r[0]),
                "source": r[1],
                "message": r[2],
                "video_title": r[3],
            }
            for r in recent_rows
        ],
    }