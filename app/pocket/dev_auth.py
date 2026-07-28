"""Dev-only auth bypass for the pocket sub-app.

When `POCKET_DEV_AUTH=1` is set in the environment, requests can authenticate
by sending an `X-Dev-User-Id` header. This is gated by an env var so it can
never be enabled in production by accident.

Use cases:
- iOS Simulator dev: send `X-Dev-User-Id: <firebase-uid>` to view real data
- E2E tests: send the same header instead of mocking Firebase
- Demo / showcase: same

NEVER set `POCKET_DEV_AUTH=1` in production. Never commit the flag.
"""

import logging
import os
from typing import Any

from fastapi import Header, HTTPException, Request, status

log = logging.getLogger(__name__)


# Read once at import time. Changing the env var requires a restart.
DEV_AUTH_ENABLED = os.environ.get("POCKET_DEV_AUTH") == "1"


async def get_current_user_dev_or_real(
    request: Request,
    x_dev_user_id: str | None = Header(default=None, alias="X-Dev-User-Id"),
) -> dict[str, Any]:
    """Auth dependency used by /m/* endpoints.

    v0.1.3-real-teaching v0.2: real Firebase auth support.
    Resolution order:
      1. If `POCKET_DEV_AUTH=1` AND `X-Dev-User-Id` header is present:
         trust the header (dev offline UI development).
      2. Otherwise: extract Bearer token from Authorization header
         and verify it via Firebase Admin SDK.
      3. If both fail: 401.
    """
    # 1. Dev path
    if DEV_AUTH_ENABLED and x_dev_user_id:
        return {"uid": x_dev_user_id, "email": f"{x_dev_user_id}@dev.local", "dev": True}

    # 2. Real Firebase path — extract Bearer token from Authorization
    # header. We don't use the web app's get_current_user directly
    # because that function is a FastAPI dep that takes `Depends(security)`
    # for credentials; calling it with positional kwargs bypasses FastAPI's
    # dep injection. Instead we extract + verify inline using the same
    # helpers the web app uses.
    auth_header = request.headers.get("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Not authenticated. Send Authorization: Bearer <firebase_id_token>"
                + (" (or X-Dev-User-Id header with POCKET_DEV_AUTH=1 server-side)"
                   if DEV_AUTH_ENABLED else "")
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header[len("Bearer "):].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        # Lazy import — firebase_admin module may not be loaded in unit tests.
        from app.auth.firebase_admin import verify_token as _verify_token
        claims = _verify_token(token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return claims
