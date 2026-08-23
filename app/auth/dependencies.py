"""FastAPI auth dependencies for protecting API routes."""

from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.admin import ensure_user_row
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
        return None

    # Only ensure the row if we have a uid (defense in depth)
    uid = claims.get("uid", "")
    if uid:
        ensure_user_row(uid, claims.get("email"), db)

    return claims


def get_user_id(claims: dict[str, Any]) -> str:
    """Extract the user ID from decoded Firebase claims."""
    return claims.get("uid", "")