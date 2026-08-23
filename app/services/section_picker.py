"""Section picker helper — where to put a newly added video.

Single responsibility: given an admin uid and an optional requested
section_id, decide which Section row the new video lands in. Used by:

  * app/routers/admin.py  — POST /api/admin/videos/youtube
  * app/routers/frontend.py — /admin/upload page (to pre-populate the form)

Priority (matches what the admin form does):
  1. Explicit requested_section_id from the admin form (a UUID). Verify
     it belongs to a Section whose Course.user_id == uid (defense — admin
     can't drop a video into another admin's course by guessing).
  2. First Section of the admin's first Course (alphabetical by Course
     title, then by Section.order_index). Reasonable default for a
     brand-new admin who hasn't picked anything yet.
  3. Auto-create "Default Catalog" Course + "Uncategorized" Section for
     the admin. This keeps the FK valid and lets the admin re-organize
     later.

Why a separate module:
  - Same logic needed in 2 places (admin POST + frontend render)
  - Pure function over db + uid — easy to unit-test without spinning up
    the FastAPI app
  - Keeps the admin router readable (the resolution is 20 lines of
    branching that don't belong in the route handler)
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Course, Section


def resolve_section_for_new_video(
    db: Session,
    uid: str,
    requested_section_id: Optional[str] = None,
) -> Section:
    """Pick a Section for a new video, creating one if the admin has none.

    Args:
        db: SQLAlchemy session.
        uid: The admin's Firebase uid (must match Section -> Course.user_id).
        requested_section_id: Optional UUID the admin picked from the form.
            If provided, must belong to a Course owned by `uid`.

    Returns:
        A Section row (already flushed so it has an id).

    Raises:
        ValueError: If requested_section_id is provided but does not exist
            or belongs to a different admin's course.
    """
    # 1. Honor the admin's explicit pick (if it's theirs)
    if requested_section_id:
        section = db.get(Section, requested_section_id)
        if section is None:
            raise ValueError(
                f"Section {requested_section_id!r} does not exist."
            )
        # Defense in depth: prevent admins from dropping videos into
        # other admins' courses by guessing section UUIDs.
        if section.course.user_id != uid:
            raise ValueError(
                f"Section {requested_section_id!r} does not belong "
                f"to your account."
            )
        return section

    # 2. Fall back to the admin's first Section (alphabetical course,
    #    then order_index, then created_at as final tiebreaker).
    section = (
        db.execute(
            select(Section)
            .join(Course, Course.id == Section.course_id)
            .where(Course.user_id == uid)
            .order_by(Course.title.asc(), Section.order_index.asc(), Section.created_at.asc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if section is not None:
        return section

    # 3. No sections at all — create a default "Default Catalog" course
    #    + "Uncategorized" section so the FK is satisfied. The admin can
    #    re-organize later via the dashboard.
    course = Course(title="Default Catalog", user_id=uid)
    db.add(course)
    db.flush()  # need course.id for the FK
    section = Section(
        title="Uncategorized",
        course_id=course.id,
        order_index=0,
    )
    db.add(section)
    db.flush()
    return section


def ensure_admin_has_a_section(db: Session, uid: str) -> None:
    """Make sure the admin has at least one Section row.

    Called by the frontend /admin/upload page so the dropdown isn't
    empty on first load. No-op when sections already exist.

    Why not just call resolve_section_for_new_video and discard:
    that helper has the side-effect of *returning* a Section and
    flushing. We just want the side-effect here (ensure existence).
    Calling it without a requested_section_id would still work, but
    this name communicates intent more clearly at the call site.
    """
    has_any = (
        db.execute(
            select(Section)
            .join(Course, Course.id == Section.course_id)
            .where(Course.user_id == uid)
            .limit(1)
        )
        .scalars()
        .first()
    )
    if has_any is None:
        # Reuses the same auto-create path. The returned Section is
        # discarded; we only care that the row exists.
        resolve_section_for_new_video(db=db, uid=uid, requested_section_id=None)
        db.commit()