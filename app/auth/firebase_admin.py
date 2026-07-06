"""Firebase Admin SDK initialization and token verification."""

import json
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials, auth as firebase_auth

from app.config import settings

_initialized = False


def init_firebase_admin() -> None:
    """Initialize Firebase Admin SDK if not already initialized.

    Uses the service account key JSON file specified in settings.
    Falls back to Application Default Credentials if the file is not found
    (useful for cloud deployments).
    """
    global _initialized
    if _initialized:
        return

    key_path = Path(settings.firebase_service_account_key_path)
    if key_path.exists():
        cred = credentials.Certificate(str(key_path))
        firebase_admin.initialize_app(cred)
    else:
        # Use Application Default Credentials (works on GCP, or via `gcloud auth application-default login`)
        firebase_admin.initialize_app()

    _initialized = True


def verify_token(id_token: str) -> dict[str, Any]:
    """Verify a Firebase ID token and return the decoded claims.

    Args:
        id_token: The Firebase ID token from the client (Authorization: Bearer <token>).

    Returns:
        Decoded token claims dict (includes uid, email, name, etc.).

    Raises:
        ValueError: If the token is invalid, expired, or revoked.
    """
    init_firebase_admin()
    decoded = firebase_auth.verify_id_token(id_token, check_revoked=True)
    return decoded


def get_user_by_uid(uid: str) -> firebase_auth.UserRecord | None:
    """Fetch a Firebase user by UID.

    Args:
        uid: The Firebase user UID.

    Returns:
        UserRecord if found, None otherwise.
    """
    init_firebase_admin()
    try:
        return firebase_auth.get_user(uid)
    except firebase_auth.UserNotFoundError:
        return None