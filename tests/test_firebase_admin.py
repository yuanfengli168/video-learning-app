"""Tests for Firebase Admin SDK initialization and token verification."""

from unittest.mock import MagicMock, patch

import pytest

from app.auth.firebase_admin import (
    get_user_by_uid,
    init_firebase_admin,
    verify_token,
)


@pytest.fixture
def reset_firebase():
    """Reset Firebase Admin initialization state before each test."""
    import app.auth.firebase_admin as fb_module
    original = fb_module._initialized
    fb_module._initialized = False
    yield
    fb_module._initialized = original


def test_init_firebase_admin_with_key_file(reset_firebase, tmp_path, monkeypatch):
    """init_firebase_admin should use service account file if it exists."""
    key_file = tmp_path / "service-account.json"
    key_file.write_text('{"type": "service_account"}')

    monkeypatch.setattr(
        "app.auth.firebase_admin.settings",
        MagicMock(firebase_service_account_key_path=str(key_file)),
    )

    with patch("app.auth.firebase_admin.credentials") as mock_cred:
        with patch("app.auth.firebase_admin.firebase_admin.initialize_app") as mock_init:
            init_firebase_admin()
            mock_cred.Certificate.assert_called_once_with(str(key_file))
            mock_init.assert_called_once()

    import app.auth.firebase_admin as fb_module
    assert fb_module._initialized is True


def test_init_firebase_admin_default_credentials(reset_firebase, tmp_path, monkeypatch):
    """init_firebase_admin should use default credentials if key file doesn't exist."""
    monkeypatch.setattr(
        "app.auth.firebase_admin.settings",
        MagicMock(firebase_service_account_key_path=str(tmp_path / "nonexistent.json")),
    )

    with patch("app.auth.firebase_admin.firebase_admin.initialize_app") as mock_init:
        init_firebase_admin()
        mock_init.assert_called_once()

    import app.auth.firebase_admin as fb_module
    assert fb_module._initialized is True


def test_init_firebase_admin_idempotent(reset_firebase):
    """init_firebase_admin should not re-initialize if already initialized."""
    import app.auth.firebase_admin as fb_module
    fb_module._initialized = True

    with patch("app.auth.firebase_admin.firebase_admin.initialize_app") as mock_init:
        init_firebase_admin()
        mock_init.assert_not_called()


def test_verify_token_success(reset_firebase):
    """verify_token should return decoded claims on success."""
    fake_claims = {"uid": "test-uid", "email": "test@example.com", "name": "Test"}

    with patch("app.auth.firebase_admin.init_firebase_admin"):
        with patch(
            "app.auth.firebase_admin.firebase_auth.verify_id_token",
            return_value=fake_claims,
        ):
            result = verify_token("fake-token")
            assert result == fake_claims
            assert result["uid"] == "test-uid"


def test_verify_token_invalid(reset_firebase):
    """verify_token should raise ValueError for invalid tokens."""
    with patch("app.auth.firebase_admin.init_firebase_admin"):
        with patch(
            "app.auth.firebase_admin.firebase_auth.verify_id_token",
            side_effect=ValueError("Invalid token"),
        ):
            with pytest.raises(ValueError, match="Invalid token"):
                verify_token("bad-token")


def test_get_user_by_uid_found(reset_firebase):
    """get_user_by_uid should return UserRecord if user exists."""
    fake_user = MagicMock()
    fake_user.uid = "test-uid"

    with patch("app.auth.firebase_admin.init_firebase_admin"):
        with patch(
            "app.auth.firebase_admin.firebase_auth.get_user",
            return_value=fake_user,
        ):
            result = get_user_by_uid("test-uid")
            assert result is not None
            assert result.uid == "test-uid"


def test_get_user_by_uid_not_found(reset_firebase):
    """get_user_by_uid should return None if user doesn't exist."""
    from firebase_admin import auth as firebase_auth

    with patch("app.auth.firebase_admin.init_firebase_admin"):
        with patch(
            "app.auth.firebase_admin.firebase_auth.get_user",
            side_effect=firebase_auth.UserNotFoundError("not found"),
        ):
            result = get_user_by_uid("nonexistent")
            assert result is None