"""Upload limits resolver + storage-quota math (13a, 2026-09-21).

The two-layer resolution model (doc/limits-registry.md, "The model"):

    effective_limit(user) = user override (if set) → else tier default (env var)

Every upload entry point (chunked init, legacy single, legacy bulk) calls
the SAME resolver — nobody can dodge a limit by picking a different path.
The per-user override columns (users.max_file_bytes /
users.storage_quota_bytes) are the paid-add-on infrastructure: "they paid
more → flip their override" is one SQL update via the flip-kit, no code.

Quota basis (registry §2, ratified): DECLARED-size reservation —
completed videos' file_size sum + ACTIVE staging sessions at their
declared size. Actual-staged-bytes was rejected as gameable at the
margin (abandon at 99% repeatedly); one-active-session-per-user plus
declared-size keeps check/display/actual one consistent number, and
the DELETE endpoint is the instant-release valve.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.roles import UserRole
from app.config import settings
from app.models import UploadSession, User

_GB = 1024 * 1024 * 1024


class UploadLimitError(Exception):
    """A friendly, user-facing limit violation.

    The message is safe to render in a toast verbatim (registry §1:
    "warn in an appropriate way" — specific numbers + an actionable
    suggestion, never a raw 500).
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _human_size(n: float) -> str:
    """Bytes → '1.0 GB' / '500 MB' — one decimal, never '1.07 GB' in UX copy."""
    if n >= _GB:
        return f"{n / _GB:.1f} GB"
    return f"{n / (1024 * 1024):.0f} MB"


# ── Per-file limit (registry §1) ────────────────────────────────────────────


def _tier_default_max_file_bytes(role: int) -> int:
    if role == UserRole.ADMIN:
        return int(settings.upload_max_file_admin_gb * _GB)
    # PAID and any unknown role get the PAID default (fail-safe: the
    # tighter product promise; unknown roles should never exist but a
    # resolver must never blow up mid-request).
    return int(settings.upload_max_file_paid_gb * _GB)


def max_file_size_for_role(user: User | None) -> int:
    """Effective max upload size in BYTES for a user row.

    Resolution: users.max_file_bytes override → tier env default.
    """
    if user is not None and user.max_file_bytes is not None:
        return int(user.max_file_bytes)
    role = int(user.role) if user is not None else int(UserRole.FREE)
    return _tier_default_max_file_bytes(role)


def check_file_size(user: User | None, file_size: int) -> int:
    """Validate a file size against the user's effective limit.

    Returns the effective limit on success. Raises UploadLimitError
    with the registry §1 wording on violation (the caller turns it
    into a 413 with the same message — client-side pick-time warning
    and server-side re-validation say the identical thing).
    """
    limit = max_file_size_for_role(user)
    if file_size > limit:
        raise UploadLimitError(
            f"This file is {_human_size(file_size)} — your plan allows up to "
            f"{_human_size(limit)} per video. Try compressing it (e.g. "
            "HandBrake → H.265), or ask us about larger uploads."
        )
    return limit


# ── Storage quota (registry §2) ─────────────────────────────────────────────


def _tier_default_quota_bytes(role: int) -> int:
    if role == UserRole.ADMIN:
        return int(settings.storage_quota_admin_gb * _GB)
    return int(settings.storage_quota_paid_gb * _GB)


def storage_quota_for_role(user: User | None) -> int:
    """Effective storage quota in BYTES (override → tier default)."""
    if user is not None and user.storage_quota_bytes is not None:
        return int(user.storage_quota_bytes)
    role = int(user.role) if user is not None else int(UserRole.FREE)
    return _tier_default_quota_bytes(role)


def get_user_storage_usage(db: Session, uid: str) -> dict:
    """The storage meter: completed videos + ACTIVE staging reservations.

    Returns {used_bytes, videos_bytes, staging_bytes, staging_sessions}
    — the /usage card and the quota check read the SAME function, so
    the displayed meter and the enforced check can never disagree
    (the anti-drift principle: one source of truth for the number).

    Staging counts at DECLARED size (the reservation model — the
    registry's §2/§3a rationale: actual-bytes is gameable at the
    margin; declared keeps check/display/actual consistent; the
    DELETE endpoint is the instant release).
    """
    # Completed (non-staging) videos owned by this user, via the
    # section → course chain (the repo's ownership path everywhere).
    # func.sum() in SQL — one round trip, no Python-side iteration
    # (matters when a user has hundreds of videos).
    from sqlalchemy import func as _func
    from app.models import Video, Section, Course

    videos_bytes = (
        db.execute(
            select(_func.sum(Video.file_size))
            .join(Section, Video.section_id == Section.id)
            .join(Course, Section.course_id == Course.id)
            .where(Course.user_id == uid)
        )
        .scalar() or 0
    )

    staging = (
        db.execute(
            select(_func.sum(UploadSession.declared_size)).where(
                UploadSession.user_id == uid,
                UploadSession.status == "active",
            )
        )
        .scalar() or 0
    )
    staging_sessions = (
        db.execute(
            select(UploadSession.id).where(
                UploadSession.user_id == uid,
                UploadSession.status == "active",
            )
        )
        .scalars()
        .all()
    )

    used = int(videos_bytes) + int(staging)
    return {
        "used_bytes": used,
        "videos_bytes": int(videos_bytes),
        "staging_bytes": int(staging),
        "staging_sessions": staging_sessions,
    }


def check_quota_headroom(
    db: Session, user: User | None, incoming_bytes: int
) -> dict:
    """Fail-fast quota check for an incoming upload (registry §2).

    Raises UploadLimitError with the honest math on violation — the
    error fires at init BEFORE any bytes transfer, so the user never
    wastes minutes discovering the limit at chunk 20 of 32.
    """
    uid = user.user_id if user is not None else ""
    usage = get_user_storage_usage(db, uid)
    quota = storage_quota_for_role(user)
    projected = usage["used_bytes"] + incoming_bytes
    if projected > quota:
        raise UploadLimitError(
            f"You're using {_human_size(usage['used_bytes'])} of "
            f"{_human_size(quota)} — this {_human_size(incoming_bytes)} "
            "file won't fit. Delete a video to free space."
        )
    return {
        "used_bytes": usage["used_bytes"],
        "quota_bytes": quota,
        "projected_bytes": projected,
    }