"""Admin analytics aggregation from the events table (2026-09-05).

Powers /admin/analytics (UI + LLM behaviour) and /admin/usage
(per-user quota table). All queries are plain SQL over the events
table — worker-independent truth (same rationale as
app/services/usage.py).

Every query is parameterized + window-bounded; the admin pages are
the only consumers, and each limits rows so a huge events table
can't make the page heavy (indexes exist on ts, source, user_id,
video_id — see app/models/event.py).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def get_analytics_overview(db: Session, days: int = 7) -> dict[str, Any]:
    """Aggregate UI + LLM activity for the last `days` days.

    Shape:
      {
        "window_days": 7,
        "logins": n,                    # distinct login events
        "unique_visitors": n,           # distinct users with ANY event
        "video_plays": n,
        "video_pauses": n,
        "video_seeks": n,
        "video_ended": n,
        "chat_messages": n,
        "materials_tab_clicks": n,
        "transcribe_clicks": n,
        "generate_clicks": n,
        "llm_calls": n,                 # successful LLM completions
        "llm_failures": n,
        "top_videos": [                 # by plays
          {"video_id": ..., "plays": n, "pauses": n, "chats": n, "title": ...}
        ],
        "top_users": [                  # by total events
          {"user_id": ..., "email": ..., "events": n}
        ],
        "daily_activity": [             # events per day, oldest first
          {"day": "2026-09-01", "events": n, "users": n}
        ],
      }
    """
    # 1. Counters — one scan, cheap with the ix_events_ts index.
    counters = db.execute(
        text(
            """
            SELECT
              SUM(CASE WHEN source = 'ui.login' THEN 1 ELSE 0 END)  AS logins,
              SUM(CASE WHEN source = 'ui.chat'  THEN 1 ELSE 0 END)  AS chats,
              SUM(CASE WHEN source = 'ui.materials' THEN 1 ELSE 0 END) AS tab_clicks,
              COUNT(DISTINCT user_id) AS unique_visitors
            FROM events
            WHERE ts >= datetime('now', :days_cutoff)
            """
        ),
        {"days_cutoff": f"-{days} days"},
    ).fetchone()

    # 2. Player + actions counters (context_json carries the action).
    player = db.execute(
        text(
            """
            SELECT
              SUM(CASE WHEN message LIKE 'ui player play%'   THEN 1 ELSE 0 END) AS plays,
              SUM(CASE WHEN message LIKE 'ui player pause%' THEN 1 ELSE 0 END) AS pauses,
              SUM(CASE WHEN message LIKE 'ui player seek%'  THEN 1 ELSE 0 END) AS seeks,
              SUM(CASE WHEN message LIKE 'ui player ended%' THEN 1 ELSE 0 END) AS ended
            FROM events
            WHERE source = 'ui.player'
              AND ts >= datetime('now', :days_cutoff)
            """
        ),
        {"days_cutoff": f"-{days} days"},
    ).fetchone()

    actions = db.execute(
        text(
            """
            SELECT
              SUM(CASE WHEN message LIKE '%transcribe%' THEN 1 ELSE 0 END) AS transcribe_clicks,
              SUM(CASE WHEN message LIKE '%generate%'  THEN 1 ELSE 0 END) AS generate_clicks
            FROM events
            WHERE source = 'ui.actions'
              AND ts >= datetime('now', :days_cutoff)
            """
        ),
        {"days_cutoff": f"-{days} days"},
    ).fetchone()

    llm = db.execute(
        text(
            """
            SELECT
              SUM(CASE WHEN message LIKE 'LLM call succeeded via %' THEN 1 ELSE 0 END) AS ok,
              SUM(CASE WHEN message LIKE 'LLM call failed%'        THEN 1 ELSE 0 END) AS failed
            FROM events
            WHERE source = 'services.llm_providers'
              AND ts >= datetime('now', :days_cutoff)
            """
        ),
        {"days_cutoff": f"-{days} days"},
    ).fetchone()

    # 3. Top videos by plays (join for titles; LEFT JOIN so catalog
    #    rows deleted since still show counts).
    top_videos = db.execute(
        text(
            """
            SELECT e.video_id, v.title,
                   SUM(CASE WHEN e.message LIKE 'ui player play%' THEN 1 ELSE 0 END) AS plays,
                   SUM(CASE WHEN e.message LIKE 'ui player pause%' THEN 1 ELSE 0 END) AS pauses,
                   SUM(CASE WHEN e.source = 'ui.chat' THEN 1 ELSE 0 END) AS chats
            FROM events e
            LEFT JOIN videos v ON v.id = e.video_id
            WHERE e.source IN ('ui.player', 'ui.chat')
              AND e.ts >= datetime('now', :days_cutoff)
            GROUP BY e.video_id
            ORDER BY plays DESC
            LIMIT 10
            """
        ),
        {"days_cutoff": f"-{days} days"},
    ).fetchall()

    # 4. Top users by total events.
    top_users = db.execute(
        text(
            """
            SELECT e.user_id, u.email, COUNT(*) AS events
            FROM events e
            LEFT JOIN users u ON u.user_id = e.user_id
            WHERE e.ts >= datetime('now', :days_cutoff)
            GROUP BY e.user_id
            ORDER BY events DESC
            LIMIT 10
            """
        ),
        {"days_cutoff": f"-{days} days"},
    ).fetchall()

    # 5. Daily activity series.
    daily = db.execute(
        text(
            """
            SELECT date(ts) AS day, COUNT(*) AS events, COUNT(DISTINCT user_id) AS users
            FROM events
            WHERE ts >= datetime('now', :days_cutoff)
            GROUP BY date(ts)
            ORDER BY day ASC
            """
        ),
        {"days_cutoff": f"-{days} days"},
    ).fetchall()

    def _n(row, idx) -> int:
        return int(row[idx] or 0)

    return {
        "window_days": days,
        "logins": _n(counters, 0),
        "unique_visitors": _n(counters, 3),
        "video_plays": _n(player, 0),
        "video_pauses": _n(player, 1),
        "video_seeks": _n(player, 2),
        "video_ended": _n(player, 3),
        "chat_messages": _n(counters, 1),
        "materials_tab_clicks": _n(counters, 2),
        "transcribe_clicks": _n(actions, 0),
        "generate_clicks": _n(actions, 1),
        "llm_calls": _n(llm, 0),
        "llm_failures": _n(llm, 1),
        "top_videos": [
            {
                "video_id": r[0],
                "title": r[1] or "(deleted video)",
                "plays": _n(r, 2),
                "pauses": _n(r, 3),
                "chats": _n(r, 4),
            }
            for r in top_videos
        ],
        "top_users": [
            {"user_id": r[0], "email": r[1] or "(unknown)", "events": _n(r, 2)}
            for r in top_users
        ],
        "daily_activity": [
            {"day": r[0], "events": _n(r, 1), "users": _n(r, 2)}
            for r in daily
        ],
    }


def get_all_users_usage(db: Session) -> list[dict[str, Any]]:
    """Per-user usage table for /admin/usage — every user, both windows.

    Reuses the same window semantics as app/services/usage.py:
      last_7h: rolling 7-hour window
      week: fixed Mon 00:00:00 → Sun 23:59:59 UTC, inclusive

    One query per window (not per user) keeps this O(1) round-trips
    regardless of user count.
    """
    week_sql = text(
        """
        SELECT e.user_id, u.email, u.role,
               (SELECT COUNT(*) FROM events e2
                 WHERE e2.user_id = e.user_id
                   AND e2.source = 'services.llm_providers'
                   AND e2.message LIKE 'LLM call succeeded via %'
                   AND e2.ts >= datetime('now', '-7 hours')) AS used_7h,
               COUNT(*) AS used_week
        FROM events e
        JOIN users u ON u.user_id = e.user_id
        WHERE e.source = 'services.llm_providers'
          AND e.message LIKE 'LLM call succeeded via %'
          AND e.ts >= :week_start
        GROUP BY e.user_id
        ORDER BY used_week DESC
        """
    )

    from app.services.usage import _week_bounds
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).replace(tzinfo=None)
    week_start, _ = _week_bounds(now)

    rows = db.execute(week_sql, {"week_start": week_start}).fetchall()

    result = []
    for r in rows:
        used_7h = int(r[3] or 0)
        used_week = int(r[4] or 0)
        result.append(
            {
                "user_id": r[0],
                "email": r[1] or "(no email)",
                "role": int(r[2] or 2),
                "used_7h": used_7h,
                "used_week": used_week,
                # 50/7h + 100/wk are the PAID display limits (2026-09-05
                # product decision; admins share the same bars).
                "pct_7h": min(100, round(used_7h / 50 * 100)) if used_7h else 0,
                "pct_week": min(100, round(used_week / 100 * 100)) if used_week else 0,
            }
        )
    return result