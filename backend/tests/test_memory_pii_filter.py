"""Tests for the PII guard on the long-term memory write path.

golden rule / privacy: the memory extractor prompt ASKS Gemini not to store
SSNs, phone numbers, salaries, etc. — but that's advisory. `_merge_memories`
is the deterministic gate that must DROP any extracted fact containing PII
before it is persisted to UserMemory, so a model slip (or a fact the user
volunteered) never lands in the database.

Run from the backend/ directory:
    cd backend && ../.venv/bin/python -m pytest tests/test_memory_pii_filter.py -v
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import User, UserMemory
from services.memory_service import _merge_memories


@pytest.fixture
def db():
    """Fresh SQLite in-memory DB per test with one seeded user."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    user = User(email="pi@morgan.edu", password_hash="x", role="user")
    session.add(user)
    session.commit()
    session.user_id = user.id
    try:
        yield session
    finally:
        session.close()


def _rows(db, user_id):
    return db.query(UserMemory).filter(UserMemory.user_id == user_id).all()


# ---------------------------------------------------------------------------
# Control: clean facts still get stored (the filter must not over-block)
# ---------------------------------------------------------------------------

def test_merge_keeps_clean_memory(db):
    """A normal research-admin fact is persisted unchanged."""
    _merge_memories(
        db, db.user_id,
        [{"memory_type": "role", "content": "PI on an NSF grant in microbiology"}],
        existing=[],
    )
    db.commit()
    rows = _rows(db, db.user_id)
    assert len(rows) == 1
    assert rows[0].content == "PI on an NSF grant in microbiology"


def test_merge_keeps_short_non_pii_context(db):
    """Short but clean facts (e.g. "New faculty") must NOT be dropped — the
    guard is PII-only, not a junk/greeting filter."""
    _merge_memories(
        db, db.user_id,
        [{"memory_type": "context", "content": "New faculty"}],
        existing=[],
    )
    db.commit()
    assert len(_rows(db, db.user_id)) == 1


# ---------------------------------------------------------------------------
# PII facts must be dropped
# ---------------------------------------------------------------------------

def test_merge_drops_ssn(db):
    _merge_memories(
        db, db.user_id,
        [{"memory_type": "context", "content": "Their SSN is 123-45-6789"}],
        existing=[],
    )
    db.commit()
    assert _rows(db, db.user_id) == []


def test_merge_drops_phone_number(db):
    _merge_memories(
        db, db.user_id,
        [{"memory_type": "context", "content": "Reachable at 410-555-1234"}],
        existing=[],
    )
    db.commit()
    assert _rows(db, db.user_id) == []


def test_merge_drops_email(db):
    _merge_memories(
        db, db.user_id,
        [{"memory_type": "context", "content": "Contact is jane.doe@morgan.edu"}],
        existing=[],
    )
    db.commit()
    assert _rows(db, db.user_id) == []


def test_merge_drops_salary_dollar_amount(db):
    _merge_memories(
        db, db.user_id,
        [{"memory_type": "context", "content": "Annual salary is $120,000"}],
        existing=[],
    )
    db.commit()
    assert _rows(db, db.user_id) == []


def test_merge_drops_personal_phrase(db):
    _merge_memories(
        db, db.user_id,
        [{"memory_type": "context", "content": "my bank account is with Chase"}],
        existing=[],
    )
    db.commit()
    assert _rows(db, db.user_id) == []


def test_merge_drops_only_the_pii_fact(db):
    """A PII fact is dropped while a clean fact in the same batch survives."""
    _merge_memories(
        db, db.user_id,
        [
            {"memory_type": "context", "content": "SSN 123-45-6789 on file"},
            {"memory_type": "sponsor", "content": "Primarily works with NIH and NSF"},
        ],
        existing=[],
    )
    db.commit()
    rows = _rows(db, db.user_id)
    assert len(rows) == 1
    assert rows[0].content == "Primarily works with NIH and NSF"
