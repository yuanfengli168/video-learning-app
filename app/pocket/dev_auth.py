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

from fastapi import Header, HTTPException, status

log = logging.getLogger(__name__)


# Read once at import time. Changing the env var requires a restart.
DEV_AUTH_ENABLED = os.environ.get("POCKET_DEV_AUTH") == "1"


async def get_current_user_dev_or_real(
    x_dev_user_id: str | None = Header(default=None, alias="X-Dev-User-Id"),
) -> dict[str, Any]:
    """Auth dependency used by /m/* endpoints.

    - If `POCKET_DEV_AUTH=1` AND `X-Dev-User-Id` is present: trust the header.
    - Otherwise: 401. (We deliberately do NOT also call real Firebase auth
      here, because the pocket sub-app's auth model in v0.1 is "share the
      session cookie with the desktop app" — which the iOS app doesn't have
      yet. v0.2 will switch to real Firebase auth.)
    """
    if DEV_AUTH_ENABLED and x_dev_user_id:
        return {"uid": x_dev_user_id, "email": f"{x_dev_user_id}@dev.local", "dev": True}

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated. (pocket v0.1: send X-Dev-User-Id header with POCKET_DEV_AUTH=1 server-side)",
        headers={"WWW-Authenticate": "Bearer"},
    )
