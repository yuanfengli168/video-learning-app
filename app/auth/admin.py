"""Admin authorization layer — role lookups + capability gates.

This module is the BRIDGE between:
  - Pure role/capability definitions (app/auth/roles.py — no I/O)
  - FastAPI route protection (this file — DB lookup + decorators)

Three responsibilities:

1. get_user_role_from_db(uid, db):
   Parameterized SQL lookup of user.role column. Returns UserRole enum.
   Raises if user row missing (shouldn't happen if ensure_user_row ran).

2. require_capability(capability):
   FastAPI dependency factory. Returns 403 if user's role lacks the cap.
   Usage:
       @router.post("/admin/videos")
       async def add_video(
           user: dict = Depends(require_capability(Capability.CURATE_CATALOG)),
           ...
       ):

3. ensure_user_row(uid, email, db):
   Idempotent INSERT OR IGNORE on first login. Called once per session
   from get_current_user OR as an explicit dependency. Guarantees every
   authenticated user has a row in our users table (for tracking +
   role assignment).

Security properties (2026-08-22, jacky.li):
- Role NEVER read from Firebase claims (claims are user-controlled)
- All SQL uses parameterized :uid binding (no injection vector)
- No endpoint accepts role as user input
- Admin promotion requires shell-level SQLite access (already trusted)
- ensure_user_row is idempotent (concurrent first-logins are safe)
- get_user_role lookup is cached per (uid, role_db) so admin role
  changes invalidate the cache automatically

Caching strategy:
- lru_cache on get_user_role_from_db with cache_clear() exposed for
  admin role updates (manual SQLite UPDATE invalidates next read)
- For v1.0 we don't auto-clear on UPDATE; restart picks up new role.
  This is acceptable because admin changes are rare and always done
  by hand during debugging.
"""

from functools import lru_cache
from typing import TYPE_CHECKING, Any

from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.roles import (
    Capability,
    UserRole,
    user_has_capability,
)
from app.database import get_db

# Type hint only — avoids circular import at module load.
# `get_current_user` is defined in app/auth/dependencies.py which imports
# nothing from this module, so runtime import inside the dependency
# factory function is safe.
if TYPE_CHECKING:
    from app.auth.dependencies import get_current_user


# Module-level alias for get_current_user. Resolved lazily to avoid
# circular import at module load time (admin.py <-> dependencies.py).
def _get_current_user_dep():
    """Lazy resolver for get_current_user (avoids circular import)."""
    from app.auth.dependencies import get_current_user
    return get_current_user


# ─────────────────────────────────────────────────────────────────────────
# 1. DB-backed role lookup (parameterized SQL, no injection)
# ─────────────────────────────────────────────────────────────────────────

# Cached role lookup. Cache key = (uid, role_db_value) so an UPDATE
# that changes the role invalidates naturally on next call.
#
# Why include role_db in the cache key:
#   - If admin updates role from 2 -> 0 in SQLite, the next call sees
#     a new role_db value and caches the new mapping. No stale cache.
#   - Avoids manual cache invalidation in 99% of cases.
#
# When to call cache_clear():
#   - Bulk role changes (e.g. promoting 100 users at once)
#   - After manual SQLite intervention that the running process doesn't see
#   - Tests (we expose the function but don't auto-call from routes)


@lru_cache(maxsize=10000)
def _lookup_role_cached(uid: str, role_db: int) -> UserRole:
    """Resolve role_db (int from DB) to UserRole enum. Cached.

    Called only from get_user_role_from_db below; not meant to be
    called directly because the role_db value is part of the cache
    key (so calling with a stale role_db gives a stale UserRole).
    """
    try:
        return UserRole(role_db)
    except ValueError:
        # Unknown int value (corrupted DB). Default to FREE for safety.
        return UserRole.FREE


def clear_role_cache() -> None:
    """Manually clear the role cache. Call after bulk role changes."""
    _lookup_role_cached.cache_clear()


def get_user_role_from_db(uid: str, db: Session) -> UserRole:
    """Look up user's role from the users table.

    Returns UserRole.FREE if the user row doesn't exist (defensive —
    shouldn't happen if ensure_user_row ran first).

    Args:
        uid: Firebase UID
        db: SQLAlchemy session

    Returns:
        UserRole enum (ADMIN/PAID/FREE, never None)

    Security: uses parameterized SQL (text + bindparams). No injection.
    """
    if not uid:
        # Empty uid shouldn't happen but fail-safe to FREE
        return UserRole.FREE

    row = db.execute(
        text("SELECT role FROM users WHERE user_id = :uid"),
        {"uid": uid},
    ).fetchone()

    if row is None:
        # User row missing — treat as FREE (don't 500 on data inconsistency)
        return UserRole.FREE

    return _lookup_role_cached(uid, row[0])


# ─────────────────────────────────────────────────────────────────────────
# 2. ensure_user_row — idempotent first-login user creation
# ─────────────────────────────────────────────────────────────────────────


def ensure_user_row(uid: str, email: str | None, db: Session) -> None:
    """Create a users row on first authenticated login. Idempotent.

    Safe to call on every authenticated request:
      - If row exists: UPDATE email (in case user changed it in Firebase)
      - If row missing: INSERT with default role=2 (FREE)

    Uses ON CONFLICT DO UPDATE (UPSERT) for atomicity + idempotency.
    Concurrent first-logins from same user (e.g. parallel tabs) are safe
    because the conflict clause resolves to UPDATE.

    Args:
        uid: Firebase UID (primary key)
        email: Firebase email (may be None for privacy)
        db: SQLAlchemy session

    Security: parameterized SQL only. No string interpolation.
    """
    if not uid:
        return  # nothing to do without a uid

    try:
        db.execute(
            text("""
                INSERT INTO users (user_id, email, role)
                VALUES (:uid, :email, 2)
                ON CONFLICT(user_id) DO UPDATE SET
                    email = COALESCE(excluded.email, users.email),
                    updated_at = CURRENT_TIMESTAMP
            """),
            {"uid": uid, "email": email},
        )
        db.commit()
    except IntegrityError:
        # Shouldn't happen (UPSERT handles all conflicts) but log + rollback
        db.rollback()
        # Swallow: this is best-effort bookkeeping, not critical to auth flow


# ─────────────────────────────────────────────────────────────────────────
# 3. require_capability — FastAPI dependency factory
# ─────────────────────────────────────────────────────────────────────────


def require_capability(capability: Capability):
    """FastAPI dependency factory: 403 if user lacks the capability.

    Usage:
        @router.post("/api/admin/videos/youtube")
        async def add_video(
            user: dict = Depends(require_capability(Capability.CURATE_CATALOG)),
            db: Session = Depends(get_db),
        ):
            ...

    Args:
        capability: The Capability enum value to require.

    Returns:
        A dependency function that returns the verified user claims dict
        on success, or raises HTTPException(403) on failure.

    Security:
      - Role lookup is DB-backed (parameterized SQL)
      - Role NEVER trusted from Firebase claims
      - Returns 403 (not 404) to avoid revealing endpoint existence
    """
    # Local import to avoid circular dependency at module load.
    # `dependencies` imports nothing from `admin`, so this is safe.
    from app.auth.dependencies import get_current_user

    async def _verify(
        user: dict[str, Any] = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        uid = user.get("uid", "") if isinstance(user, dict) else ""
        if not uid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No uid in token claims",
            )

        # Ensure user row exists (auto-create on first login).
        # Cheap: UPSERT with ON CONFLICT DO UPDATE.
        ensure_user_row(uid, user.get("email"), db)

        role = get_user_role_from_db(uid, db)
        if not user_has_capability(role, capability):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Missing capability: {capability.value}. "
                    f"Your role is '{role.name.lower()}'."
                ),
            )
        return user

    return _verify



# ─────────────────────────────────────────────────────────────────────────
# 4. require_admin — convenience for the most common gate
# ─────────────────────────────────────────────────────────────────────────


def require_admin(
    user: dict[str, Any] = Depends(_get_current_user_dep),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Require role=ADMIN. Convenience wrapper around require_capability.

    Use this for admin-only routes that don't have a more specific
    capability name. For routes that should be accessible to non-admin
    roles later (e.g. support_admin), use require_capability() instead.

    Returns:
        The user claims dict on success.

    Raises:
        HTTPException 403 if user is not ADMIN.
    """
    uid = user.get("uid", "") if isinstance(user, dict) else ""
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No uid in token claims",
        )

    ensure_user_row(uid, user.get("email"), db)

    role = get_user_role_from_db(uid, db)
    if role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Admin access required. Your role is "
                f"'{role.name.lower()}'."
            ),
        )
    return user
