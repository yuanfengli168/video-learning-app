"""Session management — exchange Firebase ID token for an httpOnly cookie.

Flow:
1. User signs in via AuthKit (frontend Firebase Auth)
2. Frontend gets the Firebase ID token
3. Frontend POSTs the token to /api/auth/session
4. Backend verifies the token and sets an httpOnly cookie
5. Subsequent requests (page loads, API calls) include the cookie automatically
6. get_current_user_optional reads the cookie instead of the Authorization header
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.auth.firebase_admin import verify_token

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Cookie name for the Firebase ID token
COOKIE_NAME = "fb_token"
# Cookie max age in seconds (1 hour — Firebase tokens expire in 1 hour)
COOKIE_MAX_AGE = 3600


class TokenRequest(BaseModel):
    id_token: str


@router.post("/session")
async def create_session(body: TokenRequest, response: Response) -> dict[str, str]:
    """Exchange a Firebase ID token for an httpOnly session cookie.

    Called by the frontend after AuthKit login.
    """
    try:
        claims = verify_token(body.id_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=401, detail=f"Invalid token: {exc}"
        ) from exc

    response.set_cookie(
        key=COOKIE_NAME,
        value=body.id_token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # True in production (HTTPS)
    )

    return {
        "status": "ok",
        "uid": claims.get("uid", ""),
        "email": claims.get("email", ""),
    }


@router.delete("/session")
async def delete_session(response: Response) -> dict[str, str]:
    """Clear the session cookie (logout)."""
    response.delete_cookie(key=COOKIE_NAME)
    return {"status": "logged_out"}


def get_token_from_cookie(request: Request) -> str | None:
    """Extract the Firebase ID token from the session cookie.

    Returns None if no cookie is present.
    """
    return request.cookies.get(COOKIE_NAME)