#!/usr/bin/env bash
# migrate_courses_to_channel.sh — one-time backfill (2026-09-08).
#
# Attaches every PUBLIC admin-curated course (one with at least one
# youtube video) to a channel, so existing content shows up in the new
# /catalog browse surface immediately after the channel feature ships.
#
# Usage:
#   bash scripts/migrate_courses_to_channel.sh "General" [slug]
#
# Defaults: name "General", slug "general".
#
# What it does:
#   1. Creates (or reuses) the channel by name.
#   2. Finds courses that have youtube videos but no channel yet.
#   3. Sets courses.channel_id on them.
#
# Idempotent: re-running reuses the channel and skips courses that
# already have a channel.

set -euo pipefail

DB_PATH="${DB_PATH:-/Volumes/Storage-Fast-NVMe/video_learning.db}"
CHANNEL_NAME="${1:-General}"
CHANNEL_SLUG="${2:-general}"

if [ ! -f "$DB_PATH" ]; then
    echo "DB not found at $DB_PATH (set DB_PATH env to override)" >&2
    exit 1
fi

echo "→ Using DB: $DB_PATH"

# 1. Create or reuse the channel (by slug uniqueness; sqlite3 in shell
#    can't easily do conditional insert, so check-then-insert)
EXISTING=$(sqlite3 "$DB_PATH" "SELECT id FROM channels WHERE slug='$CHANNEL_SLUG' LIMIT 1;")
if [ -z "$EXISTING" ]; then
    CHANNEL_ID=$(uuidgen | tr 'A-Z' 'a-z')
    sqlite3 "$DB_PATH" "INSERT INTO channels (id, name, slug, description, icon_url, user_id) VALUES ('$CHANNEL_ID', '$CHANNEL_NAME', '$CHANNEL_SLUG', 'Curated videos from across the catalog.', NULL, NULL);"
    echo "✓ Created channel '$CHANNEL_NAME' ($CHANNEL_SLUG, id=$CHANNEL_ID)"
else
    CHANNEL_ID="$EXISTING"
    echo "✓ Reusing existing channel '$CHANNEL_NAME' (id=$CHANNEL_ID)"
fi

# 2. Courses with youtube videos but no channel
ORPHANS=$(sqlite3 "$DB_PATH" "
    SELECT DISTINCT c.id, c.title
    FROM courses c
    JOIN sections s   ON s.course_id  = c.id
    JOIN videos v     ON v.section_id = s.id
    WHERE v.youtube_id IS NOT NULL
      AND c.channel_id IS NULL;")

if [ -z "$ORPHANS" ]; then
    echo "✓ No un-channeled courses with YouTube videos — nothing to do."
    exit 0
fi

echo "$ORPHANS" | while IFS='|' read -r course_id title; do
    [ -z "$course_id" ] && continue
    sqlite3 "$DB_PATH" "UPDATE courses SET channel_id='$CHANNEL_ID' WHERE id='$course_id';"
    echo "  ✓ '$title' → $CHANNEL_NAME"
done

echo "✓ Done. Visit /catalog to see the result."