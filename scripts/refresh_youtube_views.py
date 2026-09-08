#!/usr/bin/env python3
"""refresh_youtube_views.py — nightly YouTube view-count refresh (2026-09-09).

Powers the dashboard 'Top Viewed' tab. The tab SORTS LIVE at render
time, but the view-count DATA is snapshotted once a night by this job
(00:00 SGT via launchd — the Mac runs Singapore time, so Hour=0 in
the plist IS midnight SGT; no timezone math needed).

Quota: videos.list accepts up to 50 ids per call = 1 unit each, so
a 23-video catalog costs 1 of the 10,000 daily units. Even 2,000
videos would cost 40 units — negligible.

Idempotent + safe: re-running is harmless; API failures log and exit
non-zero so launchd shows the failure (never crashes anything).

Usage:
    venv/bin/python scripts/refresh_youtube_views.py            # run now
    venv/bin/python scripts/refresh_youtube_views.py --dry-run  # print only
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the app importable when run from anywhere (launchd sets no cwd)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    from app.database import SessionLocal
    from app.models import Video
    from app.services.youtube_api import (
        YouTubeAPIKeyMissing,
        YouTubeAPIClient,
    )
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="[refresh-views] %(message)s",
    )
    log = logging.getLogger(__name__)

    db = SessionLocal()
    try:
        videos = (
            db.query(Video)
            .filter(Video.youtube_id.is_not(None))
            .all()
        )
        ids = [v.youtube_id for v in videos]
        if not ids:
            log.info("No YouTube videos in the catalog — nothing to do.")
            return 0

        try:
            client = YouTubeAPIClient()
        except YouTubeAPIKeyMissing:
            log.error(
                "YOUTUBE_API_KEY not set — cannot refresh view counts. "
                "Existing values stay; Top Viewed still sorts (NULLs last)."
            )
            return 1

        by_id = client.get_view_counts(ids)
        log.info(f"Fetched {len(by_id)}/{len(ids)} view counts from YouTube.")

        updated = 0
        for v in videos:
            count = by_id.get(v.youtube_id)
            if count is not None and v.view_count != count:
                log.info(f"  {v.youtube_id}: {v.view_count} → {count}")
                if not dry_run:
                    v.view_count = count
                updated += 1

        if dry_run:
            log.info(f"[dry-run] would update {updated} video(s); nothing written.")
        else:
            db.commit()
            log.info(f"Updated {updated} video(s); {len(ids) - updated} unchanged.")
        return 0
    except Exception as exc:
        log.exception(f"Refresh failed: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())