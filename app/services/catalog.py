"""Catalog service — visibility-filtered video queries.

The MVP2 catalog is admin-curated YouTube videos, browsed by all users
based on their role. Each video has a `visibility` field (PUBLIC,
PAID_ONLY, ADMIN_ONLY) and a user's role determines which tiers they can
see:

  FREE       → PUBLIC only
  PAID       → PUBLIC + PAID_ONLY
  ADMIN      → PUBLIC + PAID_ONLY + ADMIN_ONLY (sees everything)

This module provides:

  - `visible_videos_for_role(db, role)` — returns a SQLAlchemy Select
    for the videos visible to the given role, ordered by created_at desc.
  - `visible_videos_for_user(db, user)` — convenience wrapper that
    looks up the role via the role cache, returns the same Select.

Visibility is an int 0/1/2 — `WHERE visibility <= max` works because
lower numbers are MORE public.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Select, select

from app.auth.admin import get_user_role_from_db
from app.auth.roles import UserRole, max_visibility_for_role
from app.models import Video

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def visible_videos_for_role(
    db: "Session",
    role: UserRole | int | None,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> Select:
    """Return a SQLAlchemy Select for videos visible to the given role.

    Filters by visibility tier (lower=more public) and orders by
    created_at desc (newest first — admin-curated flow).

    Args:
      db: SQLAlchemy session.
      role: UserRole enum, int 0/1/2, or None (anonymous).
      limit: Optional max rows.
      offset: Rows to skip (pagination).

    The Select is unexecuted; callers can chain .limit/.offset or just
    .scalars().all()/.first().
    """
    max_v = max_visibility_for_role(role)
    q = (
        select(Video)
        .where(Video.visibility <= max_v.value)
        .order_by(Video.created_at.desc())
    )
    if offset:
        q = q.offset(offset)
    if limit is not None:
        q = q.limit(limit)
    return q


def visible_videos_for_user(
    db: "Session",
    user: dict | None,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> Select:
    """Return a SQLAlchemy Select for videos visible to the given user.

    Convenience wrapper that resolves the user's role from the DB
    (cached) and calls visible_videos_for_role.

    Args:
      db: SQLAlchemy session.
      user: The decoded Firebase claims dict (from get_current_user_*).
        Must contain 'uid'. If None or missing uid, treated as anonymous
        (FREE role, PUBLIC only).
    """
    if user and user.get("uid"):
        role = get_user_role_from_db(user["uid"], db)
    else:
        role = UserRole.FREE
    return visible_videos_for_role(db, role, limit=limit, offset=offset)


def count_visible_videos_for_role(
    db: "Session",
    role: UserRole | int | None,
) -> int:
    """Return count of videos visible to the given role. Cheap — one COUNT query."""
    from sqlalchemy import func

    max_v = max_visibility_for_role(role)
    return db.execute(
        select(func.count(Video.id)).where(Video.visibility <= max_v.value)
    ).scalar_one()
