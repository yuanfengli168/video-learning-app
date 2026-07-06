"""FastAPI auth dependencies for protecting API routes."""

from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.firebase_admin import verify_token

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    """FastAPI dependency that verifies the Firebase ID token.

    Extracts the Bearer token from the Authorization header, verifies it
    with Firebase Admin SDK, and returns the decoded claims.

    Raises:
        HTTPException 401: If no token is provided or the token is invalid.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Provide a Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = verify_token(credentials.credentials)
        return claims
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_user_optional(
    request: Request,
) -> dict[str, Any] | None:
    """Optional auth — returns user claims if token is valid, None otherwise.

    Useful for routes that behave differently for authenticated vs anonymous users.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header.removeprefix("Bearer ")
    try:
        return verify_token(token)
    except ValueError:
        return None


def get_user_id(claims: dict[str, Any]) -> str:
    """Extract the user ID from decoded Firebase claims."""
    return claims.get("uid", "")