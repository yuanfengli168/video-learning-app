"""Auth router — endpoints for verifying tokens and getting user info."""

from typing import Any

from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
async def get_me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Return the current authenticated user's claims.

    Requires a valid Firebase ID token in the Authorization header.
    """
    return {
        "uid": user.get("uid", ""),
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "picture": user.get("picture", ""),
        "email_verified": user.get("email_verified", False),
    }


@router.get("/verify")
async def verify(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    """Simple token verification endpoint.

    Returns 200 if the token is valid, 401 otherwise (handled by dependency).
    """
    return {"status": "verified", "uid": user.get("uid", "")}