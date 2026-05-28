"""Tests for the research-admin profile fields (department / title / primary_role).

Two layers covered:
1. ProfileUpdateRequest validation -- the primary_role enum check rejects
   bad values with 422 before they touch the DB.
2. mirror_profile_to_memories() -- the helper that writes the structured
   profile values into user_memories rows so build_memory_context() and
   Sponsor Fit Finder pick them up unchanged.

Run from the backend/ directory:
    cd backend && ../.venv/bin/python -m pytest tests/test_profile_fields.py -v
"""
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from deps import PROFILE_ROLE_ENUM, ProfileUpdateRequest
from models import User, UserMemory
from services.memory_service import mirror_profile_to_memories


# =====================================================================
# ProfileUpdateRequest validation
# =====================================================================

def test_request_accepts_no_fields():
    """An empty PATCH-style update is valid (nothing to change)."""
    req = ProfileUpdateRequest()
    assert req.name is None
    assert req.department is None
    assert req.title is None
    assert req.primary_role is None


def test_request_accepts_all_fields():
    """All four fields are optional and accepted when provided."""
    req = ProfileUpdateRequest(
        name="Jane Doe",
        department="Biology",
        title="Associate Professor",
        primary_role="PI",
    )
    assert req.name == "Jane Doe"
    assert req.department == "Biology"
    assert req.title == "Associate Professor"
    assert req.primary_role == "PI"


def test_request_accepts_every_role_in_enum():
    """Every value declared in PROFILE_ROLE_ENUM must validate."""
    for role in PROFILE_ROLE_ENUM:
        req = ProfileUpdateRequest(primary_role=role)
        assert req.primary_role == role


def test_request_rejects_unknown_role():
    """primary_role outside the enum is a 422 -- never reaches the DB."""
    with pytest.raises(ValidationError):
        ProfileUpdateRequest(primary_role="Wizard")


def test_request_rejects_misspelled_role():
    """Case-sensitivity guard: 'pi' lowercase is NOT valid -- only 'PI' is."""
    with pytest.raises(ValidationError):
        ProfileUpdateRequest(primary_role="pi")


def test_request_treats_empty_string_role_as_none():
    """Empty-string clears the field -- normalized to None for downstream code."""
    req = ProfileUpdateRequest(primary_role="")
    assert req.primary_role is None


# =====================================================================
# mirror_profile_to_memories -- shared SQLite fixture
# =====================================================================

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


def _memory_rows(db, user_id, memory_type):
    return (
        db.query(UserMemory)
        .filter(UserMemory.user_id == user_id, UserMemory.memory_type == memory_type)
        .all()
    )


# =====================================================================
# Create case
# =====================================================================

def test_mirror_creates_department_and_role_rows(db):
    """First save with department + primary_role creates two new memory rows."""
    status = mirror_profile_to_memories(
        db, user_id=db.user_id, department="Biology", primary_role="PI"
    )
    db.commit()
    assert status == {"department": "created", "role": "created"}
    dept = _memory_rows(db, db.user_id, "department")
    role = _memory_rows(db, db.user_id, "role")
    assert len(dept) == 1 and dept[0].content == "Biology"
    assert len(role) == 1 and role[0].content == "PI"


def test_mirror_strips_whitespace(db):
    """Surrounding whitespace gets trimmed before storage."""
    mirror_profile_to_memories(
        db, user_id=db.user_id, department="  Computer Science  ", primary_role=" Co-PI "
    )
    db.commit()
    dept = _memory_rows(db, db.user_id, "department")
    role = _memory_rows(db, db.user_id, "role")
    assert dept[0].content == "Computer Science"
    assert role[0].content == "Co-PI"


def test_mirror_no_fields_provided_does_nothing(db):
    """If neither department nor primary_role is given, no memory rows touched."""
    status = mirror_profile_to_memories(db, user_id=db.user_id)
    db.commit()
    assert status == {"department": "noop", "role": "noop"}
    assert _memory_rows(db, db.user_id, "department") == []
    assert _memory_rows(db, db.user_id, "role") == []


# =====================================================================
# Update case (profile WINS over auto-extracted memory)
# =====================================================================

def test_mirror_overwrites_existing_department(db):
    """A second save with a new department value overwrites the existing row
    (NOT a second row). This is the 'profile wins' behavior."""
    # Pre-existing row, as if extracted from chat history yesterday.
    db.add(UserMemory(
        user_id=db.user_id,
        memory_type="department",
        content="Computer Science",
    ))
    db.commit()

    status = mirror_profile_to_memories(
        db, user_id=db.user_id, department="Biology"
    )
    db.commit()
    assert status["department"] == "updated"
    rows = _memory_rows(db, db.user_id, "department")
    assert len(rows) == 1, "should still be exactly one department memory row"
    assert rows[0].content == "Biology"


def test_mirror_overwrite_clears_stale_embedding(db):
    """When overwriting, any embedding on the existing row is cleared so the
    next consolidation re-embeds against the new content -- otherwise stale
    embeddings would mis-rank during semantic recall."""
    db.add(UserMemory(
        user_id=db.user_id,
        memory_type="role",
        content="Research Staff",
        embedding="[0.1, 0.2, 0.3]",
        embedding_model="text-embedding-004@256",
    ))
    db.commit()

    mirror_profile_to_memories(db, user_id=db.user_id, primary_role="PI")
    db.commit()

    row = _memory_rows(db, db.user_id, "role")[0]
    assert row.content == "PI"
    assert row.embedding is None
    assert row.embedding_model is None


# =====================================================================
# Delete case (user explicitly cleared a field on the form)
# =====================================================================

def test_mirror_empty_string_deletes_existing_row(db):
    """An empty-string department value means the user cleared the field --
    delete any existing memory row of that type."""
    db.add(UserMemory(
        user_id=db.user_id,
        memory_type="department",
        content="Biology",
    ))
    db.commit()

    status = mirror_profile_to_memories(
        db, user_id=db.user_id, department=""
    )
    db.commit()
    assert status["department"] == "deleted"
    assert _memory_rows(db, db.user_id, "department") == []


def test_mirror_empty_string_with_no_existing_row_is_noop(db):
    """Clearing a field that was never set is a safe no-op (no errors)."""
    status = mirror_profile_to_memories(db, user_id=db.user_id, department="")
    db.commit()
    assert status["department"] == "noop"


# =====================================================================
# Isolation (one user's mirror cannot touch another user's memories)
# =====================================================================

def test_mirror_only_touches_its_own_user(db):
    """Saving user A's profile must not affect user B's department memory."""
    user_b = User(email="b@morgan.edu", password_hash="x", role="user")
    db.add(user_b)
    db.commit()

    db.add(UserMemory(
        user_id=user_b.id,
        memory_type="department",
        content="Engineering",
    ))
    db.commit()

    mirror_profile_to_memories(
        db, user_id=db.user_id, department="Biology"
    )
    db.commit()

    # User A got their new row, user B's untouched.
    a_rows = _memory_rows(db, db.user_id, "department")
    b_rows = _memory_rows(db, user_b.id, "department")
    assert a_rows[0].content == "Biology"
    assert b_rows[0].content == "Engineering"


# =====================================================================
# Title is intentionally NOT mirrored
# =====================================================================

def test_mirror_does_not_create_title_memory(db):
    """Title has no matching UserMemory.memory_type, so it must not be
    written through the mirror (caller still saves it on the User row)."""
    mirror_profile_to_memories(db, user_id=db.user_id, department="Biology")
    db.commit()
    # Ensure no spurious 'title' memory row was created.
    titles = _memory_rows(db, db.user_id, "title")
    assert titles == []
