"""Tests for the PaidWaitlist model.

Covers:
- Field defaults (source='web', notified_at=None, created_at auto)
- UUID primary key auto-generated
- Email UNIQUE constraint (idempotent signups)
- to_dict() shape and NULL handling
- Repr output
- Optional fields (message, notified_at) work when None
"""

from datetime import datetime

from app.models import PaidWaitlist


def test_create_waitlist_entry_minimal(db_session):
    """Only email required; everything else defaults."""
    entry = PaidWaitlist(email="alice@example.com")
    db_session.add(entry)
    db_session.commit()

    assert entry.id is not None
    assert len(entry.id) == 36  # UUID4 string
    assert entry.email == "alice@example.com"
    assert entry.message is None
    assert entry.source == "web"  # default
    assert entry.notified_at is None
    assert isinstance(entry.created_at, datetime)


def test_create_waitlist_entry_with_all_fields(db_session):
    """All fields settable."""
    entry = PaidWaitlist(
        email="bob@example.com",
        message="I want unlimited chats for studying CS",
        source="friend-referral",
    )
    db_session.add(entry)
    db_session.commit()

    assert entry.message == "I want unlimited chats for studying CS"
    assert entry.source == "friend-referral"


def test_email_must_be_unique(db_session):
    """Duplicate emails raise IntegrityError (UNIQUE constraint)."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    entry1 = PaidWaitlist(email="dup@example.com")
    db_session.add(entry1)
    db_session.commit()

    entry2 = PaidWaitlist(email="dup@example.com")
    db_session.add(entry2)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_to_dict_full(db_session):
    """to_dict returns all public fields as ISO strings / None."""
    entry = PaidWaitlist(
        email="dict@example.com",
        message="For my thesis research",
        source="web",
    )
    db_session.add(entry)
    db_session.commit()

    d = entry.to_dict()
    assert d["id"] == entry.id
    assert d["email"] == "dict@example.com"
    assert d["message"] == "For my thesis research"
    assert d["source"] == "web"
    assert d["notified_at"] is None
    assert isinstance(d["created_at"], str)
    # ISO 8601 format includes 'T' separator
    assert "T" in d["created_at"]


def test_to_dict_with_notified_at(db_session):
    """to_dict serializes notified_at as ISO string when set."""
    from datetime import datetime, timezone

    notified = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    entry = PaidWaitlist(
        email="notified@example.com",
        source="manual",
        notified_at=notified,
    )
    db_session.add(entry)
    db_session.commit()

    d = entry.to_dict()
    assert d["notified_at"] is not None
    assert "2026" in d["notified_at"]


def test_repr_includes_email_and_source(db_session):
    """Repr is debug-friendly without exposing internal id."""
    entry = PaidWaitlist(email="repr@example.com", source="web")
    r = repr(entry)
    assert "repr@example.com" in r
    assert "web" in r


def test_email_max_length_accepted(db_session):
    """RFC 5321 max email length is 254 chars."""
    long_email = "a" * 240 + "@x.co"  # 245 chars total, well under 254
    entry = PaidWaitlist(email=long_email)
    db_session.add(entry)
    db_session.commit()
    assert entry.email == long_email


def test_multiple_entries_coexist(db_session):
    """Different emails = multiple rows."""
    entries = [
        PaidWaitlist(email=f"u{i}@example.com") for i in range(5)
    ]
    db_session.add_all(entries)
    db_session.commit()

    assert db_session.query(PaidWaitlist).count() == 5


def test_default_source_is_web(db_session):
    """Most common case (web signup) doesn't need explicit source."""
    entry = PaidWaitlist(email="default-src@example.com")
    db_session.add(entry)
    db_session.commit()
    assert entry.source == "web"


def test_message_can_be_long_text(db_session):
    """Message column is Text (no length limit in DB)."""
    long_msg = "x" * 10_000
    entry = PaidWaitlist(email="long-msg@example.com", message=long_msg)
    db_session.add(entry)
    db_session.commit()
    assert entry.message == long_msg
    assert len(entry.message) == 10_000
