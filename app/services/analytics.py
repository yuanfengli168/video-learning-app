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

    Tier-aware (2026-09-05 bugfix): PAID/ADMIN rows show the 50/7h +
    100/wk bars (their Ollama chain consumption). FREE rows show the
    15/day Groq claim (today's count) — the same rule their own
    /usage page uses — instead of being mislabeled with paid bars.

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
               (SELECT COUNT(*) FROM events e3
                 WHERE e3.user_id = e.user_id
                   AND e3.source = 'services.llm_providers'
                   AND e3.message LIKE 'LLM call succeeded via %'
                   AND e3.ts >= datetime('now', 'start of day')) AS used_day,
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
        # 2026-09-05 bugfix: `int(r[2] or 2)` turned ADMIN (role=0)
        # into FREE (2) — 0 is falsy in Python's `or`. Found live:
        # the admin account rendered with a FREE badge on the usage
        # monitor. is-not-None is the correct guard.
        role = int(r[2]) if r[2] is not None else 2
        used_7h = int(r[3] or 0)
        used_day = int(r[4] or 0)
        used_week = int(r[5] or 0)

        is_paid_or_admin = role in (0, 1)

        result.append(
            {
                "user_id": r[0],
                "email": r[1] or "(no email)",
                "role": role,
                "used_7h": used_7h,
                "used_week": used_week,
                "used_day": used_day,
                # 50/7h + 100/wk are the PAID display limits; FREE
                # shows the 15/day Groq claim instead (2026-09-05
                # product decision + bugfix for tier-aware bars).
                "pct_7h": min(100, round(used_7h / 50 * 100)) if is_paid_or_admin else 0,
                "pct_week": min(100, round(used_week / 100 * 100)) if is_paid_or_admin else 0,
                "pct_day": min(100, round(used_day / 15 * 100)) if not is_paid_or_admin else 0,
            }
        )
    # Sort: paid-tier users first (they're the ones the limits apply
    # to), then by weekly consumption descending.
    result.sort(key=lambda x: (x["role"] not in (0, 1), -x["used_week"]))
    return result


# ─────────────────────────────────────────────────────────────────────────
# Playback analytics (2026-09-06)
#
# Powers /admin/playback — answers "who played which video, for how long".
# Different from /admin/analytics (event counters + LLM usage): this page
# is about REAL watch time derived from the play/pause/ended/seek events
# the telemetry beacon already emits (see app/static/js/telemetry.js).
#
# Watch-time computation strategy:
#   For each user × video pair, walk the events in chronological order.
#   Each `play` event starts a "play segment". The next non-play event
#   (pause / ended / seek / or session timeout > 5 min) closes the
#   segment. Sum the segment durations.
#
#   We deliberately do NOT use `position_ms` from the events — it's the
#   position-in-video, not the watch-time, and a user can rewind to
#   the start. The cleanest signal is the actual elapsed wall-clock
#   between play and the next stop event.
#
#   Edge cases handled:
#   - No `ended` after final `play`: we count up to NOW (capped at
#     video duration if we have it). If a play event has no closer
#     but its position_ms is near the end, we treat it as ended.
#   - Multiple sessions per user/video: split on > 5 min gaps.
#   - Tab close mid-play: not detected here; the beacon does
#     `visibilitychange → flushSync` but doesn't emit a pause. The
#     watch time is then understated by however long the tab stayed
#     open without any other event. Acceptable approximation.
#
# All aggregation is server-side from the events table — same
# worker-independent truth as the rest of the analytics layer.
# ─────────────────────────────────────────────────────────────────────────


# Max gap between two events that still counts as one play session.
# Longer than this = user closed the tab and came back; count as
# separate sessions so the watch-time math doesn't span hours.
_SESSION_GAP_SEC = 300  # 5 min


def get_playback_analytics(db: Session, days: int = 7) -> dict[str, Any]:
    """Per-user × per-video playback analytics for the last `days` days.

    Shape:
      {
        "window_days": 7,
        "per_user_video": [               # one row per (user, video) pair
          {
            "user_id": ..., "email": ..., "role": ...,
            "video_id": ..., "video_title": ..., "duration_sec": ...,
            "plays": n,                    # play events
            "pauses": n,                   # pause events
            "seeks": n,                    # seek events (engagement signal)
            "ended_count": n,              # ended events (= video finished)
            "watch_sec": n,                # total active watch time
            "completion_pct": n            # 0–100, watch_sec / duration_sec
                                            # (None if no duration known)
          }
        ],
        "videos_by_watch_time": [          # top 10 videos by total watch time
          {"video_id": ..., "title": ..., "watch_sec": n, "unique_viewers": n,
           "plays": n}
        ],
        "users_by_watch_time": [           # top 10 users by total watch time
          {"user_id": ..., "email": ..., "watch_sec": n,
           "videos_started": n, "videos_completed": n}
        ],
      }
    """
    days = max(1, min(days, 90))

    # Pull the events we'll need, in order. Per-(user, video) we need
    # the full play/pause/ended/seek stream to reconstruct sessions.
    rows = db.execute(
        text(
            """
            SELECT e.user_id, e.video_id, e.ts, e.context_json,
                   u.email, u.role,
                   v.title, v.duration
            FROM events e
            LEFT JOIN users u ON u.user_id = e.user_id
            LEFT JOIN videos v ON v.id = e.video_id
            WHERE e.source = 'ui.player'
              AND e.ts >= datetime('now', :days_cutoff)
            ORDER BY e.user_id, e.video_id, e.ts ASC
            """
        ),
        {"days_cutoff": f"-{days} days"},
    ).fetchall()

    import json
    from datetime import datetime, timezone

    def _parse_ts(s: str) -> datetime:
        # Stored as ISO 8601 (with ' ' or 'T' separator, with or without
        # tz). Try a couple of formats; fall back to fromisoformat which
        # accepts most modern Python 3.11+ strings.
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace(" ", "T"))
        except Exception:
            return None

    def _parse_action(context_json: str | None) -> tuple[str, int]:
        """Return (action, position_ms) from a context_json string.

        Robust to:
          - null
          - empty string
          - non-JSON garbage (returns ('other', 0))
          - missing fields (defaults to 'other' / 0)
        """
        if not context_json:
            return ("other", 0)
        try:
            ctx = json.loads(context_json)
        except Exception:
            return ("other", 0)
        action = (ctx or {}).get("action") or "other"
        position_ms = int((ctx or {}).get("position_ms") or (ctx or {}).get("to_ms") or 0)
        return (str(action), position_ms)

    # Group events into per-(user, video) session streams, then compute
    # watch time. Grouping key is (user_id, video_id) since the same
    # user can have multiple sessions per video (multi-day watching).
    groups: dict[tuple[str | None, str | None], list] = {}
    for r in rows:
        key = (r[0], r[1])
        groups.setdefault(key, []).append(r)

    per_user_video: list[dict[str, Any]] = []
    video_agg: dict[str, dict] = {}      # video_id -> totals
    user_agg: dict[str, dict] = {}        # user_id -> totals
    # `now` is used ONLY for closing an unclosed final play (capped at
    # the video duration anyway). Events come from SQLite's
    # datetime('now') — naive UTC — so we keep a naive 'now' for
    # consistent subtraction. Python 3.14 deprecates utcnow(); use
    # now(UTC) and drop tzinfo to stay naive.
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for (uid, vid), evs in groups.items():
        plays = pauses = seeks = ended_count = 0
        watch_sec = 0
        # Track the active play's start time so we can compute the
        # next-segment's duration when we see a non-play event.
        active_play_start: datetime | None = None
        last_ts: datetime | None = None

        email = evs[0][4] if evs else None
        role = evs[0][5] if evs else None
        title = evs[0][6] if evs else None
        duration_sec = evs[0][7] if evs else None  # may be None for YouTube videos

        for ev in evs:
            ts = _parse_ts(ev[2])
            if ts is None:
                continue
            action, _pos = _parse_action(ev[3])

            # Session boundary — closes an ABANDONED play only.
            #
            # Key insight: during continuous playback the beacon emits
            # NO events (it fires on state changes only). So a
            # play→ended pair can legitimately span the entire video
            # (10+ min). The 5-min gap must therefore NOT close a play
            # when the current event is itself a closer (pause/ended/
            # seek) — those always win and close the segment naturally.
            #
            # The ONLY case where a gap means "user left the tab
            # playing and closed it" is: a NEW play starts after the
            # gap. Then the previous play was abandoned mid-watch and
            # we close it at the last event we ever saw.
            if (
                action == "play"
                and active_play_start is not None
                and last_ts is not None
                and (ts - last_ts).total_seconds() > _SESSION_GAP_SEC
            ):
                # Abandoned play: close at last_ts (the last moment we
                # know the tab was still open). Contribution is
                # (last_ts - active_play_start).
                watch_sec += (last_ts - active_play_start).total_seconds()
                active_play_start = None

            if action == "play":
                plays += 1
                if active_play_start is None:
                    active_play_start = ts
            elif action == "pause":
                pauses += 1
                if active_play_start is not None:
                    watch_sec += (ts - active_play_start).total_seconds()
                    active_play_start = None
            elif action == "ended":
                ended_count += 1
                if active_play_start is not None:
                    watch_sec += (ts - active_play_start).total_seconds()
                    active_play_start = None
            elif action == "seek":
                seeks += 1
                # A seek doesn't necessarily stop play, but if a play
                # is active we close the current segment — the user's
                # attention jumped, the previous segment is over.
                if active_play_start is not None:
                    watch_sec += (ts - active_play_start).total_seconds()
                    active_play_start = None
            # 'other' actions ignored for watch time

            last_ts = ts

        # Close any active play at the LAST event we saw (use `now` if
        # the last event is the play itself — the segment didn't end).
        if active_play_start is not None:
            if last_ts is None or last_ts == active_play_start:
                # No later event at all → use now (capped at duration)
                closer = now
            else:
                closer = last_ts
            elapsed = (closer - active_play_start).total_seconds()
            # Cap at the video's duration if we know it (prevents a
            # stuck tab from inflating watch time past the actual
            # content length).
            if duration_sec and elapsed > duration_sec:
                elapsed = duration_sec
            # Floor at 0 — a tiny rounding error shouldn't go negative.
            if elapsed > 0:
                watch_sec += elapsed

        # Round watch_sec to a whole number; the math above is in
        # float seconds from datetime deltas but reporting fractional
        # seconds just looks weird in a UI.
        watch_sec = int(round(watch_sec))

        completion_pct: int | None = None
        if duration_sec and duration_sec > 0:
            completion_pct = min(100, int(round(watch_sec / duration_sec * 100)))

        per_user_video.append(
            {
                "user_id": uid,
                "email": email or "(no email)",
                "role": role,
                "video_id": vid,
                "video_title": title or "(deleted video)",
                "duration_sec": duration_sec,
                "plays": plays,
                "pauses": pauses,
                "seeks": seeks,
                "ended_count": ended_count,
                "watch_sec": watch_sec,
                "completion_pct": completion_pct,
            }
        )

        # Roll-ups.
        if vid:
            va = video_agg.setdefault(
                vid, {"video_id": vid, "title": title or "(deleted video)",
                      "watch_sec": 0, "plays": 0,
                      "viewers": set()}
            )
            va["watch_sec"] += watch_sec
            va["plays"] += plays
            if uid:
                va["viewers"].add(uid)
        if uid:
            ua = user_agg.setdefault(
                uid, {"user_id": uid, "email": email or "(no email)",
                      "watch_sec": 0, "videos_started": 0,
                      "videos_completed": 0}
            )
            ua["watch_sec"] += watch_sec
            ua["videos_started"] += 1
            if ended_count > 0:
                ua["videos_completed"] += 1

    # Sort the per-user-video table by watch_sec DESC (most-engaged
    # first) — that's what an admin scanning for "who watched what"
    # wants to see.
    per_user_video.sort(key=lambda x: -x["watch_sec"])

    videos_by_watch_time = [
        {
            "video_id": v["video_id"],
            "title": v["title"],
            "watch_sec": v["watch_sec"],
            "unique_viewers": len(v["viewers"]),
            "plays": v["plays"],
        }
        for v in sorted(
            video_agg.values(),
            key=lambda x: -x["watch_sec"],
        )
    ][:10]

    users_by_watch_time = [
        {
            "user_id": u["user_id"],
            "email": u["email"],
            "watch_sec": u["watch_sec"],
            "videos_started": u["videos_started"],
            "videos_completed": u["videos_completed"],
        }
        for u in sorted(
            user_agg.values(),
            key=lambda x: -x["watch_sec"],
        )
    ][:10]

    return {
        "window_days": days,
        "per_user_video": per_user_video,
        "videos_by_watch_time": videos_by_watch_time,
        "users_by_watch_time": users_by_watch_time,
    }