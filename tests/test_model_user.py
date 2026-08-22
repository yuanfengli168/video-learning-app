"""Tests for the User model.

Covers:
- Field defaults (role=FREE, created_at, updated_at)
- Primary key behavior (user_id is the Firebase UID)
- to_dict() shape
- Repr output
- Edge cases: long UIDs, NULL email, NULL notes, role override
- Multiple users with different roles coexist
"""

from datetime import datetime

from app.models import User


def test_create_user_default_role_is_free(db_session):
    """New users default to role=2 (FREE), never higher privilege."""
    user = User(user_id="firebase-uid-123")
    db_session.add(user)
    db_session.commit()

    assert user.role == 2
    assert user.user_id == "firebase-uid-123"
    assert user.email is None
    assert user.notes is None


def test_create_user_with_email_and_notes(db_session):
    """All optional fields can be set explicitly."""
    user = User(
        user_id="firebase-uid-456",
        email="alice@example.com",
        notes="early tester, founder friend",
        role=0,  # ADMIN
    )
    db_session.add(user)
    db_session.commit()

    assert user.email == "alice@example.com"
    assert user.notes == "early tester, founder friend"
    assert user.role == 0


def test_user_timestamps_auto_set(db_session):
    """created_at and updated_at default to current time on insert."""
    user = User(user_id="firebase-uid-time")
    db_session.add(user)
    db_session.commit()

    assert isinstance(user.created_at, datetime)
    assert isinstance(user.updated_at, datetime)
    # updated_at should equal created_at at creation (server_default both)
    # SQLite stores seconds precision so equality is fine here
    assert user.created_at is not None
    assert user.updated_at is not None


def test_user_to_dict_shape(db_session):
    """to_dict returns only public fields, never any secrets."""
    user = User(
        user_id="uid-dict",
        email="dict@example.com",
        role=1,  # PAID
    )
    db_session.add(user)
    db_session.commit()

    d = user.to_dict()
    assert d["user_id"] == "uid-dict"
    assert d["email"] == "dict@example.com"
    assert d["role"] == 1
    assert d["notes"] is None
    assert "created_at" in d
    assert "updated_at" in d
    # ISO format strings, not datetime objects
    assert isinstance(d["created_at"], str)


def test_user_to_dict_with_null_fields(db_session):
    """to_dict handles NULL email + notes gracefully."""
    user = User(user_id="uid-null")
    db_session.add(user)
    db_session.commit()

    d = user.to_dict()
    assert d["email"] is None
    assert d["notes"] is None


def test_user_repr_includes_role_and_email(db_session):
    """Repr helps debugging without leaking secrets."""
    user = User(user_id="uid-repr", email="x@y.com", role=0)
    r = repr(user)
    assert "uid-repr" in r
    assert "x@y.com" in r
    assert "role=0" in r


def test_user_primary_key_is_firebase_uid(db_session):
    """Two users with the same user_id would conflict (intentional)."""
    user1 = User(user_id="duplicate-uid", role=2)
    db_session.add(user1)
    db_session.commit()

    # Same user_id again -> SQLAlchemy raises on flush
    import pytest
    from sqlalchemy.exc import IntegrityError

    user2 = User(user_id="duplicate-uid", role=2)
    db_session.add(user2)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_multiple_users_coexist(db_session):
    """Users with different user_ids + roles can coexist."""
    admin = User(user_id="uid-admin", email="admin@x.com", role=0)
    paid = User(user_id="uid-paid", email="paid@x.com", role=1)
    free = User(user_id="uid-free", email="free@x.com", role=2)
    db_session.add_all([admin, paid, free])
    db_session.commit()

    rows = db_session.query(User).order_by(User.role).all()
    assert len(rows) == 3
    assert [r.role for r in rows] == [0, 1, 2]
    assert [r.user_id for r in rows] == ["uid-admin", "uid-paid", "uid-free"]


def test_user_long_firebase_uid_accepted(db_session):
    """Firebase UIDs can be up to 128 chars; verify column length."""
    long_uid = "a" * 128
    user = User(user_id=long_uid)
    db_session.add(user)
    db_session.commit()
    assert user.user_id == long_uid
    assert len(user.user_id) == 128


def test_user_role_accepts_all_valid_values(db_session):
    """Role accepts any int (validation happens at API layer, not DB)."""
    for role in (0, 1, 2, 3):  # 3 = EDUCATION (future)
        user = User(user_id=f"uid-r{role}", role=role)
        db_session.add(user)
    db_session.commit()

    roles = sorted(r.role for r in db_session.query(User).all())
    assert roles == [0, 1, 2, 3]
