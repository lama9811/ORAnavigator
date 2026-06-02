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
    assert status == {
        "department": "created",
        "role": "created",
        "interests": "noop",  # interests not provided -> untouched
    }
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
    """If no fields are given, no memory rows touched."""
    status = mirror_profile_to_memories(db, user_id=db.user_id)
    db.commit()
    assert status == {
        "department": "noop",
        "role": "noop",
        "interests": "noop",
    }
    assert _memory_rows(db, db.user_id, "department") == []
    assert _memory_rows(db, db.user_id, "role") == []
    assert _memory_rows(db, db.user_id, "interest") == []


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


# =====================================================================
# Interests -- multi-value, replace-all semantics
# =====================================================================

def test_interests_creates_one_row_per_token(db):
    """A comma-separated interests string becomes one UserMemory(memory_type=
    'interest') row per non-empty token."""
    status = mirror_profile_to_memories(
        db, user_id=db.user_id,
        interests="cybersecurity, machine learning, HCI",
    )
    db.commit()
    assert status["interests"] == "replaced:3"
    rows = _memory_rows(db, db.user_id, "interest")
    contents = sorted(r.content for r in rows)
    assert contents == ["HCI", "cybersecurity", "machine learning"]


def test_interests_strips_whitespace_and_drops_empties(db):
    """Extra commas, leading/trailing whitespace, and double-commas don't
    create blank interest rows."""
    mirror_profile_to_memories(
        db, user_id=db.user_id,
        interests="  cybersecurity ,, , machine learning,  ",
    )
    db.commit()
    rows = _memory_rows(db, db.user_id, "interest")
    contents = sorted(r.content for r in rows)
    assert contents == ["cybersecurity", "machine learning"]


def test_interests_dedupes_case_insensitively(db):
    """If a user types the same interest twice (different casing), only one
    row is created. Preserves the first occurrence's casing."""
    mirror_profile_to_memories(
        db, user_id=db.user_id,
        interests="Cybersecurity, cybersecurity, CYBERSECURITY",
    )
    db.commit()
    rows = _memory_rows(db, db.user_id, "interest")
    assert len(rows) == 1
    assert rows[0].content == "Cybersecurity"


def test_interests_replace_all_overwrites_old_list(db):
    """The replace-all semantic: a second save with a different list deletes
    the old rows and writes the new list. Old interests don't linger."""
    mirror_profile_to_memories(db, user_id=db.user_id,
                               interests="cybersecurity, ML")
    db.commit()
    assert len(_memory_rows(db, db.user_id, "interest")) == 2

    # New save: completely different list.
    status = mirror_profile_to_memories(db, user_id=db.user_id,
                                        interests="biology, genetics")
    db.commit()
    assert status["interests"] == "replaced:2"
    rows = _memory_rows(db, db.user_id, "interest")
    contents = sorted(r.content for r in rows)
    assert contents == ["biology", "genetics"]


def test_interests_empty_string_clears_all_rows(db):
    """Saving an empty string explicitly clears all interest rows. (User
    wiped the field on the form.)"""
    mirror_profile_to_memories(db, user_id=db.user_id,
                               interests="cybersecurity, ML, HCI")
    db.commit()
    assert len(_memory_rows(db, db.user_id, "interest")) == 3

    status = mirror_profile_to_memories(db, user_id=db.user_id, interests="")
    db.commit()
    assert status["interests"] == "cleared:3"
    assert _memory_rows(db, db.user_id, "interest") == []


def test_interests_none_is_noop_and_preserves_rows(db):
    """interests=None means the form didn't send the field -- existing rows
    stay untouched. Distinguishes 'unchanged' from 'cleared'."""
    db.add(UserMemory(user_id=db.user_id, memory_type="interest",
                      content="cybersecurity"))
    db.commit()

    status = mirror_profile_to_memories(db, user_id=db.user_id)
    db.commit()
    assert status["interests"] == "noop"
    rows = _memory_rows(db, db.user_id, "interest")
    assert len(rows) == 1
    assert rows[0].content == "cybersecurity"


def test_interests_only_touches_its_own_user(db):
    """Replace-all must NOT delete other users' interest rows."""
    user_b = User(email="b@morgan.edu", password_hash="x", role="user")
    db.add(user_b)
    db.commit()
    db.add(UserMemory(user_id=user_b.id, memory_type="interest",
                      content="genomics"))
    db.commit()

    mirror_profile_to_memories(db, user_id=db.user_id,
                               interests="cybersecurity")
    db.commit()

    a_rows = _memory_rows(db, db.user_id, "interest")
    b_rows = _memory_rows(db, user_b.id, "interest")
    assert [r.content for r in a_rows] == ["cybersecurity"]
    assert [r.content for r in b_rows] == ["genomics"]
