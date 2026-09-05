"""FastAPI auth dependencies for protecting API routes."""

from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.admin import ensure_user_row, get_user_role_from_db
from app.auth.firebase_admin import verify_token
from app.auth.session import get_token_from_cookie
from app.database import get_db

security = HTTPBearer(auto_error=False)


def _extract_token(request: Request, credentials: HTTPAuthorizationCredentials | None) -> str | None:
    """Extract the Firebase ID token from cookie or Authorization header.

    Cookie takes priority (for browser page loads), then Bearer header (for API calls).
    """
    # Try cookie first
    cookie_token = get_token_from_cookie(request)
    if cookie_token:
        return cookie_token

    # Fall back to Authorization header
    if credentials and credentials.credentials:
        return credentials.credentials

    return None


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """FastAPI dependency that verifies the Firebase ID token.

    Checks the session cookie first, then the Authorization header.
    Verifies the token with Firebase Admin SDK and returns the decoded claims.

    Side effect: calls `ensure_user_row(uid, email, db)` so the user gets
    a row in the `users` table on their first authenticated request —
    NOT only when they hit an admin endpoint. This is the fix for the
    "sign in but no row in DB" bug (Day 1 oversight).

    Raises:
        HTTPException 401: If no token is provided or the token is invalid.
    """
    token = _extract_token(request, credentials)

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Provide a Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = verify_token(token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Idempotent UPSERT — first login creates the row, subsequent logins
    # are a no-op (ON CONFLICT DO UPDATE just refreshes the email).
    uid = claims.get("uid", "")
    if uid:
        ensure_user_row(uid, claims.get("email"), db)

    # 2026-09-05 role-enrichment fix: Firebase claims contain uid/email
    # but NEVER a role (roles live only in the local users table). Every
    # `user.get("role", 2)` consumer downstream (tier LLM chain in
    # _run_generate_job, user_can_access_video gates in chat/generation/
    # videos routers) was silently defaulting to FREE for every user.
    # Observed live: a PAID user's generate ran the FREE chain
    # (groq-only) and failed with "All 1 provider(s) failed" while the
    # PAID chain (ollama → openai) would have worked. Join the DB role
    # into the dict here so all consumers see the real tier. The
    # require_capability dependency already does its own DB lookup
    # (lru_cached), so this adds at most one cheap cached query per
    # authenticated request.
    if uid:
        try:
            claims["role"] = int(get_user_role_from_db(uid, db))
        except Exception:
            # Never fail auth because a role lookup broke — consumers'
            # default (FREE) keeps the app usable.
            claims.pop("role", None)

    return claims


async def get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> dict[str, Any] | None:
    """Optional auth — returns user claims if token is valid, None otherwise.

    Checks the session cookie first, then the Authorization header.
    Useful for routes that behave differently for authenticated vs anonymous users.

    Side effect: same as get_current_user — calls ensure_user_row on
    authenticated requests so the user row exists for downstream
    role/capability lookups.
    """
    token = _extract_token(request, credentials)
    if not token:
        return None

    try:
        claims = verify_token(token)
    except ValueError:
        # Documented contract: invalid/expired/revoked tokens raise ValueError.
        return None
    except Exception:  # noqa: BLE001 — see Day 9 hotfix note
        # Day 9 hotfix: also swallow Firebase SDK errors (UNAVAILABLE,
        # InternalError, etc.). These are network/availability issues,
        # not token-quality issues. The user is treated as anonymous
        # for this request — downstream code can render the "Sign in"
        # prompt instead of crashing with a 500.
        # See app/middleware_session.py for the matching fix.
        return None

    # Only ensure the row if we have a uid (defense in depth)
    uid = claims.get("uid", "")
    if uid:
        ensure_user_row(uid, claims.get("email"), db)
        # Same role-enrichment as get_current_user (2026-09-05) so
        # optional-auth consumers see the real tier too.
        try:
            claims["role"] = int(get_user_role_from_db(uid, db))
        except Exception:
            claims.pop("role", None)

    return claims


def get_user_id(claims: dict[str, Any]) -> str:
    """Extract the user ID from decoded Firebase claims."""
    return claims.get("uid", "")